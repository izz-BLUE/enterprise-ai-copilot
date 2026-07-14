# Contributing

Thanks for taking the time to improve Enterprise AI Copilot. Keep changes focused, document behavior changes, and avoid describing experimental features as production-ready.

## Local checks

Run the checks that match the files you changed:

```bash
# Java
cd backend-java
./mvnw compile -q
./mvnw test -q

# Python
cd agent-python
uv sync
uv run pytest -q
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

Knowledge-base or prompt changes require a fresh retrieval evaluation. If source documents change, rebuild chunks, embeddings, and the FAISS index before evaluating.

## Change guidelines

- Keep the Java and Python request/response contracts aligned; update `docs/api.md` when the public contract changes.
- Update `docs/architecture.md` when service boundaries or request flow change.
- Preserve `/agent/chat` as the stable RAG path. Treat LangGraph and reranking features as experimental unless their status changes explicitly.
- Evaluate both answerable and no-answer cases when changing retrieval, prompts, routing, or evaluation code.
- Keep deployment claims bounded by the documented test environment and duration.

## Security and repository hygiene

Never commit `.env` files, credentials, tokens, private keys, local evaluation reports, or generated directories such as `.venv/`, `target/`, `node_modules/`, and `dist/`.

Treat user input and model output as untrusted. Do not pass either directly to commands or privileged tools. The current Safety Guard is deterministic keyword filtering, not a complete semantic security system.

## Pull request checklist

- [ ] The change has a clear scope and rationale.
- [ ] Relevant local tests and evaluations pass, or the PR explains why they were not run.
- [ ] API, architecture, deployment, and roadmap documents are updated where needed.
- [ ] `git diff --check` passes.
- [ ] The diff contains no credentials, local reports, or generated artifacts.
- [ ] New claims are supported by code or reproducible test evidence.
