# app/storage_setup.py
"""对象存储装配 —— 宿主如何把框架文件组件接入应用的**正确示范**。

关键时序：本函数必须在 bootstrap() **之前**调用。控制器在 mount 阶段
（bootstrap 内部）就会按类型注解解析 FileService；若等到 lifespan 的
启动钩子里才注册，挂载时就会抛 InjectionError。
"""
import logging

from sagitta.Core.inject import Container
from sagitta.File.service import FileService
from sagitta.File.storage import S3Backend

logger = logging.getLogger("sagitta")


def setup_file_storage(settings) -> FileService:
    """构造 S3 后端 → 确保 bucket 存在 → 把 FileService 注册进 DI 容器。"""
    f = settings.file
    # 失败要吵出来：配置不全时继续启动，会让第一个上传请求以更难懂的
    # 连接错误形式爆发，不如现在就中断。
    if not (f.endpoint and f.bucket and f.access_key and f.secret_key):
        raise RuntimeError(
            "文件存储配置不完整：需设置 file.endpoint / bucket / access_key / secret_key"
        )

    backend = S3Backend(
        f.endpoint, f.bucket, f.access_key, f.secret_key,
        region=f.region, public_endpoint=f.public_domain or None,
    )
    _ensure_bucket(backend, f.bucket)

    svc = FileService(backend)
    # FileService 不是 ServiceBase（它持有后端连接，非无状态），
    # 显式注册后控制器即可按类型注入。
    Container.register(FileService, svc)
    logger.info("[boot] 文件存储就绪 endpoint=%s bucket=%s", f.endpoint, f.bucket)
    return svc


def _ensure_bucket(backend: S3Backend, bucket: str) -> None:
    """开发便利：bucket 不存在则自动创建。

    生产环境建议由运维/IaC 统一建桶，应用 AK 不给 CreateBucket 权限；
    那时候本函数会因 403 直接失败——这是对的，不要为了「能启动」
    去 catch 掉 403。
    """
    from botocore.exceptions import ClientError

    try:
        backend._client.head_bucket(Bucket=bucket)
    except ClientError as e:
        code = e.response["Error"]["Code"]
        if code not in ("404", "NoSuchBucket"):
            raise
        backend._client.create_bucket(Bucket=bucket)
        logger.info("[boot] 已自动创建 bucket %s（生产建议由运维建桶）", bucket)
