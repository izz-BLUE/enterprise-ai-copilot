# 07 - 发布检查清单（Release Checklist）

> **每次合并到 main 前必须逐项确认。**

## 代码质量

- [ ] 所有变更在 feature 分支上，不在 main
- [ ] 代码通过编译（Java: `mvnw compile`，Python: 无语法错误）
- [ ] 无 `.env`、API Key、token 被提交
- [ ] 无 `__pycache__`、`target/`、`node_modules/` 被提交
- [ ] 无 `data/eval/reports/` 被提交（除非是 baseline 更新）

## 功能验证

- [ ] Python 服务可启动（`uv run uvicorn app.main:app --reload --port 8000`）
- [ ] Java 服务可启动（`./mvnw spring-boot:run`）
- [ ] Frontend 可启动（`npm run dev`）
- [ ] RAG 问答正常（`curl -X POST http://localhost:8080/api/chat`）
- [ ] Agent 问答正常（如涉及 Agent 改动）
- [ ] 健康检查通过（`/api/health`、`/api/agent/health`）

## Evaluation

- [ ] Retrieval eval 通过（`uv run python scripts/eval/run_rag_eval.py`）
- [ ] Generation eval 通过
- [ ] 无新增 flaky case
- [ ] baseline 回归通过（如涉及 Python 改动）
- [ ] 评估结果未被人为修改

## 文档同步

- [ ] API 变更已同步 `docs/api.md`
- [ ] 架构变更已同步 `docs/architecture.md`
- [ ] README 已更新（如涉及功能变更）
- [ ] Task Board 已更新
- [ ] Agent Registry 已更新（如涉及会话变更）

## 实验功能

- [ ] `hybrid_rerank` 未被默认启用
- [ ] `rewrite_mode=rule` 未被默认启用
- [ ] LangChain RAG Chain 未被写成主链路
- [ ] 实验功能仍标记为实验性

## 安全

- [ ] 无 API Key 泄露
- [ ] 前端未直接调用 Python API
- [ ] 权限判断在 Java 后端
- [ ] Safety Guard 未被绕过

## 合并方式

- 使用 squash merge 到 main
- commit message 格式：`type: description`
  - `feat:` 新功能
  - `fix:` 修复
  - `docs:` 文档
  - `test:` 测试
  - `refactor:` 重构
  - `chore:` 杂项
- 合并后删除 feature 分支
