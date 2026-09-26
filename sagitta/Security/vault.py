import hashlib
import hmac
import os
import threading
from typing import Literal, Tuple

Purpose = Literal["jwt_sign", "cookie_encrypt", "callback_sign", "refresh_sign"]


class Vault:
    """主密钥派生中心：所有密钥来自 SG_MASTER_KEY，按用途隔离，支持轮换。"""

    _master: bytes | None = None
    _derived: dict[str, bytes] = {}
    _kid: str = "v1"
    _lock = threading.Lock()

    @classmethod
    def init(cls, master: str | None = None, kid: str = "v1") -> None:
        key = (master or os.getenv("SG_MASTER_KEY") or "").encode("utf-8")
        if not key:
            raise RuntimeError(
                "未设置主密钥！\n"
                "  PowerShell: $env:SG_MASTER_KEY = -join ((48..57+65..70|Get-Random -Count 64|%{[char]$_}) -join '')\n"
                "  或 openssl rand -hex 32\n"
                "写入 .env：SG_MASTER_KEY=...\n"
                "生产环境请用 KMS，禁止写入 config.yaml 或提交 Git。"
            )
        if len(key) < 32:
            raise RuntimeError("SG_MASTER_KEY 长度不足 32 字节。")
        cls._master = key
        cls._kid = kid
        cls._derived.clear()

    @classmethod
    def get(cls, purpose: Purpose, length: int = 64) -> bytes:
        cache_key = f"{cls._kid}:{purpose}:{length}"
        if cache_key in cls._derived:
            return cls._derived[cache_key]
        if cls._master is None:
            cls.init()
        dk = cls._hkdf(cls._master, salt=b"sagitta-v1:" + purpose.encode(), length=length)
        with cls._lock:
            cls._derived[cache_key] = dk
        return dk

    @classmethod
    def rotate(cls, new_master: str, new_kid: str) -> Tuple[bytes, str]:
        """轮换主密钥，返回 (旧key, 旧kid) 供双写过渡期验签。"""
        old = (cls._master or b"", cls._kid)
        cls.init(new_master, new_kid)
        return old

    @classmethod
    def _hkdf(cls, key: bytes, salt: bytes, length: int) -> bytes:
        prk = hmac.new(salt, key, hashlib.sha256).digest()
        out, t, block = b"", b"", 1
        while len(out) < length:
            t = hmac.new(prk, t + bytes([block]), hashlib.sha256).digest()
            out += t
            block += 1
        return out[:length]

    @classmethod
    def random_hex(cls, n: int = 16) -> str:
        return os.urandom(n).hex()
