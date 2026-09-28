# app/dto/auth_dto.py
"""认证入参 DTO。

注意与注册 DTO 的区别：登录**不做**密码强度校验——强度规则只约束「设置
密码」的场景；登录时任何强度的旧密码都应能通过校验，否则老用户会被锁死。
"""
from pydantic import BaseModel, Field


class LoginDTO(BaseModel):
    username: str = Field(min_length=1, max_length=32, description="登录名")
    password: str = Field(min_length=1, max_length=128, description="密码")


__all__ = ["LoginDTO"]
