"""端到端验证：完整 demo 应用连真实 PG 跑 CRUD + 分页 + 软删除。
证明守卫/注入/事务/Repository/两种分页在真实 HTTP + 真实 DB 下全部生效。
跑完自动清表。
"""
import os
import uuid
import warnings

warnings.filterwarnings("ignore")
os.environ["SG_MASTER_KEY"] = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
os.environ["SG_DB_DEFAULT_URL"] = "postgresql+asyncpg://postgres:postgres@127.0.0.1:15432/sagitta"
os.environ["SG_APP_ENV"] = "test"
os.environ["SG_LOG_CONSOLE"] = "0"
os.environ["SG_FILE_STRICT_BACKEND"] = "0"
# Redis 指向开发容器实例（16379，避开本机默认 6379）
os.environ["SG_REDIS_DEFAULT_URL"] = "redis://127.0.0.1:16379/0"
os.environ["SG_REDIS_AUTH_URL"] = "redis://127.0.0.1:16379/1"

import asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine

from sagitta.Database.base import Base
from sagitta.Config.setting import get_settings

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  [PASS] " if cond else "  [FAIL] ") + name + (f" :: {detail}" if detail else ""))


async def _prepare_schema():
    """建表（模拟 alembic upgrade head 的效果）。"""
    import demo.do.user_do  # noqa: 触发模型登记进 Base.metadata
    url = get_settings().get_db("default").url
    eng = create_async_engine(url, echo=False)
    async with eng.begin() as conn:
        await conn.run_sync(lambda sc: Base.metadata.drop_all(sc, tables=[demo.do.user_do.UserDO.__table__]))
        await conn.run_sync(lambda sc: Base.metadata.create_all(sc, tables=[demo.do.user_do.UserDO.__table__]))
    await eng.dispose()


asyncio.run(_prepare_schema())


