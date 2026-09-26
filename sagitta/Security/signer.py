import base64
import hashlib
import hmac
import time


class Signer:
    """通用 HMAC 签名器：webhook 回调校验、临时凭证自签、时效链接。"""

    def __init__(self, secret: bytes, algo: str = "sha256", ttl: int = 300):
        self.secret = secret
        self.algo = algo
        self.ttl = ttl

    def sign(self, *parts) -> str:
        msg = "|".join(self._safe(p) for p in parts).encode("utf-8")
        return base64.urlsafe_b64encode(
            hmac.new(self.secret, msg, getattr(hashlib, self.algo)).digest()
        ).decode().rstrip("=")

    def verify(self, signature: str, *parts) -> bool:
        return hmac.compare_digest(self.sign(*parts), signature)

    def signed_token(self, *parts) -> str:
        ts = int(time.time())
        payload = base64.urlsafe_b64encode("|".join(self._safe(p) for p in parts).encode()).decode().rstrip("=")
        return f"{ts}:{payload}.{self.sign(ts, payload)}"

    def unwrap(self, token: str) -> tuple:
        try:
            head, sig = token.split(".", 1)
            ts, payload = head.split(":", 1)
            raw = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)).decode()
            if not self.verify(sig, ts, payload):
                raise ValueError("签名无效")
            if int(time.time()) - int(ts) > self.ttl:
                raise ValueError("签名已过期")
            return (int(ts), *raw.split("|"))
        except Exception as e:
            raise ValueError(f"令牌解包失败: {e}") from e

    @staticmethod
    def _safe(v) -> str:
        s = str(v)
        if "|" in s or ":" in s or "." in s:
            raise ValueError(f"签名片段含保留字符: {s}")
        return s
