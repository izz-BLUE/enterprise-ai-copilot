# Quality assurance

This project combines deterministic tests, retrieval evaluation, controlled load checks, and deployment smoke tests. Each layer answers a different question; no single result is treated as proof of production readiness.

## Continuous integration

GitHub Actions runs four independent functional jobs, plus security workflows:

| Area | Checks |
|------|--------|
| Java backend | compile, unit tests, concurrency behavior |
| Python service | concurrency tests, retrieval evaluation in `none` and `rule` rewrite modes |
| Frontend | lint and production build |
| Browser | five Playwright scenarios against mocked API contracts |

Gitleaks scans repository history for committed secrets. CodeQL analyzes Java, Python, and JavaScript on pull requests, pushes to `main`, and a weekly schedule. Dependabot checks GitHub Actions, Maven, uv, and npm dependencies monthly.

The workflow is defined in [`.github/workflows/ci.yml`](../.github/workflows/ci.yml).

## Retrieval evaluation

The retrieval suite contains answerable and no-answer cases. It measures source hits, keyword hits, and final case outcomes without calling the LLM, so it can run in CI with zero model-token cost.

```bash
cd agent-python

uv run python scripts/eval/eval_retrieval.py --rewrite-mode none \
  --min-source-hit-rate 100 \
  --min-keyword-hit-rate 95 \
  --min-final-pass-rate 95

uv run python scripts/eval/eval_retrieval.py --rewrite-mode rule
```

The `none` mode retains a known colloquial-query miss and therefore uses explicit 95% keyword and final-pass thresholds. Production uses deterministic rule rewriting; the corresponding suite is expected to pass completely. Reported rates apply only to the versioned test set, not arbitrary user questions.

## Security checks

The automated and manual checks cover:

- deterministic refusal of known high-risk requests before retrieval and LLM calls;
- server-generated trace IDs and stable public error messages;
- administrator-token protection for evaluation reports;
- network isolation between the public Nginx/Java path and the internal Python service;
- repository scans for credentials, `.env` files, keys, and generated artifacts.

The rule-based Safety Guard can be bypassed by unseen wording and is documented as a baseline control rather than a complete content-safety solution.

## Concurrency and deployment validation

Bounded concurrency is tested at the Java and Python layers. k6 scenarios separately exercise health stability, deterministic safety rejection, application overload, and Nginx rate limiting. Commands, thresholds, and stopping conditions are documented in [Concurrency & Load Test](concurrency-and-load-test.md); measured server results are summarized in [Performance](performance.md).

Release verification also checks the public page, Java and Python health endpoints, representative RAG questions, container restart counts, and the JSON 429 contract. These are short, controlled checks on a small single-server deployment and do not establish a production SLA.

## Known gaps

- The retrieval set is intentionally small and domain-specific.
- Playwright covers the main chat, Markdown, Safety Guard, and scrolling regressions; broad visual and cross-browser UAT is still manual.
- Long-running, multi-client, distributed capacity tests have not been completed.
- Authentication is a shared administrator token rather than per-user JWT/RBAC.
- Observability does not yet include a full metrics, alerting, and audit stack.