def _run_all_checks(client):
    print("== 健康检查 ==")
    r = client.get("/health")
    check("/health 200", r.status_code == 200, r.text[:60])
    r = client.get("/ready")
    check("/ready 探测真实 DB 返回 200", r.status_code == 200, f"{r.status_code} {r.text[:140]}")
    if r.status_code == 200:
        probes = r.json().get("probes", {})
        check("/ready 探测到 db.default=ok", probes.get("db.default") == "ok", str(probes))

    print("\n== 创建用户（POST /user/）==")
    created_ids = []
    for i in range(5):
        r = client.post("/user/", json={"username": f"user_{i}",
                                        "nickname": f"昵称{i}",
                                        "password": "Passw0rd!"})
        if i == 0:
            body = r.json()
            data = body.get("data") if isinstance(body.get("data"), dict) else {}
            check("创建返回 201", r.status_code == 201, f"{r.status_code} {r.text[:100]}")
            check("创建响应被包装成壳", set(body) >= {"code", "message", "data"}, r.text[:100])
            check("成功 body.code=0", body.get("code") == 0, r.text[:100])
            check("data 含 uuid7 主键", "id" in data, r.text[:120])
            check("VO 不含 deleted_at / password_hash（未泄漏内部字段）",
                  "deleted_at" not in data and "password_hash" not in data,
                  str(list(data.keys())))
        if r.status_code == 201:
            created_ids.append(r.json()["data"]["id"])
    check("成功创建 5 个用户", len(created_ids) == 5, f"n={len(created_ids)}")

    print("\n== 参数校验 -> 422 ==")
    r = client.post("/user/", json={"username": "x", "password": "Passw0rd!"})  # 太短
    check("用户名过短返回 422", r.status_code == 422, f"{r.status_code} {r.text[:80]}")
    r = client.post("/user/", json={"username": "valid_name", "password": "allletters"})
    check("弱密码返回 422", r.status_code == 422, f"{r.status_code} {r.text[:80]}")

    print("\n== 重复用户名 -> 409 BizError ==")
    r = client.post("/user/", json={"username": "user_0", "password": "Passw0rd!"})
    check("重复用户名返回 409", r.status_code == 409, f"{r.status_code} {r.text[:80]}")
    check("重复用户名 body.code=USERNAME_TAKEN",
          r.json().get("code") == "USERNAME_TAKEN", r.text[:80])

    print("\n== 认证流程（/auth/*）==")
    r = client.post("/auth/login", json={"username": "user_0", "password": "wrong-pass"})
    check("错误密码登录 -> 401", r.status_code == 401, f"{r.status_code} {r.text[:80]}")
    r = client.post("/auth/login", json={"username": "user_0", "password": "Passw0rd!"})
    check("正确密码登录 -> 200", r.status_code == 200, f"{r.status_code} {r.text[:80]}")
    token = r.json().get("data", {}).get("access_token", "")
    check("登录返回 access_token", bool(token), r.text[:100])
    # 登录响应同时下发了 Cookie，TestClient 会自动携带；
    # 要验证「无令牌」场景必须先清掉 Cookie 罐。
    client.cookies.clear()
    r = client.get("/auth/me")
    check("未带令牌访问 /me -> 401", r.status_code == 401, f"{r.status_code}")
    r = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    check("带令牌访问 /me -> 200", r.status_code == 200, f"{r.status_code} {r.text[:80]}")
    check("/me 返回当前用户名", r.json().get("data", {}).get("username") == "user_0",
          r.text[:100])
    r = client.post("/auth/logout", headers={"Authorization": f"Bearer {token}"})
    check("登出 -> 200", r.status_code == 200, f"{r.status_code}")
    r = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    check("登出后旧令牌失效 -> 401", r.status_code == 401, f"{r.status_code}")

    print("\n== 查询单个（GET /user/{id}）==")
    if created_ids:
        r = client.get(f"/user/{created_ids[0]}")
        check("按 id 查询 200", r.status_code == 200, f"{r.status_code} {r.text[:80]}")
        check("查到的 id 一致", r.json()["data"]["id"] == created_ids[0], r.text[:100])
        r = client.get(f"/user/{uuid.uuid4()}")
        check("查询不存在的 id -> 404", r.status_code == 404, f"{r.status_code} {r.text[:80]}")
        check("404 body.code=NOT_FOUND", r.json().get("code") == "NOT_FOUND", r.text[:80])

    print("\n== OFFSET 分页（GET /user/page）==")
    r = client.get("/user/page?page=1&page_size=2")
    check("分页 200", r.status_code == 200, f"{r.status_code} {r.text[:80]}")
    data = r.json().get("data", {})
    check("分页返回 total=5", data.get("total") == 5, str(data)[:120])
    check("分页 items 长度=2", len(data.get("items", [])) == 2, str(len(data.get("items", []))))
    r = client.get("/user/page?page=1&page_size=999999")
    check("非法 page_size 被拒(422)", r.status_code == 422, f"{r.status_code}")

    print("\n== keyset 游标分页（GET /user/feed）==")
    r = client.get("/user/feed?page_size=2")
    check("feed 200", r.status_code == 200, f"{r.status_code} {r.text[:80]}")
    fd = r.json().get("data", {})
    check("feed 返回 next_cursor", fd.get("next_cursor") is not None, str(fd)[:120])
    check("feed has_more=True", fd.get("has_more") is True, str(fd.get("has_more")))
    r2 = client.get(f"/user/feed?page_size=2&cursor={fd.get('next_cursor')}")
    check("用 cursor 翻页 200", r2.status_code == 200, f"{r2.status_code} {r2.text[:80]}")
    ids1 = {x["id"] for x in fd.get("items", [])}
    ids2 = {x["id"] for x in r2.json().get("data", {}).get("items", [])}
    check("游标翻页无重复", not (ids1 & ids2), f"overlap={ids1 & ids2}")
    r3 = client.get("/user/feed?page_size=2&cursor=!!!invalid!!!")
    check("非法 cursor -> 4xx（不静默当首页）", 400 <= r3.status_code < 500,
          f"{r3.status_code} {r3.text[:80]}")
    check("非法 cursor body.code=INVALID_CURSOR",
          r3.json().get("code") == "INVALID_CURSOR", r3.text[:80])

    print("\n== 软删除 + 默认过滤 ==")
    if created_ids:
        victim = created_ids[-1]
        r = client.delete(f"/user/{victim}")
        check("软删除 200", r.status_code == 200, f"{r.status_code} {r.text[:80]}")
        r = client.get(f"/user/{victim}")
        check("软删除后按 id 查询 -> 404（默认过滤）", r.status_code == 404, f"{r.status_code}")
        r = client.get("/user/page?page=1&page_size=50")
        check("软删除后分页 total=4", r.json()["data"]["total"] == 4,
              str(r.json()["data"].get("total")))

    print("\n== OpenAPI schema ==")
    spec = client.get("/openapi.json").json()
    paths = spec.get("paths", {})
    check("/user/page 在 schema 中", "/user/page" in paths, str(list(paths)[:8]))
    create_resp = paths.get("/user/", {}).get("post", {}).get("responses", {}).get("201", {})
    check("创建接口 schema 引用 Resp 壳", "Resp" in str(create_resp), str(create_resp)[:120])


# 现在导入 main（触发 bootstrap + Vault + 控制器登记）
import main

# ⚠️ TestClient 必须用 `with` 上下文才会触发 FastAPI 的 lifespan，
# 否则 EngineRegistry/RedisRegistry 从未初始化，所有带 DB 的接口 500。
# 这正是原框架「DB 未接线」症状的测试侧对应物。
with TestClient(main.app, raise_server_exceptions=False) as client:
    _run_all_checks(client)

print(f"\n==== 端到端结果：{len(PASS)} PASS / {len(FAIL)} FAIL ====")
if FAIL:
    print("失败项：", FAIL)


# 清理：删表
async def _cleanup():
    import demo.do.user_do
    url = get_settings().get_db("default").url
    eng = create_async_engine(url, echo=False)
    async with eng.begin() as conn:
        await conn.run_sync(lambda sc: Base.metadata.drop_all(sc, tables=[demo.do.user_do.UserDO.__table__]))
    await eng.dispose()


asyncio.run(_cleanup())
print("已清理测试表")

if FAIL:
    raise SystemExit(1)
