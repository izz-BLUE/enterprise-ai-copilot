"""Keep pytest configuration independent from a developer's local .env."""

import os

import dotenv

# app.core.config imports load_dotenv at module import time.  Tests must not
# silently switch to a developer's PostgreSQL checkpoint or provider config.
dotenv.load_dotenv = lambda *args, **kwargs: False

os.environ.setdefault("DEEPSEEK_API_KEY", "test-provider")
os.environ.setdefault("DEEPSEEK_BASE_URL", "https://provider.example")
os.environ.setdefault("DEEPSEEK_MODEL", "test-model")
os.environ.setdefault(
    "LANGGRAPH_CHECKPOINT_DSN",
    "postgresql://checkpoint_test:checkpoint_test@localhost:5432/checkpoint_test",
)
os.environ.setdefault("RUN_POSTGRES_CHECKPOINT_INTEGRATION", "false")
