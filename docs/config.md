# 配置管理指南

> 本篇讲清楚四件事：配置按什么原则分类、六层优先级如何工作、各场景怎么选、以及从本地开发到 Docker / Kubernetes / GitOps 的演进路径。

---

## 一、第一性原理：按「性质」分类，而不是按「环境」分类

多数配置混乱的根源，是一上来就按 dev / test / pre / prod 切目录——这隐含两个错误假设：**环境名是固定的**、**环境是配置唯一的变化轴**。真实情况是：

- 团队的发布路径千差万别：dev → staging → canary → prod；或每个特性分支一套环境；或蓝绿两套并存；
- 变化轴不止环境：地域（cn-north / us-east）、实例角色（api / worker）、集群、租户。

正确切法是先按**配置的性质**分三类，再决定每类的载体：

| 类别 | 特征 | 典型项 | 载体 | 入 Git？ |
|---|---|---|---|---|
| **静态配置** | 随代码发布、非敏感、改动要走评审 | 功能开关、连接池参数、日志格式、分页默认值、域名 | YAML 文件 | ✅ 入库 |
| **部署密钥** | 每个部署不同、**敏感** | DB 密码、主密钥、access/secret key | 环境变量 / Secret | ❌ 绝不入库 |
| **动态配置** | 不重新发版就要改 | 熔断开关、限流阈值、灰度名单 | 配置中心（未来，可选） | — |

**「环境」只是选择哪一组值的标签，不应该决定目录结构。**框架提供机制（分层 + profile 选择器），环境叫什么名字、有多少个，完全由你的团队定义。

---

## 二、六层优先级

最终生效的配置由六层叠加而成，**后者覆盖前者**：

```
优先级（低 → 高）
┌──────────────────────────────────────────────────────────────┐
│ 1. 代码默认值        DEFAULTS（sagitta/Config/loader.py）     │
│ 2. 基线配置          conf/config.yaml                         │
│ 3. Profile 覆盖层    conf/config.<profile>.yaml（可多个）     │
│ 4. 个人覆盖          conf/config.local.yaml                   │
│ 5. .env 文件         项目根/.env（仅本地开发便利件）          │
│ 6. 真实环境变量      SG_*（CI、Docker、K8s Secret/ConfigMap） │
└──────────────────────────────────────────────────────────────┘
```

设计要点：

- **同一份配置项永远只有一个最终值**，但你可以在任何一层覆盖——不需要在每个环境文件里抄全量配置，只写差异；
- **1、2 层保证「缺配置不崩」**：新 clone 的仓库什么都不配也能启动（连本地容器即可）；
- **第 6 层最高**：这是 12-factor 的核心，也是 K8s 的注入方式。`.env` 加载时 `override=False`，**真实环境变量永远赢过文件**；
- 启动日志会打印实际生效的 profile、加载了哪些文件（只含路径，不含值）：
  ```
  [config] profiles=['prod'] files=['.../conf/config.yaml', '.../conf/config.prod.yaml'] dotenv=[]
  ```

### 2.1 环境变量命名

所有环境变量以 `SG_` 前缀，按下划线展开为嵌套路径：

| 环境变量 | 命中路径 |
|---|---|
| `SG_DB_DEFAULT_HOST=10.0.0.1` | `db.default.host` |
| `SG_LOG_LEVEL=INFO`（扁平，全库唯一时可省略节名） | `log.level` |
| `SG_APP_DEBUG=true` | 字符串自动按默认值类型转 bool/int/float/list |

列表用逗号分隔：`SG_SERVER_CORS_ORIGINS=https://a.com,https://b.com`。

---

## 三、Profile 机制：自由命名 + 链式继承

### 3.1 基本用法

```powershell
$env:SG_PROFILE = "prod"
```

加载时若存在 `conf/config.prod.yaml` 就深合并进基线；**文件不存在则静默跳过**——profile 层永远是可选的。

### 3.2 链式继承（逗号分隔）

```powershell
$env:SG_PROFILE = "staging,canary"
```

按左 → 右顺序加载，**右侧覆盖左侧**。上例的含义：先应用 staging 的通用差异，再用 canary 的差异覆盖。典型用途：

```
config.yaml            基线（含本地容器端口等开发默认）
config.staging.yaml    预发通用：json 日志、预发域名
config.canary.yaml     金丝雀实例：指向灰度依赖
```

### 3.3 名字完全自定义

框架不校验 profile 名，因为变化轴由你定义：

| 你的诉求 | SG_PROFILE 写法 |
|---|---|
| 传统四环境 | `dev` / `test` / `staging` / `prod`（各自一个文件） |
| 按地域切 | `region-cn-north` / `region-us-east` |
| 环境 + 角色 | `prod,role-worker`（链表达正交维度的叠加） |
| 特性分支环境 | `feat-xxx`（分支销毁时文件一起删） |

> **正交维度用链，不要用笛卡尔积命名**：是 `prod,region-cn-north,role-worker`，而不是维护一个 `prod-cn-north-worker.yaml`。

