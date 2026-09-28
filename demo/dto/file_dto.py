# app/dto/file_dto.py
"""文件接口入参 DTO。

S3 分片字段刻意保留大写开头（PartNumber / ETag）：S3 CompleteMultipartUpload
的协议字段就是这两个名字，model_dump() 后可直接透传给 boto3，不需要
再做一层字段名映射——映射层越多，越容易在协议升级时漏改。
"""
from typing import List

from pydantic import BaseModel, Field


class UploadPresignDTO(BaseModel):
    prefix: str = Field("demo", max_length=64, description="对象 key 前缀（业务目录）")
    filename: str = Field(min_length=1, max_length=255)
    size: int = Field(ge=0, le=5 * (1 << 30), description="字节数")
    content_type: str = Field("application/octet-stream", max_length=128)


class MultipartRefDTO(BaseModel):
    key: str = Field(min_length=1, max_length=512)
    upload_id: str = Field(min_length=1, max_length=256)


class UploadPartDTO(BaseModel):
    PartNumber: int = Field(ge=1, le=10000)
    ETag: str = Field(min_length=1, max_length=256)


class CompleteMultipartDTO(MultipartRefDTO):
    parts: List[UploadPartDTO] = Field(min_length=1, max_length=10000)


__all__ = ["UploadPresignDTO", "MultipartRefDTO", "UploadPartDTO",
           "CompleteMultipartDTO"]
