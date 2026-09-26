# app/dto/user_dto.py
"""用户入参 DTO —— 参数校验的唯一位置。

README 分层约定：Controller 只收 DTO、只做参数校验。
校验规则写在这里，不要散落到 Service 或 Controller 里。
"""
import re
from typing import Optional

from pydantic import BaseModel, Field, field_validator

USERNAME_RE = re.compile(r"^[a-zA-Z0-9_.-]{3,32}$")


class UserInsertDTO(BaseModel):
    username: str = Field(min_length=3, max_length=32, description="登录名")
    # 明文密码只在 DTO 里短暂存在，Service 层立刻哈希，绝不明文落库/落日志。
    password: str = Field(min_length=8, max_length=128, description="登录密码（明文仅用于传输）")
    nickname: str = Field("", max_length=64, description="昵称")
    email: Optional[str] = Field(None, max_length=128)

    @field_validator("username")
    @classmethod
    def _valid_username(cls, v: str) -> str:
        if not USERNAME_RE.match(v):
            raise ValueError("登录名只能包含字母、数字、下划线、点、连字符")
        return v

    @field_validator("password")
    @classmethod
    def _valid_password(cls, v: str) -> str:
        # demo 级强度规则：至少两类字符。真实项目按业务收紧，
        # 但不要在错误信息里回显密码本身。
        kinds = sum([bool(re.search(r"[a-z]", v)), bool(re.search(r"[A-Z]", v)),
                     bool(re.search(r"\d", v)), bool(re.search(r"[^a-zA-Z0-9]", v))])
        if kinds < 2:
            raise ValueError("密码需至少包含两类字符（字母/数字/符号）")
        return v


class UserUpdateDTO(BaseModel):
    """部分更新：全部字段可选，None 表示不改。"""

    password: Optional[str] = Field(None, min_length=8, max_length=128,
                                    description="传值表示修改密码")
    nickname: Optional[str] = Field(None, max_length=64)
    status: Optional[str] = Field(None, pattern=r"^(active|disabled)$")
    email: Optional[str] = Field(None, max_length=128)

    @field_validator("password")
    @classmethod
    def _valid_password(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        kinds = sum([bool(re.search(r"[a-z]", v)), bool(re.search(r"[A-Z]", v)),
                     bool(re.search(r"\d", v)), bool(re.search(r"[^a-zA-Z0-9]", v))])
        if kinds < 2:
            raise ValueError("密码需至少包含两类字符（字母/数字/符号）")
        return v


__all__ = ["UserInsertDTO", "UserUpdateDTO"]
