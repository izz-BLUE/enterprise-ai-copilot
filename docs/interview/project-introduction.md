# 项目介绍

下面提供三种长度的介绍口径。所有表述以当前仓库和 v0.4.0 的公开验证结果为准。

## 30 秒版本

> Enterprise AI Copilot 是一个企业知识库问答项目。Java Spring Boot 负责统一 API 和边界控制，Python FastAPI 负责检索、LLM 调用与 Agent 编排，React 提供演示界面。项目实现了 FAISS + BM25 + RRF 混合检索、可回归的检索评估、请求追踪和有界并发，并已在小规格云服务器上完成 HTTPS 部署和受控压测。

## 1 分钟版本

> 这是一个 Java、Python 和 React 组成的企业知识库问答系统。Java 是对外入口，负责输入校验、traceId、超时、并发保护和异常收敛；Python 负责文档检索、Prompt 构造、LLM 调用和实验性的 LangGraph 路由。
>
> RAG 主链路采用 FAISS 语义检索与字符级 BM25，再用 RRF 融合排名。检索质量通过 38 个固定用例回归，测试区分 answerable 与 no-answer 场景，并在 CI 中运行。部署侧使用 Nginx、Docker Compose 和 HTTPS，Python 不暴露宿主机端口；Nginx、Java、Python 三层都有明确的限流、并发或超时边界。
>
> 这个项目已经完成公网功能验证和小规格服务器的短时受控压测，但没有正式用户体系、高可用和完整监控，因此我把它定义为工程化个人项目，而不是生产级平台。

## 3 分钟版本

> 项目的目标是把一个知识库问答原型补齐为可以部署、测试和解释的 AI 应用。
>
> 架构上，React 只访问 Java。Java 作为控制面统一处理 CORS、输入长度、traceId、管理员权限、异常和 Java 到 Python 的在途请求数。Python 是内部 AI 服务，负责 Safety Guard、查询改写、混合检索、Prompt 和 LLM 调用。公网流量先经过 Nginx，Python 只存在于 Docker 内网。
>
> 检索部分没有直接把向量分数和 BM25 分数相加，因为两者尺度不同。我使用 RRF 按排名融合，让语义召回和关键词召回互补。生产配置还启用了确定性规则改写，解决“几点上班”这类口语查询与知识库表述不一致的问题。
>
> 质量保障分为几层：单元测试验证 Java/Python 并发边界，Retrieval Evaluation 用固定数据集检查来源、关键词和最终结果，前端执行 lint 与构建；部署后再做健康检查、代表性问答、拒答、权限和 429 契约验证。k6 测试分别覆盖健康接口、Safety 拒答、应用层过载和公网限流，避免把不同层的结果混在一起。
>
> 项目目前在小规格单机上运行。过载请求会在短等待后返回 429，而不是无限排队。现有测试证明保护机制在记录的环境和时长内有效，但不能推出生产 SLA。正式上线仍需补充用户级认证、集中监控告警、长期多客户端容量测试和高可用部署。

## 技术栈

| 层 | 技术 |
|---|---|
| 前端 | React, Vite |
| API 与控制面 | Java 17, Spring Boot 3, Maven |
| AI 服务 | Python 3.11, FastAPI, Pydantic, Uvicorn |
| LLM | DeepSeek（OpenAI-compatible API） |
| Embedding | BAAI/bge-small-zh-v1.5, ONNX Runtime |
| 检索 | FAISS, 字符级 BM25, RRF |
| Agent | LangGraph（实验链路） |
| 部署与验证 | Docker Compose, Nginx, Let's Encrypt, GitHub Actions, k6 |

## 可以重点展开的实现

- Java/Python 服务边界与失败处理
- FAISS + BM25 + RRF 混合检索
- 查询改写与检索评估的对应关系
- answerable/no-answer 评估口径
- traceId、Safety Guard 与 Evaluation 权限边界
- Nginx 限流和 Java/Python 双层有界并发
- 小内存服务器上的 ONNX 与容器资源优化

## 项目边界

| 已验证 | 尚未完成 |
|---|---|
| HTTPS 公网演示与隔离部署 | 正式用户认证、JWT/RBAC |
| RAG 与实验性 LangGraph 链路 | 多租户与数据权限 |
| 检索回归和 CI | 大规模、长时间分布式压测 |
| Safety Guard 基础拒答 | 语义级安全与 Prompt Injection 完整防护 |
| Nginx/Java/Python 过载保护 | 多实例、高可用与自动扩缩容 |
| 短时受控压测 | 完整监控、告警与审计平台 |

面试时应主动说明这些边界。能够区分“已经验证”“当前设计”和“未来计划”，比把项目描述成生产级系统更可信。