### 3.4 `SG_APP_ENV` 与 `SG_PROFILE` 的区别

这是最容易混淆的点，刻意分开：

| | `SG_APP_ENV`（app.env） | `SG_PROFILE` |
|---|---|---|
| 本质 | 一个**标签** | 文件**选择器** |
| 用途 | 日志、/health、监控聚合 | 决定加载哪些 yaml 覆盖层 |
| 默认值 | `dev` | 空（不加载任何覆盖层） |
| 框架是否解释其值 | 否 | 是（但只当文件名，不解释语义） |

通常两者取相同值，在部署脚本里一起设；但当你需要「环境标签是 prod，实际叠加的是 prod + canary 两层」时，分开的价值就体现出来了。

---

## 四、.env 工作流与 Git 边界

### 4.1 三条铁律

1. **真实值文件永不入库**：`.env` 及 `.env.*` 已被 .gitignore 过滤；
2. **样例必须入库**：[.env.example](../.env.example) 随仓库分发，新人两步上手：
   ```powershell
   Copy-Item .env.example .env
   # 编辑 .env，填入本机主密钥
   ```
3. **profile yaml 入库但只放非敏感项**：敏感项即使在私有仓库也不写——`git commit` 历史几乎无法彻底清除。结构参考 [conf/config.prod.yaml.example](../conf/config.prod.yaml.example)。

### 4.2 为什么本地需要 .env，容器里却不需要

- 本地开发：`.env` 让你不用每次开终端都 export 一串变量；
- Docker / K8s：环境变量由容器运行时 / Pod 规格注入，`.env` 文件根本不存在，加载逻辑自然 no-op。

`.env` 是开发便利件，不是配置的主通道——主通道永远是环境变量。

### 4.3 个人覆盖：config.local.yaml

临时要改一个结构性配置（如把日志级别调成 DEBUG、换本地端口）但不想动入库文件、也不想污染 `.env` 时，用 `conf/config.local.yaml`。它被 gitignore，优先级高于所有入库 yaml。适合：

- 你本机的依赖跑在非标准端口；
- 排查问题时的临时开关；
- 紧急热修时的本机验证。

---

## 五、各场景如何抉择

| 场景 | 你需要动的层 | 说明 |
|---|---|---|
| **新人第一次跑 demo** | 复制 `.env.example` → `.env`，填主密钥 | 基线 yaml 已配好本地容器端口，零额外配置 |
| **日常本地开发** | `.env` + 偶尔 `config.local.yaml` | 不要直接改 config.yaml 的共享默认值 |
| **新增一个配置项** | 先加 DEFAULTS，再按需在 config.yaml 写示例 | DEFAULTS 同时是环境变量路径解析的来源 |
| **CI 流水线** | 只注入 `SG_*` 环境变量 | 不放 .env，不挂 yaml；密钥走 CI 的 secret store |
| **新增一个发布环境** | 建 `conf/config.<name>.yaml`（只写非敏感差异）+ 部署环境注入密钥 | 不需要改框架任何代码 |
| **多地域部署同一镜像** | 同一镜像 + 不同 `SG_PROFILE` / 环境变量 | 见第六章，镜像保持不可变 |
| **需要不发版改值** | 现阶段用环境变量 + 重启；高频需求才上配置中心 | 见第八章，不要过早引入 |

### 决策口诀

```
这个值敏感吗？           是 → 环境变量/Secret，绝不碰入库文件
这个值随代码走吗？       是 → config.yaml / profile yaml
这个值要不发版就改吗？   是 → 先问「频率多高」，低频用重启，高频才配中心
```

---

## 六、Docker 阶段

目标：**一个不可变镜像，通过环境变量适配所有环境**。

```dockerfile
# 构建期只把代码与「非敏感基线」打进镜像
COPY sagitta/ ./sagitta/
COPY app/ ./app/
COPY conf/config.yaml ./conf/config.yaml
# config.<profile>.yaml 也可选择打入（非敏感）；更推荐运行时挂载
ARG BUILD_COMMIT=""
ENV SG_BUILD_COMMIT=${BUILD_COMMIT}
```

运行：

```powershell
docker run -d `
  -e SG_PROFILE=prod `
  -e SG_MASTER_KEY=$key `
  -e SG_DB_DEFAULT_URL=$dsn `
  -e SG_FILE_SECRET_KEY=$s3secret `
  --env-file .env.runtime `
  your-image:${commit}
```

要点：

- 镜像里**没有** `.env`、没有任何密钥；配置全部 `-e` 注入；
- 配置较大/较结构化时，可用挂载：`-v ./config.prod.yaml:/app/conf/config.prod.yaml:ro`；
- 镜像 tag 与 `SG_BUILD_COMMIT` 一起，构成可追溯的发布单位。

---

## 七、Kubernetes + GitOps 终态

### 7.1 配置注入的两种原生方式

