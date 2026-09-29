# sagitta/File/__init__.py
"""File 子包：S3 兼容存储后端、流式响应。

导入示例::

    from sagitta.File import FileService, build_backend, SgFileResponse
"""
from .service import FileService
from .storage import StorageBackend, S3Backend, build_backend
from .response import SgFileResponse, close_shared_oss_clients

__all__ = [
    # service
    "FileService",
    # storage
    "StorageBackend", "S3Backend", "build_backend",
    # response
    "SgFileResponse", "close_shared_oss_clients",
]
