# app/controllers/file_controller.py
"""文件 Controller —— 对象存储直传 / 分片 / 下载的**正确示范**。

链路设计（为什么这样分）：
  * 上传走**预签名直传**：客户端拿 URL 后直接 PUT 到 MinIO/S3，字节
    不过应用服务器——大文件场景省带宽，也不会把 API worker 占满。
  * 大文件（>20MB）预签名返回**分片计划**，客户端逐片上传后回调
    complete；中途放弃调 abort，避免已上传分片一直占存储计费。
  * 下载同样给预签名 URL；需要经 API 出口（鉴权/审计/内网客户端）时
    用 GET /file/{key}，由 SgFileResponse 按策略处理。

本控制器依赖 FileService：它不是 ServiceBase，而是在 main.py 里通过
setup_file_storage() 显式注册后按类型注入的。
"""
from typing import Optional

from fastapi import Query, Request

from sagitta.Core.inject import Inject
from sagitta.File.response import SgFileResponse
from sagitta.File.service import FileService
from sagitta.Flow.controller import Controller

from app.dto.file_dto import (
    CompleteMultipartDTO, MultipartRefDTO, UploadPresignDTO,
)


class FileController(Controller):
    prefix = "/file"
    tags = ["文件"]

    # FileService 不是 ServiceBase（持有后端连接，非无状态单例），
    # 纯类型注解不会被自动注入；必须用 Inject() 显式声明「按类型取组件」。
    fs: FileService = Inject()

    # ---------- 上传 ----------

    @Controller.post("/upload/presign",
                     summary="获取上传预签名（小文件直传，大文件自动分片计划）")
    async def upload_presign(self, dto: UploadPresignDTO) -> dict:
        return self.fs.presign_upload(
            dto.prefix, dto.filename, dto.size, dto.content_type
        )

    @Controller.post("/upload/complete", summary="完成分片上传")
    async def upload_complete(self, dto: CompleteMultipartDTO) -> dict:
        url = self.fs.complete_upload(
            dto.key, dto.upload_id, [p.model_dump() for p in dto.parts]
        )
        return {"key": dto.key, "url": url}

    @Controller.post("/upload/abort", summary="取消分片上传并清理已上传分片")
    async def upload_abort(self, dto: MultipartRefDTO) -> dict:
        self.fs.abort_upload(dto.key, dto.upload_id)
        return {"aborted": True}

    # ---------- 下载 ----------

    # 注意：此路由必须在 "/{key:path}" **之前**声明，否则 download/presign
    # 会被通配路由当成 key 截获（同方法路由按声明顺序匹配）。
    @Controller.get("/download/presign", summary="获取下载预签名 URL")
    async def download_presign(
        self,
        key: str = Query(..., min_length=1, max_length=512),
        filename: Optional[str] = Query(None, max_length=255),
        inline: bool = Query(False, description="true=内联展示，false=附件下载"),
    ) -> dict:
        return self.fs.presign_download(key, filename, inline=inline)

    @Controller.get("/{key:path}",
                    summary="经 API 下载（auto：按配置走 oss 流式代理）")
    async def download(
        self,
        request: Request,
        key: str,
        filename: Optional[str] = Query(None, max_length=255),
    ) -> SgFileResponse:
        name = filename or key.rstrip("/").split("/")[-1] or "file"
        # request 必传：SgFileResponse 要从它读 Range 头以支持断点续传。
        return SgFileResponse(request=request, oss_key=key, filename=name)


__all__ = ["FileController"]
