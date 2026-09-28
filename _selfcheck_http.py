"""HTTP 层守卫验证：README 硬规则 1/2 是否真的生效。
不依赖 DB/Redis（配置里 db/redis 置空，只测 Flow 层契约）。
"""
import warnings
from typing import List, Optional

warnings.filterwarnings("ignore")

from fastapi.testclient import TestClient
from fastapi import Depends
from pydantic import BaseModel
from sqlalchemy import Column, Integer, String

from sagitta.app.bootstrap import bootstrap
from sagitta.Config.setting import build_settings, set_settings
from sagitta.Core.inject import ServiceBase
from sagitta.Database.base import Base
from sagitta.Flow.controller import Controller, mount_all
from sagitta.Flow.page import Page, PageQuery
from sagitta.Flow.response import SgResponse

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  [PASS] " if cond else "  [FAIL] ") + name + (f" :: {detail}" if detail else ""))


# ---- ORM 模型（不建表，只实例化用于泄漏检测）----
class UserDO(Base):
    __tablename__ = "sg_http_user"
    id = Column(Integer, primary_key=True)
    name = Column(String(64))


class UserVO(BaseModel):
    id: int
    name: str


class InDTO(BaseModel):
    age: int  # 必填，用于测 422


class DemoService(ServiceBase):
    async def hello(self):
        return "from-service"


# ---- 控制器：覆盖各种返回形态 ----
class GuardController(Controller):
    prefix = "/guard"
    tags = ["守卫"]
    svc: DemoService  # 测注入

    @Controller.get("/bare")
    async def bare(self):
        return {"name": "plain", "id": 1}  # 裸数据，应被自动包装

    @Controller.get("/wrapped")
    async def wrapped(self):
        return {"code": 200, "message": "ok", "data": 1}  # 手写壳，应报错

    @Controller.get("/do")
    async def do_leak(self):
        u = UserDO(id=1, name="x")
        return u  # DO 泄漏，应报错

    @Controller.get("/do-list")
    async def do_in_list(self):
        return [UserDO(id=1, name="a"), UserDO(id=2, name="b")]  # 嵌套在 list

    @Controller.get("/do-dict")
    async def do_in_dict(self):
        return {"user": UserDO(id=3, name="c")}  # 嵌套在 dict

    @Controller.get("/vo-ok")
    async def vo_ok(self) -> UserVO:
        return UserVO(id=1, name="ok")  # VO 正常，应被包装

    @Controller.get("/page")
    async def page_ok(self, q: PageQuery = Depends()) -> Page[UserVO]:
        items = [UserVO(id=i, name=f"n{i}") for i in range(q.limit)]
        return Page.of(items, q.page, q.page_size, total=100)

    @Controller.post("/inject")
    async def inject(self):
        return {"msg": await self.svc.hello()}  # 测 Service 注入

    @Controller.post("/validate")
    async def validate(self, dto: InDTO):
        return {"age": dto.age}  # 缺 age 应 422

    @Controller.get("/custom-code")
    async def custom_code(self):
        return SgResponse(data={"created": True}, code=201, status_code=201)  # 自定义码放行


settings = build_settings({
    "app": {"env": "test", "debug": False, "check_do_leak": True, "wrap_response": True},
    "db": {}, "redis": {},
    "log": {"console": False, "level": "ERROR"},
})
set_settings(settings)

app = bootstrap(settings=settings, mount_controllers=True, controller_packages=[])
client = TestClient(app, raise_server_exceptions=False)

print("== README 硬规则 1：响应自动包装 ==")
r = client.get("/guard/bare")
check("裸 dict 被自动包装成 {code,message,data}",
      r.status_code == 200 and set(r.json()) >= {"code", "message", "data"}
      and r.json()["data"] == {"name": "plain", "id": 1}, f"{r.status_code} {r.text[:80]}")
check("成功 body.code 恒为 0（与 HTTP code 解耦）",
      r.json()["code"] == 0, r.text[:80])

