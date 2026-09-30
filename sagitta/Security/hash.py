import re
from typing import Tuple

from argon2 import PasswordHasher as _Argon2
from argon2.exceptions import InvalidHash, VerifyMismatchError

DEFAULT_PARAMS = {
    "time_cost": 3,
    "memory_cost": 64 * 1024,
    "parallelism": 4,
    "hash_len": 32,
    "salt_len": 16,
}


class NeedsRehashError(Exception):
    """哈希参数已过时，验证成功后应回写新哈希。"""
    pass


class PasswordHasher:
    _inst = _Argon2(**DEFAULT_PARAMS)
    _pattern = re.compile(r"\$argon2(id|i)?\$v=(\d+)\$m=(\d+),t=(\d+),p=(\d+)")

    @classmethod
    def hash(cls, password: str) -> str:
        return cls._inst.hash(password)

    @classmethod
    def verify(cls, password: str, hashed: str) -> bool:
        try:
            cls._inst.verify(hashed, password)
            return True
        except VerifyMismatchError:
            return False
        except InvalidHash:
            raise

    @classmethod
    def check_and_rehash(cls, password: str, hashed: str) -> Tuple[bool, str | None]:
        """验证成功且参数过时时返回新哈希，调用方应回写 DB。"""
        ok = cls.verify(password, hashed)
        if not ok:
            return False, None
        if cls._needs_rehash(hashed):
            return True, cls._inst.hash(password)
        return True, None

    @classmethod
    def _needs_rehash(cls, hashed: str) -> bool:
        m = cls._pattern.search(hashed)
        if not m:
            return True
        _, v, mem, tc, p = m.groups()
        return (
            int(v) < 19
            or int(mem) < DEFAULT_PARAMS["memory_cost"]
            or int(tc) < DEFAULT_PARAMS["time_cost"]
            or int(p) < DEFAULT_PARAMS["parallelism"]
        )
