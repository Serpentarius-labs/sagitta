# sagitta/File/response.py
import os
import time
import logging
from typing import Literal, Optional
from urllib.parse import quote
from collections import deque
from fastapi import HTTPException
from starlette.responses import StreamingResponse, JSONResponse
from ..Config.setting import get_settings

logger = logging.getLogger(__name__)


def _backend_ready(cfg) -> bool:
    """对象存储后端是否配置完整（endpoint + bucket + 密钥）。"""
    return bool(getattr(cfg, "endpoint", "") and getattr(cfg, "bucket", "")
                and getattr(cfg, "access_key", "") and getattr(cfg, "secret_key", ""))


def _concretize_auto(oss_key: Optional[str], local_path: Optional[str], cfg) -> str:
    """把 auto 解析成具体策略：

    * oss_key 且后端配置完整 → oss（生产首选，零服务器带宽）
    * 有本地文件             → python（本地开发兜底）
    * 其余                   → 按入参形态就近选择
    """
    if oss_key and _backend_ready(cfg):
        return "oss"
    if local_path:
        return "python"
    return "oss" if oss_key else "python"


# ---------- 共享 aiobotocore client ----------
# aiobotocore **首次** create_client 有约 2.5s 的模型/handler 加载成本
# （同进程第二次创建仅 0.01s）。此前每次下载在 head 与 stream 两处各建一个
# client → 小文件下载也要 5s。这里按后端配置缓存共享 client，aiohttp
# 连接池天然支持并发复用；进程关闭时由 close_shared_oss_clients() 释放。
_oss_client_cache: dict = {}


async def _get_oss_client(cfg):
    import aiobotocore.session

    key = (cfg.endpoint, cfg.access_key, cfg.secret_key, cfg.bucket,
           getattr(cfg, "region", "us-east-1"))
    c = _oss_client_cache.get(key)
    if c is None:
        session = aiobotocore.session.get_session()
        c = await session.create_client(
            "s3",
            endpoint_url=cfg.endpoint,
            aws_access_key_id=cfg.access_key,
            aws_secret_access_key=cfg.secret_key,
            region_name=getattr(cfg, "region", "us-east-1"),
        ).__aenter__()
        _oss_client_cache[key] = c
    return c


async def close_shared_oss_clients() -> None:
    """关闭全部共享 OSS client（应用 shutdown 钩子里调用）。"""
    for c in list(_oss_client_cache.values()):
        try:
            await c.close()
        except Exception:
            logger.debug("关闭 oss client 异常", exc_info=True)
    _oss_client_cache.clear()


