import base64
import hmac
import re
import time
import uuid
from typing import Optional
from urllib.parse import quote

from fastapi import HTTPException

from sagitta.File.storage import S3Backend


class FileService:
    """统一文件入口：上传预签名 / 分片上传 / 下载预签名 / 回调校验 / 服务端二次核验"""

    MAX_SINGLE_UPLOAD = 5 << 30
    CHUNK_THRESHOLD = 20 << 20
    MULTIPART_PARTS = 10
    DEFAULT_EXPIRE_UPLOAD = 900
    DEFAULT_EXPIRE_DOWNLOAD = 3600

    def __init__(self, backend: S3Backend):
        self.backend = backend

    # ---------- 上传 ----------

    def presign_upload(
        self,
        prefix: str,
        filename: str,
        size: int,
        content_type: str = "application/octet-stream",
        expire_seconds: int = DEFAULT_EXPIRE_UPLOAD,
    ) -> dict:
        if not filename:
            raise HTTPException(status_code=400, detail="filename 不能为空")
        if size < 0:
            raise HTTPException(status_code=400, detail="size 不能为负数")
        if size > self.MAX_SINGLE_UPLOAD:
            raise HTTPException(status_code=413, detail=f"文件 {size >> 20}MB 超过上限 {self.MAX_SINGLE_UPLOAD >> 20}MB")
        key = self._make_key(prefix, filename)
        if size > self.CHUNK_THRESHOLD:
            return self._presign_multipart(key, content_type)
        url = self.backend.presign_url(key, expire_seconds, method="PUT")
        return {
            "method": "PUT",
            "url": url,
            "key": key,
            "direct": True,
            "headers": {"Content-Type": content_type},
            "fields": {},
        }

    def _presign_multipart(self, key: str, content_type: str) -> dict:
        up = self.backend._client.create_multipart_upload(Bucket=self.backend.bucket, Key=key, ContentType=content_type)
        uid = up["UploadId"]
        urls = [
            self.backend._client.generate_presigned_url(
                "upload_part",
                Params={"Bucket": self.backend.bucket, "Key": key, "UploadId": uid, "PartNumber": i},
                ExpiresIn=3600,
            )
            for i in range(1, self.MULTIPART_PARTS + 1)
        ]
        return {"method": "MULTIPART", "key": key, "upload_id": uid, "parts": urls, "direct": True}

    def continue_multipart(self, key: str, upload_id: str, start_part: int, count: int = 10) -> list[str]:
        if start_part < 1 or count < 1 or count > 100:
            raise HTTPException(status_code=400, detail="part 范围无效")
        return [
            self.backend._client.generate_presigned_url(
                "upload_part",
                Params={"Bucket": self.backend.bucket, "Key": key, "UploadId": upload_id, "PartNumber": i},
                ExpiresIn=3600,
            )
            for i in range(start_part, start_part + count)
        ]

    def complete_upload(self, key: str, upload_id: str, parts: list[dict]) -> str:
        if not parts:
            raise HTTPException(status_code=400, detail="parts 不能为空")
        self.backend._client.complete_multipart_upload(
            Bucket=self.backend.bucket, Key=key, UploadId=upload_id, MultipartUpload={"Parts": parts}
        )
        return self.backend.public_url(key) or key

    def abort_upload(self, key: str, upload_id: str) -> None:
        try:
            self.backend._client.abort_multipart_upload(Bucket=self.backend.bucket, Key=key, UploadId=upload_id)
        except Exception:
            import logging
            logging.getLogger("sagitta.file").warning("abort_upload 失败: key=%s upload_id=%s", key, upload_id)

    # ---------- 下载 ----------

    def presign_download(self, key: str, filename: Optional[str] = None, expire_seconds: int = DEFAULT_EXPIRE_DOWNLOAD, inline: bool = False) -> dict:
        url = self.backend.presign_url(key, expire_seconds, method="GET")
        disp = "inline" if inline else "attachment"
        if filename:
            safe = self._sanitize_name(filename)
            disp += f'; filename="{safe}"'
            disp += f"; filename*=UTF-8''{quote(safe, safe='')}"
        return {"url": url, "content_disposition": disp, "expires_in": expire_seconds}

    # ---------- 服务端二次核验 ----------

    def verify_uploaded(self, key: str, expected_size: int, expected_etag: Optional[str] = None) -> bool:
        head = self.backend._client.head_object(Bucket=self.backend.bucket, Key=key)
        if head.get("ContentLength") != expected_size:
            return False
        if expected_etag and head.get("ETag", "").strip('"') != expected_etag.strip('"'):
            return False
        return True

    def get_object_meta(self, key: str) -> dict:
        head = self.backend._client.head_object(Bucket=self.backend.bucket, Key=key)
        return {
            "size": head.get("ContentLength", 0),
            "etag": head.get("ETag", "").strip('"'),
            "content_type": head.get("ContentType", ""),
            "last_modified": head.get("LastModified"),
        }

    # ---------- 回调签名 ----------

    @staticmethod
    def sign_callback(secret: str, key: str, size: int) -> str:
        ts = int(time.time())
        mac = FileService._hmac(secret, f"{key}|{size}", ts)
        sig = base64.urlsafe_b64encode(mac).decode().rstrip("=")
        return f"{ts}.{sig}"

    def verify_callback(self, key: str, token: str, size: int, secret: str, ttl_seconds: int = 60) -> bool:
        try:
            ts_str, sig = token.split(".", 1)
            ts = int(ts_str)
            if abs(int(time.time()) - ts) > ttl_seconds:
                return False
            raw = base64.urlsafe_b64decode(sig + "=" * (-len(sig) % 4))
            if len(raw) != 32:
                return False
            expect = self._hmac(secret, f"{key}|{size}", ts)
            return hmac.compare_digest(raw, expect)
        except Exception:
            return False

    @staticmethod
    def _hmac(secret: str, payload: str, ts: int) -> bytes:
        return hmac.new(secret.encode("utf-8"), f"{ts}:{payload}".encode("utf-8"), "sha256").digest()

    # ---------- 工具 ----------

    @staticmethod
    def _make_key(prefix: str, filename: str) -> str:
        prefix = re.sub(r"[^a-zA-Z0-9/_-]+", "-", prefix.strip("/"))
        name = FileService._sanitize_name(filename)
        return f"{prefix}/{uuid.uuid4().hex[:16]}_{name}"

    @staticmethod
    def _sanitize_name(name: str) -> str:
        name = name.strip().replace("\\", "/").split("/")[-1]
        name = re.sub(r"[^a-zA-Z0-9.\-_]+", "_", name)
        name = name.lstrip(".")
        return name or uuid.uuid4().hex[:8]
