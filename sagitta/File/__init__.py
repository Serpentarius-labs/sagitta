# sagitta/File/__init__.py
"""File 子包：S3 兼容存储后端、流式响应。

导入示例::

    from sagitta.File import FileService, build_backend, SgFileResponse

S3 后端依赖 boto3/aiobotocore（pip install sagitta[s3]），
未安装时 S3Backend/build_backend 不可用但不影响 FileService 基础功能。
"""
from .service import FileService

__all__ = [
    # service
    "FileService",
]

# S3 后端是可选依赖，未安装时跳过 re-export
try:
    from .storage import StorageBackend, S3Backend, build_backend
    __all__ += ["StorageBackend", "S3Backend", "build_backend"]
except ImportError:
    pass

try:
    from .response import SgFileResponse, close_shared_oss_clients
    __all__ += ["SgFileResponse", "close_shared_oss_clients"]
except ImportError:
    pass
