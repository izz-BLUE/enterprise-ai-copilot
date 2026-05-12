# 每日学习记录

## D1：项目初始化与双服务打通

### 今日完成

1. 建立 enterprise-ai-copilot 项目目录。
2. 编写根目录 README.md。
3. 编写 docs/01-项目定位.md。
4. 初始化 Git 本地仓库。
5. 创建 GitHub 远程仓库并完成首次 push。
6. 创建 Spring Boot Java 主系统。
7. 创建 FastAPI Python Agent 服务。
8. 实现 Java 健康检查接口 /api/health。
9. 实现 Python 健康检查接口 /agent/health。
10. 实现 Java 调用 Python 的接口 /api/agent/health。

### 今日问题

1. 本机未安装 JDK，导致 Spring Boot 无法启动。
2. Cursor 内置终端未正确读取 JAVA_HOME。
3. GitHub push 时 Git / PowerShell 未走代理。
4. GitHub 认证时 Git Credential Manager 出现本地凭据弹窗异常。

### 解决方式

1. 安装 JDK 17。
2. 使用外部 PowerShell 启动 Java 服务。
3. 给 Git 配置代理后完成 push。
4. 通过 GitHub 浏览器认证完成远程仓库推送。

### 今日复盘

今天完成了 Java + Python 双服务架构的最小闭环，验证了 Java 主系统可以通过 HTTP 调用 Python Agent 服务，为后续聊天接口、大模型调用、RAG 和 Agent 工作流打下基础。