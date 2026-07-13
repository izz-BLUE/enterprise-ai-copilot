# Deployment

## 验证环境

本次部署验证在腾讯云小规格实例上完成，环境如下：

| 项目 | 配置 |
|------|------|
| 操作系统 | Ubuntu 22.04 |
| Docker | 26.1.3 |
| Docker Compose | v2.27.1 |
| 总内存 | 3.3 GiB |
| 部署前可用内存 | 约 1.2 GiB |
| 根分区 | 40 GiB，约 29 GiB 可用 |
| Swap | 4 GiB（部署时创建） |
| swappiness | 10 |

> 注意：以上为本次验证环境，不是最低官方要求。

## 部署方式

采用**本地构建 + 上传**方式，不在服务器执行重型构建。

### 构建流程

1. 本地构建 Docker 镜像
2. `docker save` 导出为 tar 文件
3. gzip 压缩
4. SHA-256 校验
5. SCP 上传到服务器
6. 服务器 `docker load` 加载镜像

### 镜像

| 镜像 | 说明 |
|------|------|
| enterprise-ai-copilot-python:6e24f52 | Python Direct ONNX 生产镜像 |
| enterprise-ai-copilot-java:6e24f52 | Java Backend 生产镜像 |

## 目录结构

```
/opt/enterprise-ai-copilot/
├── models/
│   └── bge-small-zh-v1.5-onnx/    # ONNX 模型文件（只读挂载）
│       ├── onnx/model.onnx
│       ├── tokenizer.json
│       └── 1_Pooling/config.json
├── data/
│   └── processed/                  # 知识库数据（只读挂载）
│       ├── faiss.index
│       ├── faiss_metadata.json
│       ├── chunks.json
│       └── embeddings.json
├── deploy/
│   ├── docker-compose.prod.yml
│   └── .env                        # 权限 600
├── releases/                       # 发布产物（临时）
└── backups/                        # 配置备份
```

## 模型部署

### 模型信息

- 模型：BAAI/bge-small-zh-v1.5
- 格式：ONNX FP32
- 维度：512
- Runtime：Direct ONNX Runtime（不加载 Torch）
- Provider：CPUExecutionProvider

### 部署要求

- 模型文件不进 Git
- 只读挂载到容器
- 校验 SHA-256 一致性
- 不在服务器在线导出

### SHA-256 校验

```
model.onnx: f2220ab6b0959ee6ecf4c52dc793a77798aefa98f267f5bcce15c497612d4238
```

## 公网部署（copilot.jintianchi.cn）

### 域名和证书

| 项目 | 说明 |
|------|------|
| 域名 | copilot.jintianchi.cn |
| DNS | A 记录指向服务器 IP |
| 证书 | 独立 Let's Encrypt 证书（非共享） |
| 签发 | Docker certbot/certbot:v5.7.0 |
| 有效期 | 90 天（自动续签） |
| 续签 | `/opt/enterprise-ai-copilot/deploy/renew-copilot-cert.sh` |
| Cron | `/etc/cron.d/eac-copilot-certbot`（每天 3:15 AM 和 3:15 PM） |

### Nginx 配置

配置文件归档在 `deploy/nginx/copilot.conf`，当前服务器因历史原因将该片段合并进共享 `eat-what.conf`。

**HTTP (80)：**
- ACME challenge 路由（webroot）
- 其他请求 301 到 HTTPS

**HTTPS (443)：**
- 独立证书路径
- 静态文件：`/usr/share/nginx/html/copilot/current`
- SPA fallback：`try_files $uri $uri/ /index.html`
- `/assets/` 缓存 7 天
- `/api/` 反向代理到 `http://ai-copilot-java:8080`
- 安全响应头（nosniff, DENY, strict-origin, permissions-policy）
- 请求体限制 64k
- API 限流：2 req/s，burst 5

### 前端 Release 结构

```
/opt/eat-what/deploy/nginx/html/copilot/
├── releases/
│   └── ${RELEASE_ID}/          # 不可变 release 目录
│       ├── index.html
│       └── assets/
└── current -> releases/${RELEASE_ID}  # 原子软链接切换
```

