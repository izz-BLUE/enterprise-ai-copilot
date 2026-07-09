# 01 - 架构边界（Architecture Boundary）

> **多 Agent 协作的核心约束文件。任何跨层修改必须先确认此文档。**

## 三端架构

```
┌──────────────┐     ┌──────────────────┐     ┌──────────────────┐
│   Frontend   │────▶│  Java Backend    │────▶│  Python AI       │
│   React      │     │  Spring Boot     │     │  FastAPI         │
│   port 5173  │     │  port 8080       │     │  port 8000       │
└──────────────┘     └──────────────────┘     └──────────────────┘
     用户入口              业务层                  AI 能力层
```

## 层级职责

### Frontend（React）

**职责：** 用户交互、消息展示、traceId 显示

**允许修改：**
- UI 组件、样式、交互逻辑
- 请求 `/api/*` 接口
- 展示 response 中的字段

**禁止：**
- ❌ 直接调用 Python API（`localhost:8000`）
- ❌ 硬编码 API Key 或 token
- ❌ 实现业务逻辑判断（如权限判断）
- ❌ 绕过 Java 直接访问 Python

### Java Backend（Spring Boot）

**职责：** 统一入口、请求转发、traceId 管理、CORS、异常兜底、权限判断

**允许修改：**
- Controller、Filter、Config
- 新增 Java 侧 API 端点
- 错误码和响应格式

**禁止：**
- ❌ 直接调用 LLM
- ❌ 直接访问知识库
- ❌ 直接读写 Python 侧文件
- ❌ 相信前端传来的 `role` 字段做权限判断

**关键约束：**
- 权限判断必须在 Java 后端完成
- Java 是唯一的对外入口
- Python 是内部能力层

### Python AI Service（FastAPI）

**职责：** RAG 检索、Prompt 构造、LLM 调用、Agent 编排、Evaluation

**允许修改：**
- RAG 链路内的模块（retrieval、prompt、service）
- Agent 编排逻辑
- Evaluation 脚本和 case
- Build 脚本

**禁止：**
- ❌ 直接暴露给前端
- ❌ 修改 Java 侧代码
- ❌ 默认启用实验功能（hybrid_rerank、rewrite_mode=rule）
- ❌ 修改知识库文档内容（会影响评估结果）

## 跨层修改规则

| 修改类型 | 必须同步 | 示例 |
|---|---|---|
| Python 新增 API | 更新 `docs/api.md` + Java Controller | 新增 `/agent/xxx` |
| Java 新增 API | 更新 `docs/api.md` + Frontend 调用 | 新增 `/api/xxx` |
| Python 修改 response 字段 | 更新 `docs/api.md` + Java DTO + Frontend 展示 | `ChatResponse` 新增字段 |
| 修改 Prompt | 运行 eval 验证 + 更新 README 评估数据 | `system_prompt.py` |
| 修改 Eval Cases | 运行 eval + 确认不破坏已有 case | `rag_eval_cases.json` |
| 修改 Retrieval | 运行 eval + 对比前后结果 | `hybrid_retriever.py` |

## 多会话并发约束

| 规则 | 说明 |
|---|---|
| 不同模块可并行 | Java 改 Controller + Python 改 Retrieval = OK |
| 同一模块互斥 | 两个会话同时改 `system_prompt.py` = ❌ |
| main 分支不直接开发 | 必须用 feature 分支 |
| eval 产物不并发写 | `data/eval/reports/` 只能一个会话写 |

## 高冲突文件

以下文件多会话可能同时修改，必须加锁或协商：

| 文件 | 冲突原因 | 建议 |
|---|---|---|
| `agent-python/app/prompts/system_prompt.py` | Prompt 影响所有 eval | 改前通知其他会话 |
| `agent-python/app/retrieval/hybrid_retriever.py` | 检索逻辑核心 | 改前运行 eval |
| `data/eval/rag_eval_cases.json` | eval case 集 | 改前确认不影响已有 case |
| `README.md` | 项目全貌 | 改前确认不破坏已有描述 |
| `docs/api.md` | 接口契约 | 改前确认 Java + Python 同步 |
