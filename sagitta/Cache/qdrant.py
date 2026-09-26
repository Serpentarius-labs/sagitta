# sagitta/Cache/qdrant.py
from typing import Optional, Any
from qdrant_client import QdrantClient, models

# SgSettingQdrant 类型别名：接受 SimpleNamespace 或 dict
SgSettingQdrant = Any

_client: Optional[QdrantClient] = None


class QdrantRegistry:
    @classmethod
    def init(cls, cfg: SgSettingQdrant) -> None:
        global _client
        scheme = "https" if cfg.https else "http"
        _client = QdrantClient(host=cfg.host, port=cfg.port, grpc_port=cfg.grpc_port,
                               https=cfg.https, api_key=cfg.api_key, prefix=cfg.prefix, timeout=10)
        _client.get_collections()  # 探活

    @classmethod
    def get(cls) -> QdrantClient:
        assert _client is not None, "Qdrant 未初始化，请在 config.yaml 中配置 qdrant"
        return _client

    @classmethod
    def close_all(cls) -> None:
        global _client
        if _client:
            _client.close()
            _client = None


def qdrant_dep():
    async def _dep():
        yield QdrantRegistry.get()
    return _dep