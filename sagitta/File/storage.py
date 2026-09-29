# sagitta/File/storage.py
from abc import ABC, abstractmethod
from typing import Literal, Optional

Provider = Literal["minio", "s3", "aliyun"]


class StorageBackend(ABC):
    @abstractmethod
    def presign_url(self, key: str, expire_seconds: int = 3600, method: str = "GET") -> str: ...
    @abstractmethod
    def public_url(self, key: str) -> str: ...
    @abstractmethod
    def head(self, key: str) -> dict: ...
    @abstractmethod
    def upload(self, key: str, content: bytes, content_type: str = "application/octet-stream") -> str: ...
    @abstractmethod
    def delete(self, key: str) -> None: ...


class S3Backend(StorageBackend):
    """boto3 唯一实现：MinIO / AWS S3 / 阿里云 OSS(S3兼容) / R2 / B2 通用"""
    def __init__(self, endpoint: str, bucket: str, ak: str, sk: str,
                 region: str = "us-east-1", public_endpoint: Optional[str] = None,
                 force_path_style: bool = True,
                 signature_version: str = "s3v4"):
        import boto3
        from botocore.config import Config
        self.bucket = bucket
        self._public_endpoint = (public_endpoint or endpoint).rstrip("/")
        # signature_version 默认 s3v4：不显式指定时 generate_presigned_url
        # 可能退回 SigV2（URL 里带 AWSAccessKeyId&Expires），而现代 MinIO
        # 已拒绝 SigV2 → 预签名直传一律 403。
        self._client = boto3.client(
            "s3", endpoint_url=endpoint, region_name=region,
            aws_access_key_id=ak, aws_secret_access_key=sk,
            use_ssl=endpoint.startswith("https"),
            config=Config(
                signature_version=signature_version,
                s3={"addressing_style": "path"} if force_path_style else {"addressing_style": "virtual"},
            ),
        )

    def presign_url(self, key: str, expire_seconds: int = 3600, method: str = "GET") -> str:
        action = "put_object" if method.upper() == "PUT" else "get_object"
        return self._client.generate_presigned_url(
            action, Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=expire_seconds)

    def public_url(self, key: str) -> str:
        return f"{self._public_endpoint}/{self.bucket}/{key}"

    def head(self, key: str) -> dict:
        return self._client.head_object(Bucket=self.bucket, Key=key)

    def upload(self, key: str, content: bytes, content_type: str = "application/octet-stream") -> str:
        self._client.put_object(Bucket=self.bucket, Key=key, Body=content, ContentType=content_type)
        return self.public_url(key)

    def delete(self, key: str) -> None:
        self._client.delete_object(Bucket=self.bucket, Key=key)


def build_backend(provider: Provider, cfg) -> S3Backend:
    return S3Backend(cfg.endpoint, cfg.bucket, cfg.access_key, cfg.secret_key,
                     region=cfg.region, public_endpoint=cfg.public_domain or None,
                     force_path_style=(provider != "aliyun"))