# Serpentarius-labs

> *Serpentarius* — 蛇鹫（Secretary Bird），一种长腿猛禽，不飞，一步一步逼近，然后一击。

---

## 我们做什么

Serpentarius 构建面向 Agent 时代的基础设施层。

我们相信：

- **Agent 应该独立。** 每个 Agent 是一个完整的、可独立部署的服务，不依赖框架的隐式生命周期。
- **框架应该隐形。** 基础设施层只做三件事：路由、追踪、鉴权。其余的，框架不碰。
- **约定大于配置。** 一套清晰的接口约定，比一百个可选项更有价值。

我们的项目不是另一个 "全能框架"。它是让一堆独立服务能够可靠地协作的 **那一层薄薄的东西**。

---

## 项目

| 项目 | 描述 |
|------|------|
| [Sagitta](https://github.com/Serpentarius-labs/sagitta) | 执行端。Agent 的运行时骨架——注册、发现、消息收发、链路追踪。箭射出去之后，框架不再干涉。 |
| [Conclave](https://github.com/Serpentarius-labs/conclave) | 决策端。多 Agent 协作中的校验、质证、裁决机制。屋子里的辩论，有规则、有记录、可追溯。 |
| [Controller-Manager](https://github.com/Serpentarius-labs/controller-manager) | 协调层。Controller 的统一生命周期管理、健康检查、配置热更新。 |
| [Protocols](https://github.com/Serpentarius-labs/protocols) | 公共协议与接口定义。所有服务必须遵守的契约——消息格式、错误码、追踪上下文传播。 |

---

## 设计原则

1. **无状态优先。** 所有服务默认无状态，状态由外部存储管理。
2. **可观测性内建。** 每个请求自动携带 trace ID，日志结构化输出。
3. **失败可见。** 超时、重试、熔断——失败的模式必须可观察、可配置。
4. **最小依赖。** 每个项目只依赖必要的库，不引入 "未来可能用得上" 的依赖。
5. **文档即代码。** README 和 API 文档与代码同仓库、同版本。

---

## 快速开始

```bash
# 克隆任意仓库
git clone https://github.com/Serpentarius-labs/<repo>.git
cd <repo>
cat README.md
```

---

## 许可证

[MIT License](LICENSE)
