# 项目介绍

> 面试开场用。根据面试时间选择 30 秒 / 1 分钟 / 3 分钟版本。

---

## 30 秒版本

> 这是一个企业知识库 AI 应用后端项目，Java Spring Boot 做业务网关，Python FastAPI 做 AI 服务引擎，支持 RAG 问答和 LangGraph Agent 实验链路。我按生产化风险做了多轮安全加固，包括 Safety Guard、权限边界、traceId 链路追踪、超时兜底等。当前定位是本地 Demo / 面试演示项目。

---

## 1 分钟版本

> 这是一个企业知识库 AI 应用后端项目，采用 Java Spring Boot + Python FastAPI + React 三端架构。
>
> Java 做业务网关，负责统一入口、权限判断、异常兜底；Python 做 AI 服务引擎，负责 RAG 检索、Prompt 构造、LLM 调用、Agent 编排。
>
> 核心能力包括：稳定 RAG 主链路（Hybrid Retrieval = Faiss + BM25 + RRF）、LangGraph Agent 实验链路（Safety Guard + 意图路由 + Tool Calling）、两层评估体系（Retrieval + Generation，支持 flaky 检测和 baseline 回归）。
>
> 我在项目中按生产化风险做了 Phase 3 多轮安全加固，共 12 项修复，覆盖 CORS、超时、输入校验、Safety Guard、traceId、异常收敛、Admin Token 权限边界、Evaluation 访问限制。
>
> 当前定位是本地 Demo / 面试演示项目，不做公网部署，但已完成部署准备文档。

---

## 3 分钟版本

> 这是一个企业知识库 AI 应用后端项目，采用 Java Spring Boot + Python FastAPI + React 三端架构。
>
> **架构设计：** Java 做业务网关，是唯一的对外入口，负责权限判断、traceId 管理、异常兜底、CORS 控制。Python 做 AI 服务引擎，是内部能力层，负责 RAG 检索、Prompt 构造、LLM 调用、Agent 编排。前端通过 Vite proxy 只调 Java，不直接调 Python。
>
> **核心链路有两条：** 第一条是稳定 RAG 主链路，手写全链路不依赖 LangChain，用 Faiss 语义检索 + BM25 关键词检索 + RRF 融合排序，TopK=3 传给 LLM。第二条是 LangGraph Agent 实验链路，Safety Guard 做输入安全检查，router_node 做意图路由，分 RAG 问答、Evaluation 查询、安全拒答三个分支。
>
> **评估体系：** 38 个测试用例，两层评估 — Retrieval Evaluation 零 token 消耗检查检索命中，Generation Evaluation 调用 LLM 检查回答质量，支持 flaky 检测和 baseline 回归。
>
> **安全加固：** Phase 3 做了 12 项修复 — CORS 从 `*` 收敛为白名单、Java 和 Python 双层超时控制、双层输入长度校验、Safety Guard 覆盖两条链路、traceId 服务端统一生成不信任客户端、异常信息收敛不暴露底层细节、Admin Token 最小权限保护 Evaluation。
>
> **项目边界：** 当前定位是本地 Demo / 面试演示项目，不做公网部署。FIX-003（Python 服务裸露）仍是上线前阻塞项。当前方案是最小 Admin Token + Evaluation 访问限制，不是完整用户权限体系。

---

## 项目背景

- 企业内部知识库问答场景
- 从 Java 后端转型 AI 应用开发的工程实践
- 重点关注 RAG / Agent / Evaluation 工程化，不涉及模型训练

## 技术栈

| 层 | 技术 |
|---|------|
| 前端 | React + Vite |
| 业务网关 | Java 17, Spring Boot 3.x, RestTemplate, Maven |
| AI 服务 | Python 3.11, FastAPI, Pydantic, Uvicorn |
| LLM | DeepSeek / OpenAI-compatible API |
| Embedding | BAAI/bge-small-zh-v1.5 |
| 向量检索 | faiss-cpu, IndexFlatIP |
| 关键词检索 | BM25 (自研), 字符级 n-gram |
| Agent 框架 | LangGraph (实验) |
| 评估 | Python 脚本, JSON 报告 |

## 我负责的内容

- 整体架构设计（Java + Python 双服务分工）
- RAG 主链路实现（Hybrid Retrieval = Faiss + BM25 + RRF）
- LangGraph Agent 实验链路（Safety Guard + 意图路由 + Tool Calling）
- Evaluation 两层评估体系
- Phase 3 安全加固（12 项修复）
- 多 Agent 协作开发流程设计
- 部署准备文档和本地生产模拟

## 项目亮点

| 亮点 | 说明 |
|------|------|
| Java + Python 双服务架构 | 职责清晰，Java 做网关控制，Python 做 AI 能力 |
| Hybrid Retrieval | Faiss 语义 + BM25 关键词 + RRF 融合，三种模式可切换 |
| LangGraph Agent | Safety Guard → 意图路由 → RAG / Eval / Refuse |
| Evaluation 回归 | 38 cases, flaky 检测, baseline 回归, 零 token Retrieval Eval |
| Safety Guard | 5 类风险关键词覆盖两条链路 |
| traceId 全链路 | 服务端统一生成 → 日志关联 → 前端展示 |
| timeout / fallback | Java RestTemplate + Python LLM 双层超时 |
| Admin Token 权限边界 | 最小方案保护 Evaluation，不引入复杂登录 |
| 多 Agent 协作 | 9 个协文档、任务看板、Session 注册、分支管理 |

## 当前项目边界

| 已完成 | 未完成 |
|--------|--------|
| RAG 主链路 | Python 服务访问控制（FIX-003） |
| LangGraph Agent | 正式认证体系（JWT / RBAC） |
| Safety Guard | sources 字段脱敏 |
| Evaluation 回归 | 日志脱敏 |
| Admin Token | 限流 |
| traceId 链路追踪 | Docker Compose 部署 |
| timeout / fallback | 前端管理台 |
| CORS 白名单 | CI/CD 集成 |
| 部署准备文档 | |
