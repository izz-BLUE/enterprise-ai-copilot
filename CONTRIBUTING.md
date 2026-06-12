# Contributing Guide

Thank you for your interest in contributing to Enterprise AI Copilot.

This project is **early-stage but actively maintained**. Contributions are welcome from both human developers and AI coding agents.

## Recommended Workflow

1. **Open an Issue** — describe the bug, feature, or improvement
2. **Create a Branch** — `git checkout -b <type>/<short-description>`
3. **Make Changes** — keep commits focused and well-described
4. **Run Local Checks** — see below
5. **Open a Pull Request** — link the issue, describe what changed
6. **CI Must Pass** — GitHub Actions runs Java compile, Python retrieval eval, and frontend build
7. **Merge** — after review and CI green

## Local Setup

### Python AI Service

```bash
cd agent-python
uv sync
uv run uvicorn app.main:app --reload --port 8000
```

### Java Backend

```bash
cd backend-java
./mvnw spring-boot:run
```

### Frontend

```bash
cd frontend
npm install
npm run dev
```

## Validation Commands

### Run RAG Evaluation

```bash
cd agent-python
uv run python scripts/eval/run_rag_eval.py
```

### Run Java Compile

```bash
cd backend-java
./mvnw compile
```

### Run Frontend Build

```bash
cd frontend
npm run build
```

## Change Guidelines

- **API changes**: update `docs/api.md`
- **Architecture changes**: update `docs/architecture.md`
- **RAG / Agent / Eval changes**: run evaluation, update `README.md` or `docs/roadmap.md` if needed
- **Keep `/agent/chat` stable**: the main RAG pipeline should not be broken by experimental changes
- **Do not exaggerate**: experimental features should not be described as production-ready

## Do Not Commit

- `.env` files or API keys
- `.venv/`, `target/`, `node_modules/`
- Current evaluation reports (`data/eval/reports/`) unless they are baselines
- Local IDE configuration (`.vscode/`, `.idea/`)
- Large binary files or generated artifacts

## Code Style

- **Java**: standard Spring Boot layered architecture
- **Python**: FastAPI + Pydantic, modular services
- **Frontend**: React functional components
- Keep changes minimal and focused
- Do not introduce unnecessary dependencies
