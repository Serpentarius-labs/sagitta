# Sagitta（蛇鹫）

FastAPI 工程化骨架。外表克制，下手狠，专治业务代码里的蛇。

> 📖 **第一次接触？先读 [使用指南](docs/guide.md)**：一个功能从 0 到上线的完整动线、如何与原生 FastAPI 混用、以及什么项目该用 / 不该用。

> Sagittarius serpentarius —— 冠羽如箭，步态优雅，捕猎时专踩毒蛇。

## 一、它是什么

Sagitta 不只是一个库，也不只是一套脚手架模板。它是一套**跑起来就能写业务的 FastAPI 工程约定**，以可安装、可导入的 Python 包形式提供（见 pyproject.toml）。

它替你定死五件事：

| 维度 | 它做的事 |
|---|---|
| **分层** | DO / DTO / VO 职责隔离，DO 流出 service 层直接运行时报错并给出字段路径 |
| **响应** | endpoint 只返回裸数据，禁止手写 code/message，trace_id 自动注入 header 与日志 |
| **存储链路** | 数据库（MySQL / PostgreSQL 双驱动）+ Redis + Qdrant 统一初始化、探活与优雅关闭 |
| **可观测性** | 慢 SQL、连接池排队、HTTP 耗时三件套 + Prometheus 指标（/metrics），零配置出日志 |
| **安全** | 全局限流、请求体大小限制、权限系统（资源级 + 数据级）、JWT 认证闭环 |

剩下的路由写法、业务逻辑，你自由发挥。

它不提供的：ORM 模型生成、LLM 调用、沙箱执行。这些属于业务层，框架只留 hook。

## 二、为什么需要它

FastAPI 的官方示例停在「一个 main.py 里写三个路由」。真实项目撑到第三个月通常会出现：

- 有人直接在 controller 里拼 SQL，有人在 service 里返回 DO，有人给前端返回 {code, msg, data}，隔壁模块返回 {status, message, result} —— **同一个项目三种响应壳**。
- 日志里查不到请求关联，线上出问题只能靠猜时间戳。
- 慢 SQL 拖垮实例，但没人知道是哪条，因为 SQLAlchemy 的 echo 开起来就是洪水。
- 测试里直接 import 业务代码，结果配置没加载，报 settings 未初始化，于是大家开始在测试里手抄一遍 bootstrap。

Sagitta 的做法是**把这些坑变成运行时错误**，而不是靠 Code Review 提醒。违规代码在开发环境就会炸，且报错信息会告诉你怎么改。

## 三、架构分层

```
请求 → TraceMiddleware(trace_id)
     → Controller(只收 DTO、只做参数校验)
       → Service(纯业务、无状态单例、DO↔VO 转换在此完成)
         → DO / Repository(唯一允许出现 SQL 的地方)
     ← SgResponse(code/message/trace_id 统一包装)
```

```
sagitta/                  # 框架层：你不该改这里
├── App/      bootstrap, lifespan
├── Config/   yaml + env 合并加载，SgSettings
├── Core/     依赖注入容器、ServiceBase、指标采集、请求体限制
├── Database/ engine/session/慢SQL/连接池监控
├── Cache/    redis 池、分布式锁、装饰器缓存、Qdrant
├── Flow/     Controller 基类、SgResponse、Page 分页
├── Security/ Vault 主密钥派生、JWT 签发校验、Argon2 哈希、登录守卫、认证中间件、限流、权限系统
├── File/     S3 兼容存储后端（预签名直传）+ 流式代理 + Nginx X-Accel + python 兜底
├── Log/      stdlib/loguru 双后端、TraceMiddleware
└── Utils/    trace_id(uuid7)、工具函数

app/                        # 业务层：你只该在这里写代码
│             （本仓库示例代码在 demo/ 下，宿主项目用 app/）
├── do/       UserDO        # SQLAlchemy Model，禁止离开 app/
├── dto/      UserInsertDTO # 入参校验
├── vo/       UserVO        # 出参结构
├── services/ UserService, AuthService  # 业务逻辑，继承 ServiceBase 自动注册
├── biz_code.py             # 业务码集中定义处（码册）
├── storage_setup.py        # 有状态组件（FileService）的装配示范
└── controllers/ UserController, AuthController, FileController
                            # 路由，继承 Controller 自动挂载
```

判断标准：如果一段代码删掉 sagitta 就完全看不懂了，说明它放错了位置。

## 四、三分钟跑起来

### 4.1 环境

Python 3.11+（3.12+ 有原生 uuid7 支持；loguru 后端依赖 contextvars 传播语义，<3.11 时请使用 stdlib 后端）。

