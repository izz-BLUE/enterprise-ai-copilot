FROM python:3.11-slim

ARG APP_VERSION=dev
ARG GIT_COMMIT=unknown
ARG BUILD_TIME=unknown

ENV APP_VERSION=${APP_VERSION} \
    GIT_COMMIT=${GIT_COMMIT} \
    BUILD_TIME=${BUILD_TIME} \
    ENTERPRISE_OA_MCP_HOST=0.0.0.0 \
    ENTERPRISE_OA_MCP_PORT=8100 \
    ENTERPRISE_OA_MCP_PATH=/mcp \
    PATH=/app/enterprise_oa_mcp_server/.venv/bin:$PATH \
    PYTHONPATH=/app

LABEL org.opencontainers.image.version="${APP_VERSION}" \
      org.opencontainers.image.revision="${GIT_COMMIT}" \
      org.opencontainers.image.created="${BUILD_TIME}" \
      org.opencontainers.image.source="https://github.com/izz-BLUE/enterprise-ai-copilot"

RUN groupadd -r mcp && useradd -r -g mcp -d /app -s /sbin/nologin mcp

WORKDIR /app/enterprise_oa_mcp_server

COPY pyproject.toml uv.lock ./
COPY __init__.py __main__.py fixtures.py server.py tools.py ./

RUN pip install --no-cache-dir uv==0.11.25 \
    && uv sync --frozen --no-dev --no-install-project \
    && chown -R mcp:mcp /app

USER mcp

EXPOSE 8100

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import socket; s=socket.create_connection(('127.0.0.1', 8100), 2); s.close()"]

CMD ["python", "-m", "enterprise_oa_mcp_server"]