| K8s 对象 | 对应配置类别 | 注入方式 |
|---|---|---|
| **ConfigMap** | 非敏感静态配置 | 环境变量（`configMapKeyRef`）或挂载为文件 |
| **Secret** | 部署密钥 | 环境变量（`secretKeyRef`）或挂载；生产建议接外部密钥管理（External Secrets + Vault/KMS） |

Deployment 片段：

```yaml
spec:
  template:
    spec:
      containers:
        - name: api
          image: your-image:{{commit}}
          env:
            - name: SG_PROFILE
              value: "prod,region-cn-north"
            - name: SG_MASTER_KEY
              valueFrom:
                secretKeyRef: { name: app-secrets, key: master-key }
            - name: SG_DB_DEFAULT_URL
              valueFrom:
                secretKeyRef: { name: app-secrets, key: db-url }
          # 结构化配置也可以整份挂载（等价于 profile 文件层）：
          # volumeMounts:
          #   - name: cfg
          #     mountPath: /app/conf/config.prod.yaml
          #     subPath: config.prod.yaml
      # volumes:
      #   - name: cfg
      #     configMap: { name: app-config }
```

### 7.2 GitOps：配置中心的「平价替代」

你提到「最好有个配置中心管理环境变量与版本（按 commit）」——在 K8s 体系里，这件事的主流答案不是先部署 Apollo/Nacos，而是：

```
独立配置仓库（infra/config 仓库）
  ├── base/            各集群通用值
  └── overlays/
      ├── prod-cn-north/   ConfigMap + Secret 清单（Secret 只放引用，不放明文）
      └── prod-us-east/
        ↓ commit / PR / 评审
      ArgoCD / Flux 按 commit 同步到集群
```

它已经覆盖了配置中心 80% 的核心诉求：

| 配置中心的诉求 | GitOps 的回答 |
|---|---|
| 版本管理 | Git 历史，版本 = commit SHA |
| 审计 | 谁改的、为什么改，PR 记录 |
| 回滚 | `git revert` + 同步 |
| 环境差异化 | kustomize overlay |
| 发布留痕 | ArgoCD 同步记录 |

**建议：走到 K8s 时先上 GitOps，而不是先上配置中心。** 只有「不发版、不同步也要秒级生效的动态配置」成为真实高频需求时，配置中心才有独立存在的必要。

### 7.3 线上版本可观测

框架已内置：CI 构建时注入 `SG_BUILD_COMMIT`，`/health` 会返回：

```json
{ "status": "ok", "env": "prod", "profiles": ["prod", "region-cn-north"], "build_commit": "a1b2c3d" }
```

任何时候都能回答「线上这一批实例跑的是哪个 commit、叠加了哪些 profile」。排查发布问题时，这是第一个要看的端点。

---

## 八、配置中心：留好接口，但现在不建

### 8.1 为什么不急着建

- 它是一个**有状态的运维依赖**：高可用、备份、权限、审计都要自己兜；
- 静态配置放进配置中心，反而失去 Git 的 diff / review / 回滚；
- GitOps 已经吃掉大部分场景。

### 8.2 留好扩展位：ConfigSource 抽象

当前六层在实现上天然是「一串返回 dict 的源」，未来加配置中心只是在链上插入一个新源，业务代码无感：

```python
# 未来形态（示意，非当前实现）
class ConfigSource(Protocol):
    def load(self) -> dict: ...

# 链：DEFAULTS → yaml 各层 → env → RemoteConfigSource（可选）
# RemoteConfigSource：启动时拉取，可加后台轮询/长连接监听；
# 拉取失败的降级策略在源内部定义（用上一次成功值 / 拒绝启动，按配置项性质选择）。
```

优先级建议：**远程动态配置置于环境变量之下还是之上，按配置项性质区分**——熔断/限流类应让远程值生效（高于默认），但部署密钥永远以环境变量为准（不允许远程覆盖，避免配置中心成为新的泄密面）。

### 8.3 触发建设的信号

- 频繁需要在不重启/不重新同步的情况下调整限流、灰度、开关；
- 配置变更需要秒级推送到成百上千实例；
- 需要按租户/实例维度下发不同配置。

在此之前，保持六层模型就是最简且不封死未来的选择。

---

## 九、演进路线总结

```
阶段 0（现在）   基线 yaml + profile 链 + .env + 环境变量         ← 已实现
                 缺配置不崩、密钥不入库、样例可复制

阶段 1（Docker） 不可变镜像 + 运行时环境变量注入 + commit 可追溯

阶段 2（K8s）    ConfigMap/Secret + GitOps（ArgoCD/Flux）
                 = 有版本、有审计、有回滚的「声明式配置中心」

阶段 3（按需）   独立配置中心（Apollo/Nacos），仅承接动态配置，
                 以 ConfigSource 形式插入，静态配置仍留 Git
```

每一步都不推翻前一步的产物——这是刻意控制摊子大小的方式：**用机制的可组合性替代一次性大建设。**