### 4.2 启动依赖

```
docker compose -f docker-compose.dev.yml up -d
```

这会拉起：PostgreSQL 16 + pgvector(**15432**)、Redis(**16379**)、Qdrant(**16333/16334**，默认关闭）、MinIO(**9000/9001**)。

端口刻意避开本机其他项目占用的 5432/6379/6333。

MinIO 控制台：http://127.0.0.1:9001，账号 admin / admin123456，bucket `demo` 自动创建。

### 4.3 启动应用

```
pip install -r requirements.txt
python main.py
```

conf/config.yaml 已随仓库提供，开箱即连 4.2 的容器；需要改连接目标时改它或用 SG_ 环境变量覆盖。

- 文档：http://127.0.0.1:8000/docs
- 健康检查：http://127.0.0.1:8000/health
- 就绪探针：http://127.0.0.1:8000/ready（会真实探测 DB 与 Redis，K8s readiness 用）
- Prometheus 指标：http://127.0.0.1:8000/metrics

### 4.4 验证一切正常

```
python _selfcheck_config.py   # 配置加载与 env 覆盖
python _selfcheck_db.py       # 真实 PG：建表/分页/软删除/事务
python _selfcheck_http.py     # HTTP 守卫：包装/DO 泄漏/校验/trace
python _selfcheck_e2e.py      # 端到端：CRUD + 认证全流程
pytest -v               # 75 个单元测试：游标编解码/配置校验/路由排序/权限系统/中间件
```

四个脚本均应零 FAIL。

## 五、配置

> 完整的配置设计（分类原则、场景抉择、Docker / K8s / GitOps 演进）见 **[配置管理指南](docs/config.md)**。

### 5.1 加载优先级（六层，后者覆盖前者）

```
代码默认值
  < conf/config.yaml                基线（入库、非敏感）
  < conf/config.<profile>.yaml ...  SG_PROFILE 链，自由命名，如 prod / staging,canary
  < conf/config.local.yaml          个人覆盖（gitignore）
  < .env                            本地开发便利件（gitignore，容器内 no-op）
  < SG_ 真实环境变量                 CI / Docker / K8s Secret / ConfigMap
```

环境变量按下划线展开为嵌套结构：`SG_DB_DEFAULT_HOST=10.0.0.1` → `db.default.host`；
也支持 `SG_LOG_BACKEND=stdlib` 这种省略节名的扁平写法（全库唯一时生效）。

**密钥边界**：`.env` 与一切真实值文件被 git 过滤，复制 [.env.example](.env.example) 上手；
入库的 profile yaml 只放非敏感差异，模板见 [conf/config.prod.yaml.example](conf/config.prod.yaml.example)。

### 5.2 关键项速查

| 配置 | 说明 | 建议值 |
|---|---|---|
| app.env / SG_PROFILE | env 是标签，PROFILE 是覆盖层选择器，两者通常同值 | 见配置指南 3.4 |
| app.check_do_leak | DO 泄漏运行时守卫 | 全环境保持 true |
| db.default.type | mysql / postgresql | demo 用 postgresql |
| db.default.echo | SQL 全量打印 | 仅本地 debug 开，生产必关 |
| file.default_strategy | auto / oss / nginx / python | 生产 oss，本地 python |
| log.format | text / json | 接 Loki/ELK 时用 json |
| app.build_commit | CI 构建时注入的 commit，/health 可查 | SG_BUILD_COMMIT |
| rate_limit.enabled | 全局限流总开关 | 生产保持 true |
| rate_limit.ip_requests | 单 IP 窗口内最大请求数 | 60 |
| server.max_body_bytes | 请求体大小上限（字节），0 不限制 | 10485760 (10MB) |
| app.shutdown_timeout | 优雅关闭等待 in-flight 请求超时（秒） | 10.0 |

多数据源、多 Redis 实例直接在 db / redis 下加 key 即可，代码侧用 session_dep("read")、RedisRegistry.get("cache") 取。

## 六、硬规则（违反即报错）

### 规则 1：endpoint 只返回裸数据

```
# 正确
return await self.svc.create(db, dto)          # dict / VO / Page 均可，自动套壳
# 成功壳：HTTP 200/201 + body {"code": 0, "message": "success", "data": ...}

# 错误：手写响应壳
return {"code": 0, "message": "ok", "data": user}
# → WrappedResponseError: 路由 /user 返回了疑似已包装的数据 ['code','message','data']

# 错误：DO 直接流出
return user_do
# → DOLeakError: 【架构违规】DO 对象流出 Service 层！位置: /user -> [0] .name
```

临时关闭（仅限排查问题）：SG_CHECK_DO_LEAK=0。

### 规则 2：Service 必须无状态单例

继承 ServiceBase 即自动注册进容器，通过类型注解注入：

```
class UserController(Controller):
    prefix = "/user"; tags = ["用户"]
    svc: UserService          # 自动注入，无需 __init__
```

请求级数据（当前用户、trace_id、tenant_id）绝不允许写成实例属性，请走方法参数或 ContextVar。否则第一个并发请求就会污染第二个。

### 规则 3：存储后端必须显式配置

未配置 OSS 时下载接口返回 **500 + 明确文案**，而不是静默退化到内存读文件再 OOM。这是故意设计的不友好——它逼你在上线前把存储配好。

### 规则 4：业务状态码集中管理，HTTP code 与 body.code 两层分工

只用 HTTP code 表业务状态会冲突：HTTP 常用码约 30 个，业务错误可能上百种（余额不足/库存不足/超限购在 HTTP 里没有对应语义）；且 503 既可能是业务"维护中"也可能是网关真实故障。框架因此采用国内主流的**两层模型**：

| 层 | 职责 | 使用者 |
|---|---|---|
| HTTP status | 协议大类：200/201 成功，400/401/403/404/409/422/429，5xx 故障 | 网关重试熔断、错误率监控、通用拦截器 |
| body.code | 细粒度业务状态：成功恒 `0`，失败为字符串码（`USERNAME_TAKEN`） | 前端业务分支、精细化提示 |

每个业务码在码册中一次性声明「HTTP 大类 + 默认文案」，全局查重：

```
# app/biz_code.py —— 业务码集中定义处
class UserCodes(BizCodes):
    USERNAME_TAKEN = BizCode(409, "用户名已被占用")

# Service：只引用，不重写
raise BizError(UserCodes.USERNAME_TAKEN)

# Controller：声明本接口可能抛出的码 → 自动进入 /docs 响应码表
@Controller.post("/")
@raises(UserCodes.USERNAME_TAKEN)
async def create(...): ...
```

协议级错误用框架内置协议码（`NOT_FOUND` / `UNAUTHORIZED` / `FORBIDDEN` /
`TOO_MANY_REQUESTS` / `VALIDATION_ERROR` / `INVALID_CURSOR` / `INTERNAL_ERROR`），
业务码只表业务规则拒绝——两套码各守边界，不要求数字相等。

## 七、常用模块用法

### 7.1 分页

```
class UserPageQuery(PageQuery):
    keyword: str = ""

async def page(self, db: AsyncSession, q: UserPageQuery) -> Page[UserVO]:
    rows = (await db.execute(stmt.offset(q.offset).limit(q.page_size))).scalars().all()
    total = (await db.execute(select(func.count(UserDO.id)))).scalar() or 0
    return Page.of(rows_vo, q.page, q.page_size, total)
```

page_size 上限硬编码 200，防止前端传 10^6 拖死数据库。

### 7.2 分布式锁

```
async with DistributedLock(f"order:{order_id}", ttl=30):
    ...
```

基于 Redis SET NX EX + Lua 释放，持有者校验防止误删他人锁。注意 ttl 必须大于业务最坏耗时，否则锁会在执行中途过期。

### 7.3 声明式缓存

```
@cached("user:{user_id}", ttl=300)
async def get_user(user_id: int): ...
```

key 按 format 拼装，value 经 JSON 序列化。返回值必须是 JSON 可序列化的，pydantic 模型请先 .model_dump()。

### 7.4 文件（上传预签名直传 + 三套下载链路）

**下载**——一套接口，按策略选择链路：

```
return SgFileResponse(request=request, oss_key="reports/a.xlsx", filename="报表.xlsx")
```

| 策略 | 行为 | 适用 |
|---|---|---|
| oss | 应用通过共享 aiobotocore client 流式代理，支持 Range 断点续传与限速 | 默认/通用；客户端无需能直连对象存储 |
| nginx | X-Accel-Redirect 内部重定向，由 Nginx 送文件 | 内网/合规场景，可控限速 |
| python | 应用进程直读本地磁盘直发 | 本地开发兜底，超 100MB 主动 413 拒绝 |

auto（默认）：有 oss_key 且后端配置完整 → oss；只有本地文件 → python。

**上传**——推荐预签名直传（应用零带宽），demo 控制器 `FileController` 是完整范例：

| 端点 | 用途 |
|---|---|
| POST /file/upload/presign | 取上传预签名；大文件自动返回分片计划 |
| POST /file/upload/complete | 完成分片上传 |
| POST /file/upload/abort | 取消分片并清理 |
| GET /file/download/presign | 取下载预签名 URL |

注意 `FileService` 持有后端连接、不是无状态单例，不能靠 ServiceBase 自动注册：
在 bootstrap 前调用 `app/storage_setup.py` 完成装配（main.py 里有完整时序注释）。

### 7.5 日志与 trace

每条日志自带 trace_id：

```
2026-09-25 14:32:01 | INFO  | a1b2c3d4e5f6... | sg.http:42 | POST /user 200 87ms
2026-09-25 14:32:01 | WARN  | a1b2c3d4e5f6... | sg.sql.slow:18 | [SLOW] 1204ms | sql=SELECT ...
```

客户端可在请求头带 X-Trace-Id 实现全链路串联（网关→后端→下游）。

切 log.format: json 后直接对接 Loki/ELK，字段含 ts/level/trace_id/span_id/logger/line/msg/exc。

### 7.6 认证（JWT + HttpOnly Cookie）

demo 的 `AuthController` 给出完整认证闭环：

| 端点 | 用途 |
|---|---|
| POST /auth/login | 登录；access + refresh 同时走响应体和 HttpOnly Cookie |
| POST /auth/refresh | refresh cookie 换新 access |
| POST /auth/logout | 吊销令牌（Redis 黑名单）+ 清 Cookie |
| GET /auth/me | 当前登录用户（以 DB 最新数据为准） |

需要登录的接口直接声明依赖：

```
async def me(self, user: TokenPayload = Depends(current_user)): ...
```

安全要点：

- `JwtAuthMiddleware` 放在 Trace 之内（见 main.py），认证失败也有 trace_id。
- 中间件对每个令牌查 Redis 吊销名单——登出即时生效，而不是等 access 自然过期。
- 登录接口挂 `LoginGuard`：失败计数 + 锁定 + 递增延迟，防暴力破解；仅凭证失败计数，DB 故障不锁人。
- 密码统一 Argon2id 哈希（`sagitta.Security.hash`），明文只在 DTO 短暂存在。
- prod 必须 `security.cookie_secure=true`。

### 7.7 限流与请求体限制

全局限流基于 Redis ZSET 滑动窗口，三层维度同时检查：

| 维度 | 默认配额 | 用途 |
|---|---|---|
| per-IP | 60 次/60 秒 | 防爬虫、防单 IP 滥用 |
| per-user | 200 次/60 秒 | 防已登录用户滥用 |
| global | 100 次/60 秒 | 全局热点保护 |

Redis 不可用时降级为不限流（不阻塞业务）。限流触发返回 429 + 统一响应壳。

请求体大小限制检查 Content-Length，超限返回 413。配置 `server.max_body_bytes`。

### 7.8 Prometheus 指标

GET /metrics 返回 Prometheus 文本格式，无需额外依赖：

| 指标 | 类型 | 维度 |
|---|---|---|
| http_requests_total | Counter | method, route, status |
| http_request_duration_ms | Histogram | method, route |
| http_requests_in_progress | Gauge | method |
| db_pool_checkedout | Gauge | engine |
| db_pool_size | Gauge | engine |
| redis_commands_total | Counter | command |
| redis_command_duration_ms | Histogram | command |

### 7.9 权限系统

三层权限模型：

| 层级 | 用法 | 说明 |
|---|---|---|
| 角色级 | `require_role("admin")` | 粗粒度，判断角色 |
| 资源级 | `require_permission("user:write")` | 细粒度，按动作权限。支持通配符 `user:*`、`*` |
| 数据级 | `require_data_scope("owner")` | 行级权限，自动附加 WHERE 条件 |

权限通过 PermissionResolver 解析，默认从 JWT roles 映射。宿主可注册自定义 resolver 对接 Casbin / ACL 表：

```
from sagitta.Security.permissions import set_permission_resolver, PermissionResolver

class CasbinResolver(PermissionResolver):
    def resolve(self, user) -> set:
        # 对接 Casbin，返回权限集合
        ...

set_permission_resolver(CasbinResolver())
```

数据级权限（DataScope）自动向查询附加 WHERE 条件，而非查出全表再过滤：

```
@Controller.get("/article/page")
async def page(self, db = Depends(session_dep()),
               scope: DataScope = Depends(require_data_scope("owner"))):
    stmt = select(ArticleDO)
    stmt = scope.apply_filter(stmt, ArticleDO)  # WHERE user_id = :current_user
    ...
```

## 八、数据库迁移

框架不猜宿主的模型包，autogenerate 前必须告诉它 DO 在哪：

```
# PowerShell
$env:SG_ALEMBIC_MODEL_MODULES="app.do"
alembic revision --autogenerate -m "add user.status"
alembic upgrade head
alembic downgrade -1          # 回滚
alembic downgrade base        # 回滚到空库
```

注意：

- alembic.ini 必须保持纯 ASCII——Alembic 按系统区域编码（中文 Windows 为 GBK）读取它，非 ASCII 注释会直接解析失败。
- 已开启 compare_type=True 与 compare_server_default=True，列类型和默认值变更会被检测到。
- 仓库自带 baseline 迁移（app_user 表），已验证 upgrade/downgrade/upgrade 循环。

生产环境建议生成 SQL 后人工 review 再执行，不要直接 upgrade。

切换数据库类型（mysql ↔ postgresql）时，需同步修改 db.default.type 与已有 migration 里的方言特定语法（如自增主键、JSON 类型、ILIKE）。

## 九、接入现有 FastAPI 项目

> 混用分级（0~3 级）与适用场景见 [使用指南 · 第三、四章](docs/guide.md)；本节给出落地清单。

1. 把 sagitta 作为依赖包安装（`pip install -e .` 或发布到私有源），**不要把 sagitta/ 拷贝进业务目录**。
2. 保留你现有的 do/dto/vo/services/controllers 目录结构，让类继承框架基类即可被自动发现；暂不改造的路由可作为原生路由与 Controller 共存（指南 3.3）。
3. **优先改造的部分**（按收益排序）：
   - 统一响应壳 → 消除前后端对 code/message 的歧义，收益立竿见影
   - DO 不越界 → 解决 N+1 与脏更新的最主要来源
   - 慢 SQL 与连接池监控 → 上线第一周就能抓到问题
   - trace_id 贯通 → 后续所有排查的前提
4. MySQL → PostgreSQL 方言适配：AUTO_INCREMENT、反引号、ON DUPLICATE KEY、JSON 列需改为 IDENTITY/SERIAL、双引号、ON CONFLICT、JSONB。使用 pgvector 时需在库内先 `CREATE EXTENSION vector`。

## 十、常见问题

**Q：为什么不用 Depends 而要用 Controller 基类 + 自动挂载？**

A：Depends 完全可用（session_dep() 就是 Depends），自动挂载只是省去手动 include_router 的重复劳动。你想手写 router 也完全可以。

**Q：能不能同时用 MySQL 和 PostgreSQL？**

A：可以。db 下配多个 key，各自指定 type，驱动会自动按 type 分支选择 asyncpg 或 asyncmy。

**Q：Qdrant 是同步客户端，会阻塞事件循环吗？**

A：会。框架侧只负责初始化和探活，业务侧请用 anyio.to_thread.run_sync 包一层，或单独线程池。

**Q：AGPL 传染性？**

A：Sagitta 本身采用 MIT 协议。MinIO 服务端以 AGPL 分发，内部自用无碍；若对外交付 SaaS 或分发镜像，请自行评估合规风险。

**Q：名字怎么念？**

A：sa-GIT-ta（拉丁语「箭」）。中文叫「蛇鹫」，也叫秘书鸟。

## 十一、版本与约定演进

| 版本 | 状态 |
|---|---|
| 0.1.0 | bootstrap / 响应自动包装 / 两种分页 / 文件预签名直传与流式下载 / 日志 / 慢SQL / Redis锁 / JWT 认证闭环 / OpenAPI Resp[T] / pyproject 包化 / baseline 迁移 / 六层分层配置（SG_PROFILE 自由链 + .env.example） |
| 0.2.0 | 全局限流中间件 / 请求体大小限制 / Prometheus 指标（/metrics） / 优雅关闭与连接排空 / API 版本控制（Controller.version） / 权限系统（PermissionResolver + DataScope） / 配置类型校验 / 75 个单元测试 / SgError.to_json_response() 中间件安全 |
| 待办 | STS 临时凭证、Qdrant 异步包装层、refresh token 轮换与重放防护、WebSocket 支持、事件总线 |

**命名回溯**：本骨架前身曾用名 obfastapi（OceanBase 时期缩写）、keystone（因与 OpenStack Identity 撞名废弃）。若你在旧日志或 CI 里看到 ob. 命名空间或 OBResponse，均为历史残留，请替换为 sg. / Sg*。

## 十二、贡献

- 框架层的改动请附带理由：**这条规则拦住了什么真实的坑？**
- 业务特定的逻辑请留在 app/，不要往 sagitta/ 里塞。
- 提交前跑一遍清零验证：grep -rn "obfastapi|OBResponse|\"ob\." 应无命中。
