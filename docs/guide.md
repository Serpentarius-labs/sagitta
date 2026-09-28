# Sagitta 使用指南

本指南回答三个问题：**怎么用框架开发一个完整功能、怎么和原生 FastAPI 混用、什么时候该用 / 不该用。**

> 阅读约定：[README](../README.md) 是「契约与速查」（规则、配置项、API 片段），本指南是「教程与决策」（连贯动线、混用边界、取舍依据）。两者交叉引用，不重复展开。
> 配置文件的分层、profile 与多环境抉择见 [配置管理指南](config.md)。

---

## 一、心智模型：框架在 FastAPI 的哪一层

### 1.1 它是约定层，不是替代品

```
  你的业务代码        app/ （DO / DTO / VO / Service / Controller）
  ─────────────────────────────────────────────────────────────
  Sagitta 约定层      装配自动化 · 分层强制 · 基础设施托管
  ─────────────────────────────────────────────────────────────
  FastAPI             路由 / 依赖注入 / 校验 / OpenAPI
  Starlette           Request / Response / Middleware / WebSocket
  ASGI                异步服务接口
```

框架**只做三类事**：

| 动词 | 内容 | 不做会怎样 |
|---|---|---|
| **装配** | `bootstrap()` 一键组装 CORS、中间件、异常处理、健康探针、控制器挂载、生命周期 | 每个项目重复抄一遍启动代码，还抄得不一样 |
| **强制** | DO 不出 Service、endpoint 不手写响应壳、业务码集中注册 | 全靠 code review 自觉，约束随项目老化失效 |
| **托管** | DB/Redis/Qdrant 的连接池在统一 lifespan 初始化与释放 | 多个自建 engine 生命周期失控，测试难以隔离 |

除此之外的一切——路由声明、参数校验、依赖注入、OpenAPI——**都是原生 FastAPI，原样可用**。

### 1.2 接管边界总表

| 能力 | 原生 FastAPI 写法 | 框架写法 | 能否混用 |
|---|---|---|---|
| 路由 | `@app.get` / `APIRouter` | `Controller` 子类自动挂载 | ✅ 两者可共存 |
| 依赖注入 | `Depends()` | `ServiceBase` + 类型注解 | ✅ 各管一层，见 3.5 |
| 入参校验 | pydantic 模型 | DTO（仍是 pydantic） | — 同一件事 |
| 出参结构 | 任意返回 | VO + 自动套壳 | ✅ 可关闭套壳 |
| 异常 | `HTTPException` | `BizError(业务码)` / 协议异常 | ✅ 都会被统一套壳 |
| 中间件 | ASGI 中间件 | `middlewares=` 传入 | ✅ 完全一致 |
| 生命周期 | `lifespan` | `make_lifespan` + 钩子 | ✅ 可独立使用 |
| WebSocket | `@app.websocket` | 无封装 | 走原生 |

---

## 二、标准开发动线：一个功能从 0 到上线

以「文章（Article）」为例，走完整十步。记住顺序背后的依赖方向：**DO → DTO/VO/业务码 → Service → Controller，依赖只能向内，不能反向。**

### 步骤 1：定义 DO（数据对象）

`app/do/article_do.py`

```python
from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from sagitta.Database.base import BaseDO   # 需要软删除则用 SoftDeleteDO


class ArticleDO(BaseDO):
    __tablename__ = "app_article"

    title: Mapped[str] = mapped_column(String(200))
    content: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="draft")
```

**DO 是数据库行的映射，只允许活在 Service / Repository 内**，这是整套分层的核心约束。

### 步骤 2：生成迁移

```powershell
$env:SG_ALEMBIC_MODEL_MODULES = "app.do"
alembic revision --autogenerate -m "add article"
alembic upgrade head
```