Release ID 格式：`${UTC_TIMESTAMP}-${SHORT_SHA}`

### Docker 网络

```mermaid
graph TD
    subgraph Internet
        U[用户浏览器]
    end

    subgraph Host ["宿主机"]
        NG[Nginx 0.0.0.0:80/443]
        J[Java 127.0.0.1:8080]
    end

    subgraph Net1 ["deploy_eat-what-net (external)"]
        NG
        J
    end

    subgraph Net2 ["ai-copilot-net (bridge)"]
        J
        P[Python expose 8000]
    end

    U -->|HTTPS| NG
    NG -->|/api| J
    J -->|HTTP| P
```

- Nginx 位于 `deploy_eat-what-net`（与 eat-what/jobfit 共享）
- Java 同时连接 `deploy_eat-what-net` 和 `ai-copilot-net`
- Python 只连接 `ai-copilot-net`
- Nginx 无法直接访问 Python

### CORS

生产 Origin：`https://copilot.jintianchi.cn`

### 安全措施

- HTTP → HTTPS 301 重定向
- 安全响应头（X-Content-Type-Options, X-Frame-Options, Referrer-Policy, Permissions-Policy）
- API 基础限流（2 req/s，burst 5）
- 不开放公网 8000/8080
- 不在服务器构建前端或 Java/Python 镜像

## Compose 配置

### 服务拓扑

```mermaid
graph LR
    H[Host localhost] --> J[Java Backend<br/>127.0.0.1:8080]
    J --> N[Docker bridge<br/>ai-copilot-net]
    N --> P[Python Agent<br/>expose 8000]
    P --> M[models/:ro]
    P --> D[data/processed/:ro]
```

### 资源限制

| 服务 | 内存限制 | 说明 |
|------|----------|------|
| Python | 512 MiB | Uvicorn 单 Worker |
| Java | 512 MiB | JVM -Xms64m -Xmx256m |

### 端口绑定

| 服务 | 端口 | 绑定 |
|------|------|------|
| Python | 8000 | 仅 Docker 内网（expose） |
| Java | 8080 | 127.0.0.1（localhost only） |

### 环境变量

| 变量 | 说明 |
|------|------|
| EMBEDDING_BACKEND | onnx_direct |
| EMBEDDING_MODEL_PATH | /app/models/embedding/bge-small-zh-v1.5-onnx |
| EMBEDDING_ONNX_FILE | onnx/model.onnx |
| EMBEDDING_PROVIDER | CPUExecutionProvider |
| RAG_GATE_MODE | off |
| REWRITE_MODE | none |

## Secret 管理

- `.env` 文件权限 600
- API Key 不进 Git、不进镜像、不出现在 Compose
- 不输出到日志
- 通过 `--env-file` 传入容器

## 健康检查

### 检查命令

```bash
# 容器状态
docker compose -p enterprise-ai-copilot -f /opt/enterprise-ai-copilot/deploy/docker-compose.prod.yml ps

# 资源使用
docker stats --no-stream

# 系统内存
free -h
swapon --show

# Java 健康检查
curl http://127.0.0.1:8080/api/health

# Python 内网检查（通过测试容器）
docker run --rm --network enterprise-ai-copilot_ai-copilot-net \
  curlimages/curl:latest http://python-agent:8000/agent/health
```

### 预期结果

- Python: healthy, `{"service":"agent-python","status":"UP"}`
- Java: healthy, `{"status":"UP","service":"backend-java"}`
- 无 OOM、无重启

## 回滚

仅停止本项目，不影响其他服务：

```bash
docker compose \
  -p enterprise-ai-copilot \
  -f /opt/enterprise-ai-copilot/deploy/docker-compose.prod.yml \
  down
```

不会影响 eat-what 和 jobfit 项目。

## 当前边界

- 已接入 Nginx 反向代理（copilot.jintianchi.cn）
- 已配置独立域名和 HTTPS（Let's Encrypt 自动续签）
- 未配置高可用
- 未配置集中日志、APM 或自动扩缩容
- 当前是单机隔离部署验证 + 公网演示
- 公网演示不等于生产负载验证
