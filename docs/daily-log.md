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

# D2：Mock Chat 聊天链路打通

## 今日完成

### 1. Python Agent 服务新增聊天接口

新增：

POST /agent/chat

实现内容：

- 定义 ChatRequest 请求 DTO
- 定义 ChatResponse 响应 DTO
- 使用 FastAPI + Pydantic 实现聊天接口
- 返回 mock AI 响应
- 增加 traceId 用于请求链路追踪

当前返回示例：

```json
{
  "answer": "你好，这是 Python Agent 的模拟回答：xxx",
  "model": "mock-agent",
  "traceId": "xxx"
}
```

###2. Java 主系统新增聊天接口

新增：

POST /api/chat

实现内容：

定义 Java ChatRequest / ChatResponse DTO
新增 ChatController
使用 RestClient 调用 Python Agent 服务
将用户问题转发到 Python /agent/chat
接收 Python 返回结果并返回给前端
###3. 完成 Java → Python 聊天链路

当前链路：

客户端
↓
Java /api/chat
↓
RestClient
↓
Python /agent/chat
↓
Mock AI Response
↓
Java 返回结果

###4. GitHub 代码同步

完成 Git commit 与 push。

###今日问题
1. Spring Boot 接口 404

问题原因：

ChatController 放在：

package com.controller

不在 Spring Boot 默认扫描路径下。

解决方式：

调整为：

package com.fantuan.copilot.controller

并移动到 controller 包下。

2. JAVA_HOME 配置错误

问题原因：

JDK 路径版本号错误。

原路径：

jdk-17.0.19-hotspot

实际路径：

jdk-17.0.19.10-hotspot

解决方式：

重新配置 JAVA_HOME。

3. Git LF / CRLF warning

出现 Git 换行符 warning。

当前确认：

不影响代码运行与 push。

###今日理解提升
1. Java 与 Python 的职责划分

Java：

API 统一入口
参数校验
请求转发
业务逻辑处理

Python：

AI 能力处理
后续 LLM 调用
Prompt 编排
RAG 检索
2. DTO 思维

理解：

ChatRequest / ChatResponse 本质属于 DTO（数据传输对象）。

相比 Map：

类型更明确
Swagger 更清晰
更适合工程化维护
3. FastAPI 基础语法

学习：

BaseModel
类型注解
@app.post
请求与响应模型

##当前项目进度

已完成：

·Java + Python 双服务架构
·健康检查接口
·Java 调 Python
·Mock Chat 聊天链路

下一步：

·接入真实大模型 API
·替换 mock answer
·实现真正 AI 回复