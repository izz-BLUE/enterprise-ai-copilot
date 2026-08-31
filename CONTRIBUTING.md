# 参与贡献

感谢你投入时间改进 Enterprise AI Copilot。请保持变更聚焦，记录行为变化，不要把实验性功能描述为已达到生产可用状态。

## 本地检查

请根据修改的文件执行相应检查：

```bash
# Java
cd backend-java
./mvnw compile -q
./mvnw test -q

# Python
cd agent-python
uv sync
uv run pytest -q
# app.core.config requires a reachable PostgreSQL checkpoint DSN for evaluations.
export LANGGRAPH_CHECKPOINT_DSN=postgresql://checkpoint_user:checkpoint_password@localhost:5432/enterprise_ai_runtime
uv run python scripts/eval/eval_retrieval.py --rewrite-mode none \
  --min-source-hit-rate 100 --min-keyword-hit-rate 95 --min-final-pass-rate 95
uv run python scripts/eval/eval_retrieval.py --rewrite-mode rule

# Frontend
cd frontend
npm install
npm run lint
npm run build
npx playwright install chromium
npm run test:e2e
```

知识库或 Prompt 发生变化时，需要重新执行检索评估。如果源文档发生变化，评估前请重新构建分块、向量和 FAISS index。

## 变更规范

- 保持 Java 与 Python 的请求/响应契约一致；公共契约变化时更新 `docs/api.md`。
- 服务边界或请求流变化时更新 `docs/architecture.md`。
- 保留 `/agent/chat` 作为稳定 RAG 路径。除非明确改变其状态，否则将 LangGraph 和 reranking 功能视为实验性功能。
- 修改检索、Prompt、路由或评估代码时，同时评估可回答和无答案场景。
- 部署相关表述必须限定在文档记录的测试环境和持续时间内。

## 安全与仓库整洁

绝不要提交 `.env` 文件、凭据、token、私钥、本地评估报告，或 `.venv/`、`target/`、`node_modules/`、`dist/` 等生成目录。

将用户输入和模型输出视为不可信内容。不要把任一内容直接传给命令或特权工具。当前 Safety Guard 是确定性的关键词过滤器，不是完整的语义安全系统。

## Pull request 检查清单

- [ ] 变更范围和理由清晰。
- [ ] 相关本地测试和评估通过，或在 PR 中说明未执行的原因。
- [ ] 按需更新 API、架构、部署和 roadmap 文档。
- [ ] `git diff --check` 通过。
- [ ] diff 不包含凭据、本地报告或生成产物。
- [ ] 新增结论有代码或可复现测试证据支持。