改表结构走迁移，不要用 `create_all` 凑——后者无法演进，详见 [README 第八章](../README.md#八数据库迁移)。

### 步骤 3：定义 DTO（入参）

`app/dto/article_dto.py`

```python
from pydantic import BaseModel, Field


class ArticleCreateDTO(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1)


class ArticleUpdateDTO(BaseModel):
    title: str | None = Field(None, max_length=200)
    content: str | None = None
```

DTO 回答「**外界能传给我什么**」，校验规则在这里一次性写清。

### 步骤 4：定义 VO（出参）

`app/vo/article_vo.py`

```python
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class ArticleVO(BaseModel):
    model_config = ConfigDict(from_attributes=True)  # 允许从 DO 直接转换

    id: UUID
    title: str
    status: str
    created_at: datetime
```

VO 回答「**我允许外界看到什么**」。内部字段（更新时间、软删标记、外键）不写进去就天然不会泄漏。

### 步骤 5：登记业务码

`app/biz_code.py`（在现有码册中追加）

```python
class ArticleCodes(BizCodes):
    ARTICLE_NOT_DRAFT = BizCode(409, "仅草稿状态可编辑")
```

码值、HTTP 大类、文案在此一处声明，全局查重。规则见 [README 规则 4](../README.md#规则-4业务状态码集中管理http-code-与-bodycode-两层分工)。

### 步骤 6：Repository（按需）

简单 CRUD 可直接用框架 `BaseRepository` 基类的通用方法；有自定义 SQL 时才建子类。**SQL 只出现在这一层**：

`app/do/article_repo.py`

```python
from sagitta.Database.repository import BaseRepository
from demo.do.article_do import ArticleDO


class ArticleRepository(BaseRepository[ArticleDO]):
    model = ArticleDO
```

### 步骤 7：写 Service（业务逻辑）

`app/services/article_service.py`

```python
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from sagitta.Core.errors import BizError, NotFoundError
from sagitta.Core.inject import ServiceBase

from demo.biz_code import ArticleCodes
from demo.do.article_do import ArticleDO
from demo.do.article_repo import ArticleRepository
from demo.dto.article_dto import ArticleCreateDTO
from demo.vo.article_vo import ArticleVO


class ArticleService(ServiceBase):
    # 无状态单例：db、当前用户一律走方法参数，绝不写 self.xxx

    async def create(self, db: AsyncSession, dto: ArticleCreateDTO) -> ArticleVO:
        row = ArticleDO(title=dto.title, content=dto.content)
        await ArticleRepository(db).add(row)  # flush，不 commit
        await db.refresh(row)
        return ArticleVO.model_validate(row)

    async def publish(self, db: AsyncSession, article_id: UUID) -> ArticleVO:
        row = await ArticleRepository(db).get(article_id)
        if row is None:
            raise NotFoundError()  # 协议错误：404 / NOT_FOUND
        if row.status != "draft":
            raise BizError(ArticleCodes.ARTICLE_NOT_DRAFT)  # 业务拒绝：409
        row.status = "published"
        await db.flush()
        return ArticleVO.model_validate(row)
```

三条铁律（理由见 [README 规则 2](../README.md#规则-2service-必须无状态单例)）：不写 `commit`、SQL 交给 Repository、返回前转 VO。

### 步骤 8：写 Controller（路由）

`app/controllers/article_controller.py`

```python
import uuid

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from sagitta.Core.biz_code import raises
from sagitta.Database.session import session_dep
from sagitta.Flow.controller import Controller

from demo.biz_code import ArticleCodes
from demo.dto.article_dto import ArticleCreateDTO
from demo.services.article_service import ArticleService
from demo.vo.article_vo import ArticleVO

_db = Depends(session_dep())


class ArticleController(Controller):
    prefix = "/article"
    tags = ["文章"]

    svc: ArticleService  # 自动注入

    @Controller.post("/", status_code=201, summary="创建文章")
    async def create(self, dto: ArticleCreateDTO, db: AsyncSession = _db) -> ArticleVO:
        return await self.svc.create(db, dto)

    @Controller.post("/{article_id}/publish", summary="发布文章")
    @raises(ArticleCodes.ARTICLE_NOT_DRAFT)  # → OpenAPI 409 码表
    async def publish(self, article_id: uuid.UUID, db: AsyncSession = _db) -> ArticleVO:
        return await self.svc.publish(db, article_id)
```

Controller 只收 DTO、调 Service、**返回裸数据**（框架自动套壳）。

### 步骤 9：在入口登记

`main.py` 的 `CONTROLLER_MODULES` 加一行：

```python
CONTROLLER_MODULES = [
    "app.controllers.user_controller",
    "app.controllers.auth_controller",
    "app.controllers.article_controller",   # ← 控制器要被 import 才会登记
]
```

### 步骤 10：验证

1. 打开 `/docs`，确认路由、`Resp[ArticleVO]` 壳结构、409 业务码表都在；
2. 走一遍创建 → 发布 → 再发布（第二次应得 409 + `ARTICLE_NOT_DRAFT`）；
3. 必要时把该领域的断言补进 selfcheck 或 tests。

**完成后，一个请求的完整流转是：**

```
HTTP 请求
 → TraceMiddleware（分配 trace_id）
 → JwtAuthMiddleware（解析令牌，如需要）
 → Controller（DTO 校验，参数注入）
 → Service（业务规则，事务边界）
 → Repository（SQL）
 → DO（只在内部）
 → VO 裸数据返回
 → 自动套壳：HTTP 200/201 + body {code: 0, message, data}
```

---

## 三、与 FastAPI 混用

框架不是全有或全无。按接受程度分四级，你可以停在任何一级。

### 3.1 四个混用级别

| 级别 | 做法 | 得到什么 | 适用 |
|---|---|---|---|
| **0** | 纯原生 `FastAPI()` | 完全自由，零约定 | 脚本、极小服务 |
| **1** | 原生 app + `make_lifespan` | 配置 / 日志 / DB / Redis 基础设施托管 | 想自己管路由的项目 |
| **2** | `bootstrap` + 原生路由共存 | 全套装配，部分路由保持原生 | 渐进迁移、特殊接口 |
| **3** | `bootstrap` + `Controller` 体系 | 全部约定被强制 | 标准用法（推荐） |

### 3.2 级别 1：原生 FastAPI，只借基础设施

```python
from fastapi import FastAPI

from sagitta.app.lifespan import make_lifespan
from sagitta.Config.setting import get_settings

# 若用到安全功能，先于一切 import 初始化主密钥：
# from sagitta.Security.vault import Vault; Vault.init()

app = FastAPI(lifespan=make_lifespan(settings_provider=get_settings))


@app.get("/ping")
async def ping():
    return {"pong": True}  # 不套壳、不查 DO，完全原生
```

这样 DB 连接池、Redis、日志、慢 SQL 监听都由框架生命周期托管，但路由完全是你自己的。需要健康探针可再调 `register_health(app)`。

### 3.3 级别 2：框架应用中挂原生路由

`bootstrap()` 返回的就是标准 `FastAPI` 实例，随时可以 `include_router`：

```python
from fastapi import APIRouter
import main   # 框架已装配好的 app

raw = APIRouter(prefix="/raw", tags=["原生接口"])


@raw.get("/ping")
async def ping():
    return {"pong": True}      # ← 原生路由：不套壳、不受 DO 守卫约束


main.app.include_router(raw)
```

**边界要清楚**：原生路由的返回值长什么样、会不会泄漏内部字段，全由你自己负责——框架的两道守卫只作用于 `Controller` 挂载的路由。

典型用途：第三方回调（对方规定了报文格式，不能套壳）、遗留接口、WebSocket 握手端点。

### 3.4 框架控制器里，原生 Depends 照常工作

这不是例外，而是常态——`session_dep()` 本身就是 `Depends`：

```python
from fastapi import Depends

@Controller.get("/mine")
async def mine(
    self,
    db: AsyncSession = Depends(session_dep()),
    user: TokenPayload = Depends(current_user),   # 原生依赖链
) -> list[ArticleVO]:
    ...
```

FastAPI 的依赖缓存、`yield` 资源清理、嵌套依赖全部原样生效。

### 3.5 两套 DI 的分工：Depends vs 框架容器

两者不冲突，因为**作用域不同**：

| | FastAPI `Depends` | 框架容器（`ServiceBase` / `Inject`） |
|---|---|---|
| 作用域 | **请求级** | **进程级单例** |
| 触发 | 路由参数解析 | 类属性 / 类型注解 |
| 适合 | db 会话、当前用户、请求级资源、分页参数 | 无状态业务服务、有状态组件（如 FileService） |
| OpenAPI | 能识别 | 不体现在文档 |
| 资源清理 | 支持 `yield` | 走 shutdown 钩子 |

判断口诀：**「这个对象的生命周期跟请求走，还是跟进程走？」**——跟请求 → Depends；跟进程 → 容器。不要用单例持有会话，也不要每个请求 new 一个 Service。

### 3.6 原生能力兼容矩阵

| 原生能力 | 怎么用 | 注意 |
|---|---|---|
| **WebSocket** | `@app.websocket("/ws")` 或原生 `APIRouter` | Controller 不封装 WS；WS 鉴权需自己做（HTTP 中间件不覆盖） |
| **BackgroundTasks** | 路由参数加 `tasks: BackgroundTasks` | 任务在响应后执行；重活用独立队列而非依赖进程存活 |
| **静态文件** | `app.mount("/static", StaticFiles(directory=...))` | 与框架路由不冲突 |
| **ASGI 中间件** | `bootstrap(middlewares=[(Cls, {...})])` 或 `app.add_middleware` | 见 README 中间件顺序说明 |
| **异常** | 继续抛 `fastapi.HTTPException` | 会被捕获并统一套 `{code, message, data}` |
| **自定义异常处理** | `app.add_exception_handler(MyExc, handler)` | 在 bootstrap 之后注册可覆盖默认处理 |
| **response_model / status_code / tags / dependencies** | 路由装饰器参数原样写 | 框架不拦截这些参数 |
| **多应用挂载** | `app.mount("/sub", sub_app)` | 子应用有自己的路由表与中间件 |

### 3.7 混用的禁区

1. **不要自建第二套 engine / Redis 连接**又同时启用框架托管——两套生命周期，测试和关闭都会漏。要自定义连接参数就改配置，而不是绕过框架。
2. **不要在框架控制器里手写响应壳**（`return {"code": ..., "data": ...}`）——守卫会直接拒绝。需要自定义响应时返回 `SgResponse(...)`，或把该接口拆成原生路由。
3. **不要另写一个 lifespan 替换 `make_lifespan`**——会绕过全部基础设施初始化与优雅关闭。要扩展就用 `on_startup` / `on_shutdown` 钩子。
4. **不要让 DO 出现在原生路由上还以为有守卫兜底**——原生路由没有这层保护，需自觉转 VO。

---

## 四、如何抉择

### 4.1 三问决策

```
Q1. 这个项目会被多人长期维护吗？
    否（一次性脚本 / 原型 / 单文件）         → 级别 0，原生 FastAPI
    是                                      → 继续

Q2. 你需要团队统一的分层与响应约定吗？
    不需要，且想保留最大自由度               → 级别 0~1
    需要                                    → 继续

Q3. 现有代码能接受渐进改造吗？
    能                                      → 级别 2 渐进，最终到 3
    全新项目                                → 直接级别 3
```

### 4.2 项目形态对照

| 形态 | 建议 | 原因 |
|---|---|---|
| 微服务集群中的业务服务 | **级别 3** | 统一约定在跨服务协作中价值最大，trace/码规范可直接贯通 |
| 内部管理后台 / 中后台 | **级别 3** | CRUD 多、生命周期长，分层约束防止劣化 |
| 创业期快速迭代产品 | 级别 2~3 | 先用框架搭骨架，个别特殊接口走原生 |
| 对外公开 API（强 REST 契约） | 级别 2，关 `wrap_response` | 外部消费者可能不接受自定义壳；保留基础设施与文档能力 |
| POC / Demo / 脚本 | 级别 0 | 约定的收益抵不上学习成本 |
| 以 WebSocket / GraphQL 为主 | 级别 0~1 | 框架的 HTTP 分层约束帮助有限 |

### 4.3 已有 FastAPI 项目：渐进接入五步

不需要推倒重来，按收益排序逐步引入：

1. **接配置与日志**：改用 `get_settings()` + `make_lifespan`（级别 1），立刻获得 trace_id 贯通；
2. **接异常壳**：用 `bootstrap()` 或注册框架异常处理器，统一错误响应；
3. **收敛数据访问**：把散落的 SQL 归入 Repository，engine 交给框架托管；
4. **Service 化**：业务逻辑抽成无状态 `ServiceBase`，DO 转 VO 后返回；
5. **控制器迁移**：逐块把 `APIRouter` 改成 `Controller` 子类（级别 2 → 3）。

每一步都可独立上线、独立回滚。落地清单见 [README 第九章](../README.md#九接入现有-fastapi-项目)。

### 4.4 「关掉某个特性」优先于「放弃框架」

对某条约定不爽时，先看能否局部关闭——框架的大多数约束都是开关：

- 不想要响应壳 → `app.wrap_response: false`（仍保留日志、DB、守卫）；
- 临时排查 DO → `app.check_do_leak: false`；
- 不要 CORS → `server.cors_origins: []`；
- 不用文件 / 向量库 → `FILE_ENABLED = False` / `qdrant.enabled: false`；
- 完全不想自动挂路由 → `bootstrap(mount_controllers=False)`，自己 include。

完整开关见第五章。

### 4.5 该离开框架的信号

出现以下情况，说明框架与你的核心诉求不匹配，退回原生（或级别 1）是诚实的选择：

- 你的服务以 WebSocket / SSE 长连接 / GraphQL 为核心，HTTP 分层帮助有限；
- 响应格式被外部契约严格规定（如支付回调、对接遗留系统），且大部分接口都无法套壳；
- 框架的强制约定频繁与真实业务冲突，以至于你一直在写「绕过」代码；
- 你只需要一两个工具函数，引入整套约定得不偿失。

**判断标准**：如果框架每周帮你拦住的坑，少于它每周给你制造的摩擦，就该降低使用级别。

---

## 五、特性开关总表

| 特性 | 关闭方式（配置） | 关闭方式（代码） | 关闭后 |
|---|---|---|---|
| 响应自动套壳 | `app.wrap_response: false` | `bootstrap()` 后路由不受影响；仅对 Controller 生效 | endpoint 返回值原样序列化 |
| DO 泄漏守卫 | `app.check_do_leak: false` | — | 不扫描返回值，需自律 |
| CORS | `server.cors_origins: []` | `bootstrap(cors_origins=[])` | 不装配 CORS 中间件，浏览器默认同源限制 |
| 健康探针 | — | 自己 bootstrap 后不调 `register_health` | 无 /health /ready |
| 控制器自动挂载 | — | `bootstrap(mount_controllers=False)` | 手动 include_router |
| 文件能力 | — | `main.py` 中 `FILE_ENABLED = False` | 文件控制器与装配一并移除 |
| Qdrant | `qdrant.enabled: false` | — | 不初始化向量库 |
| trace 注入到 body | `app.echo_trace_in_body: false` | — | trace_id 只在响应头与日志中 |

> 注意：CORS 设为 `["*"]` 时必须同时 `cors_allow_credentials: false`，否则浏览器拒绝带 Cookie 的请求；详见 README 配置说明。

---

## 附录：最小可运行骨架

一个只含「健康检查 + 一个原生路由」的最小项目：

```
myapp/
├── .env                     # SG_MASTER_KEY 等（可选）
├── conf/config.yaml         # 数据源 / Redis 配置
└── main.py
```

```python
# main.py
from fastapi import FastAPI
from sagitta.app.lifespan import make_lifespan
from sagitta.Config.setting import get_settings
from sagitta.app.bootstrap import register_health

app = FastAPI(lifespan=make_lifespan(settings_provider=get_settings))
register_health(app)


@app.get("/ping")
async def ping():
    return {"pong": True}
```

在此骨架上，按第三章逐级引入约定，或按第二章标准动线添加业务领域即可。
