# app/vo/user_vo.py
"""用户出参 VO —— DO 转换的目标结构。

README 硬规则 1：DO 不得流出 Service 层。转换在 Service 里完成，
controller 只返回 VO。框架的 DO 泄漏守卫会在 DO 越界时直接报错。

`from_attributes=True` 是**必须**的：没有它，
`UserVO.model_validate(user_do)` 会在 Pydantic v2 下直接抛
ValidationError（它默认只接受 dict）。原 demo 漏了这行，
因为这条路径从未被真正执行过。
"""
import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class UserVO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(description="uuid7 主键")
    username: str
    nickname: str = ""
    status: str = "active"
    email: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    # 刻意**不暴露** deleted_at / extra：VO 是对外契约，
    # 内部字段不该因为 DO 里有就自动漏出去。


__all__ = ["UserVO"]
