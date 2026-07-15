FROM python:3.11-slim

ARG APP_VERSION=dev
ARG GIT_COMMIT=unknown
ARG BUILD_TIME=unknown

ENV APP_VERSION=${APP_VERSION} \
    GIT_COMMIT=${GIT_COMMIT} \
    BUILD_TIME=${BUILD_TIME}

LABEL org.opencontainers.image.version="${APP_VERSION}" \
      org.opencontainers.image.revision="${GIT_COMMIT}" \
      org.opencontainers.image.created="${BUILD_TIME}" \
      org.opencontainers.image.source="https://github.com/izz-BLUE/enterprise-ai-copilot"

# 安全：创建非 root 用户
RUN groupadd -r agent && useradd -r -g agent -d /app -s /sbin/nologin agent

WORKDIR /app

# 安装生产依赖（不含 torch/sentence-transformers/optimum）
COPY scripts/build/requirements-prod.txt /tmp/requirements-prod.txt
RUN pip install --no-cache-dir -r /tmp/requirements-prod.txt && rm /tmp/requirements-prod.txt

# 复制项目代码
COPY app/ app/
COPY scripts/ scripts/

# 设置目录权限
RUN chown -R agent:agent /app

# 切换到非 root 用户
USER agent

# 健康检查（使用 Python 而非 curl，避免额外依赖）
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/agent/health')" || exit 1

EXPOSE 8000

# 单 Worker 模式，适合低内存环境
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