class SgFileResponse(StreamingResponse):
    """
    支持 OSS/本地/nginx 三种策略的文件响应。

    架构设计原则：
    1. OSS 策略使用 aiobotocore 异步客户端，避免阻塞 ASGI 事件循环
    2. 分片读取 + 自动重试，不依赖 S3 range 请求的完整性
    3. 漏桶算法限速 + 预取缓冲，输出速率平滑稳定
    4. Content-Length 由我们自己计算并保证，严格对齐
    """
    _sg_final = True
    media_type = "application/octet-stream"

    def __init__(
            self,
            *,
            oss_key: Optional[str] = None,
            local_path: Optional[str] = None,
            filename: Optional[str] = None,
            content_type: Optional[str] = None,
            strategy: Literal["auto", "oss", "nginx", "python"] = "auto",
            inline: bool = False,
            request=None,
    ):
        self._range_header = request.headers.get("range") if request else None
        self.filename = filename or os.path.basename(oss_key or local_path or "file")
        self.content_type = content_type or "application/octet-stream"
        self.inline = inline

        cfg = get_settings().s3

        if strategy != "auto":
            eff = strategy
        elif hasattr(cfg, "default_strategy") and cfg.default_strategy not in ("", "auto"):
            # 配置里显式写死了具体策略（oss/nginx/python）
            eff = cfg.default_strategy
        else:
            # auto 必须在这里**落地成具体策略**，不能把 "auto" 带进 __call__
            # （否则它会掉进 else 的 python 分支，oss 下载链路永远不可达）。
            eff = _concretize_auto(oss_key, local_path, cfg)

        super().__init__(content=iter([]))

        self._strategy = eff
        self._oss_key = oss_key
        self._local_path = local_path
        self._cfg = cfg

    async def _stream_oss_range(self, start: int, end: int, file_size: int,
                               s3_client):
        """
        异步流式读取 S3 指定范围的数据，带漏桶限速 + 预取缓冲。

        关键设计：
        - 使用共享 aiobotocore 异步客户端（调用方传入），不阻塞事件循环，
          也不在每次下载时重复承担 client 创建成本
        - 分片读取（每片 1MB），每片独立重试
        - 漏桶算法：以固定速率补充令牌，数据发送必须消耗令牌，桶空则等待
        - 预取缓冲：后台协程预取 3 个 chunk 放入队列，主循环按速率消费
        - 精确校验每片字节数，确保 Content-Length 与实际发送一致
        """
        import asyncio
        from botocore.exceptions import ClientError

        RATE_LIMIT = 5 * 1024 * 1024  # 5 MB/s，可根据需要调整
        CHUNK_SIZE = 1024 * 1024  # 1MB per chunk
        MAX_RETRIES = 3
        RETRY_DELAY = 1.0  # seconds

        # ========== 漏桶算法 ==========
        bucket_capacity = RATE_LIMIT * 2  # 桶容量 = 2秒速率，允许短暂突发
        tokens = bucket_capacity  # 初始满桶
        last_refill = time.monotonic()

        async def consume_tokens(nbytes: int):
            nonlocal tokens, last_refill
            now = time.monotonic()
            # 补充令牌（按时间流逝速率补充）
            elapsed = now - last_refill
            tokens = min(bucket_capacity, tokens + elapsed * RATE_LIMIT)
            last_refill = now
            # 消费令牌
            if tokens < nbytes:
                # 令牌不足，计算需要等待的时间
                wait_time = (nbytes - tokens) / RATE_LIMIT
                await asyncio.sleep(wait_time)
                # 等待后重新补充令牌
                tokens = min(bucket_capacity, tokens + wait_time * RATE_LIMIT)
                last_refill = time.monotonic()
            tokens -= nbytes

        # ========== 预取缓冲 ==========
        buffer = asyncio.Queue(maxsize=3)  # 预取 3 个 chunk

        async def fetch_chunks():
            """后台协程：预取 S3 数据放入缓冲队列"""
            current = start
            while current <= end:
                chunk_end = min(current + CHUNK_SIZE - 1, end)
                range_header = f"bytes={current}-{chunk_end}"
                expected_bytes = chunk_end - current + 1

                for attempt in range(MAX_RETRIES):
                    try:
                        kwargs = {
                            "Bucket": self._cfg.bucket,
                            "Key": self._oss_key,
                            "Range": range_header,
                        }
                        response = await s3_client.get_object(**kwargs)
                        stream = response["Body"]
                        data = await stream.read()
                        actual_bytes = len(data)

                        if actual_bytes != expected_bytes:
                            logger.warning(
                                f"S3 chunk size mismatch: key={self._oss_key}, "
                                f"range={range_header}, expected={expected_bytes}, "
                                f"actual={actual_bytes}, attempt={attempt + 1}"
                            )
                            if attempt < MAX_RETRIES - 1:
                                await asyncio.sleep(RETRY_DELAY * (attempt + 1))
                                continue
                            else:
                                logger.error(
                                    f"S3 chunk read failed after {MAX_RETRIES} retries: "
                                    f"key={self._oss_key}, range={range_header}"
                                )
                                raise RuntimeError(
                                    f"S3 stream incomplete: expected {expected_bytes}, "
                                    f"got {actual_bytes}"
                                )

                        await buffer.put((data, actual_bytes))
                        current = chunk_end + 1
                        break

                    except Exception as e:
                        logger.error(
                            f"S3 chunk read error: key={self._oss_key}, "
                            f"range={range_header}, attempt={attempt + 1}, error={e}"
                        )
                        if attempt < MAX_RETRIES - 1:
                            await asyncio.sleep(RETRY_DELAY * (attempt + 1))
                        else:
                            raise

            await buffer.put(None)  # 结束信号

        # 启动后台预取任务
        fetch_task = asyncio.create_task(fetch_chunks())

        try:
            while True:
                item = await buffer.get()
                if item is None:  # 结束信号
                    break
                data, nbytes = item
                # 漏桶限速
                await consume_tokens(nbytes)
                yield data
        finally:
            fetch_task.cancel()
            try:
                await fetch_task
            except asyncio.CancelledError:
                pass

        logger.info(
            f"S3 download completed: key={self._oss_key}, "
            f"range={start}-{end}"
        )

    async def _prepare_oss_async(self, s3_client):
        """用共享 client 探测对象并组装响应头，返回 (stream_gen, status, media, headers)。"""
        from botocore.exceptions import ClientError

        try:
            head = await s3_client.head_object(
                Bucket=self._cfg.bucket, Key=self._oss_key
            )
            file_size = head["ContentLength"]
            etag = head.get("ETag", "").strip('"')
        except ClientError as e:
            code = e.response["Error"]["Code"]
            self._error_response = JSONResponse(
                status_code=404,
                content={"detail": f"s3 error: {code} - {str(e)}"},
            )
            return None, None, None, None

        range_header = self._range_header
        start = 0
        end = file_size - 1
        status_code = 200

        if range_header:
            try:
                range_str = range_header.replace("bytes=", "").strip()
                if range_str.startswith("-"):
                    suffix = int(range_str[1:])
                    start = max(0, file_size - suffix)
                elif range_str.endswith("-"):
                    start = int(range_str[:-1])
                    end = file_size - 1
                else:
                    parts = range_str.split("-")
                    start = int(parts[0])
                    end = int(parts[1])
                if start < 0 or start >= file_size or end < start or end >= file_size:
                    self._error_response = JSONResponse(
                        status_code=416,
                        headers={"Content-Range": f"bytes */{file_size}"},
                        content={"detail": f"Range not satisfiable: {range_header}"},
                    )
                    return None, None, None, None
                status_code = 206
            except (ValueError, IndexError):
                pass

        content_length = end - start + 1 if status_code == 206 else file_size

        fname = self.filename or self._oss_key.split("/")[-1]
        disp = (
            f"inline; filename*=UTF-8''{quote(fname)}"
            if self.inline
            else f"attachment; filename*=UTF-8''{quote(fname)}"
        )

        headers = {
            "Content-Disposition": disp,
            "Cache-Control": "private, no-store",
            "Accept-Ranges": "bytes",
            "ETag": f'"{etag}"',
        }
        if status_code == 206:
            headers["Content-Range"] = f"bytes {start}-{end}/{file_size}"
        headers["Content-Length"] = str(content_length)

        return (
            self._stream_oss_range(start, end, file_size, s3_client),
            status_code,
            self.content_type,
            headers,
        )

    async def __call__(self, scope, receive, send):
        if hasattr(self, "_error_response"):
            return await self._error_response(scope, receive, send)

        if self._strategy == "oss":
            s3_client = await _get_oss_client(self._cfg)
            result = await self._prepare_oss_async(s3_client)
            if hasattr(self, "_error_response"):
                return await self._error_response(scope, receive, send)
            content, status_code, media_type, headers = result
            self.body_iterator = content
            self.status_code = status_code
            self.media_type = media_type
            self.init_headers(headers)
        elif self._strategy == "nginx":
            internal_prefix = getattr(self._cfg, "internal_prefix", "/files")
            root = getattr(self._cfg, "root", "/data")
            # 防御：local_path 缺失时不能直接切片（TypeError）。
            # 有本地路径 → 映射成 nginx 内部 URI；只给了 oss_key → 用 key 拼内部 URI。
            if self._local_path:
                suffix = (self._local_path[len(root):]
                          if self._local_path.startswith(root) else self._local_path)
                internal = f"{internal_prefix}{suffix}"
            elif self._oss_key:
                internal = f"{internal_prefix}/{self._oss_key.lstrip('/')}"
            else:
                err = JSONResponse(
                    status_code=400,
                    content={"detail": "nginx 策略需要 local_path 或 oss_key"},
                )
                return await err(scope, receive, send)
            disp = (
                f"inline; filename*=UTF-8''{quote(self.filename)}"
                if self.inline
                else f"attachment; filename*=UTF-8''{quote(self.filename)}"
            )
            self.init_headers({
                "X-Accel-Redirect": internal,
                "X-Accel-Buffering": "yes",
                "Accept-Ranges": "bytes",
                "Content-Type": self.content_type,
                "Content-Disposition": disp,
            })
        else:
            from starlette.responses import FileResponse
            if not self._local_path or not os.path.exists(self._local_path):
                err = JSONResponse(status_code=404, content={"detail": "文件不存在"})
                return await err(scope, receive, send)
            fr = FileResponse(
                self._local_path,
                media_type=self.content_type,
                filename=self.filename,
                content_disposition_type="inline" if self.inline else "attachment",
            )
            return await fr(scope, receive, send)

        return await StreamingResponse.__call__(self, scope, receive, send)