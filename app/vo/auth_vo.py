# app/vo/auth_vo.py
"""认证出参 VO。

双轨演示：Cookie 由后端在 controller 里下发（HttpOnly，JS 读不到，
防 XSS 窃取）；响应体同时返回令牌，方便移动端 / 非浏览器客户端
改用 Authorization 头。两种客户端用一套接口。
"""
from pydantic import BaseModel

from app.vo.user_vo import UserVO


class LoginResultVO(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int = 900
    user: UserVO


__all__ = ["LoginResultVO"]