print("\n== README 硬规则 1：禁止手写响应壳 ==")
r = client.get("/guard/wrapped")
check("手写 {code,message,data} 被拒绝(500)", r.status_code == 500, f"{r.status_code} {r.text[:80]}")
check("手写壳报错含架构违规提示", "架构违规" in r.text or "已包装" in r.text, r.text[:100])

print("\n== README 硬规则 1：DO 不得流出 Service 层 ==")
r = client.get("/guard/do")
check("直接返回 DO 被拦截(500)", r.status_code == 500, f"{r.status_code} {r.text[:80]}")
check("DO 泄漏报错含架构违规+位置", "架构违规" in r.text and "guard/do" in r.text, r.text[:120])
r = client.get("/guard/do-list")
check("list 中嵌套 DO 被拦截", r.status_code == 500, f"{r.status_code}")
check("list 泄漏给出索引路径[0]", "[0]" in r.text, r.text[:120])
r = client.get("/guard/do-dict")
check("dict 中嵌套 DO 被拦截", r.status_code == 500, f"{r.status_code}")
check("dict 泄漏给出键路径['user']", "user" in r.text, r.text[:120])

print("\n== VO / 分页 正常放行并包装 ==")
r = client.get("/guard/vo-ok")
check("VO 返回被包装且数据正确", r.status_code == 200 and r.json()["data"] == {"id": 1, "name": "ok"}, r.text[:80])
r = client.get("/guard/page?page=1&page_size=10")
check("Page[VO] 被包装", r.status_code == 200 and "items" in r.json()["data"], r.text[:80])
check("Page 保留 total", r.json()["data"].get("total") == 100, r.text[:100])

print("\n== README 硬规则 2：Service 自动注入 ==")
r = client.post("/guard/inject")
check("svc 注入生效(非500)", r.status_code == 200, f"{r.status_code} {r.text[:100]}")
check("注入的 Service 返回正确", r.json().get("data", {}).get("msg") == "from-service", r.text[:100])

print("\n== 参数校验 -> 422（不被吞成 500）==")
r = client.post("/guard/validate", json={})
check("缺失必填字段返回 422", r.status_code == 422, f"{r.status_code} {r.text[:80]}")
check("校验失败 body.code=VALIDATION_ERROR",
      r.json()["code"] == "VALIDATION_ERROR", r.text[:80])

print("\n== SgResponse 自定义 code 直接放行 ==")
r = client.get("/guard/custom-code")
check("自定义 201 状态码放行", r.status_code == 201, f"{r.status_code}")
check("自定义 code=201 写入 body", r.json().get("code") == 201, r.text[:80])

print("\n== trace_id 贯通响应头 ==")
r = client.get("/guard/bare")
check("响应头含 X-Trace-Id", "x-trace-id" in {k.lower() for k in r.headers}, str(dict(r.headers))[:120])
r = client.get("/guard/bare", headers={"X-Trace-Id": "abc123def456"})
check("客户端传入的 trace_id 被回显", r.headers.get("x-trace-id") == "abc123def456", r.headers.get("x-trace-id", ""))

print("\n== OpenAPI schema 含 Resp[T] 壳结构 ==")
spec = app.openapi()
bare = spec["paths"].get("/guard/bare", {}).get("get", {})
check("/guard/bare 有 200 response schema", "200" in bare.get("responses", {}), str(list(bare.get("responses", {}))))
vo = spec["paths"].get("/guard/vo-ok", {}).get("get", {})
schema_ref = str(vo.get("responses", {}).get("200", {}))
check("VO 接口 schema 引用 Resp 壳", "Resp" in schema_ref, schema_ref[:120])

print(f"\n==== HTTP 守卫结果：{len(PASS)} PASS / {len(FAIL)} FAIL ====")
if FAIL:
    print("失败项：", FAIL)
    raise SystemExit(1)
