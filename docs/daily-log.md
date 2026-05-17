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

## D3：接入 DeepSeek 大模型 API

### 今日完成

1. 在 Python Agent 服务中接入 DeepSeek Chat Completion API。
2. 使用 `.env` 管理 `DEEPSEEK_API_KEY`、`DEEPSEEK_BASE_URL`、`DEEPSEEK_MODEL`。
3. 将 `/agent/chat` 从 mock answer 替换为真实大模型回答。
4. 验证 Java `/api/chat` 可以通过 Python Agent 服务获取 DeepSeek 回答。
5. 当前链路：Postman → Java → Python → DeepSeek → Python → Java。

### 今日理解

大模型 API 本质也是 HTTP + JSON 调用。  
Java 负责业务入口，Python 负责 AI 调用和模型编排。

# D4：Prompt 工程与异常处理

## 今日完成

- 抽离 SYSTEM_PROMPT，完成 Prompt 与业务逻辑分离
- 增加企业 AI 行为约束，避免模型伪造企业制度
- Python 增加 try/except，处理 DeepSeek 调用失败
- Java 增加 Python 服务异常兜底
- 新增统一响应结构：success / answer / traceId
- Java 调 Python 链路恢复正常

## 今日问题

### 1. Java 调 Python 返回 422

FastAPI 返回：

```text
422 Unprocessable Entity
body: null
```

排查发现：

Java 与 Python DTO 字段不一致
RestClient 请求体解析异常

最终改用：
    RestTemplate + HttpEntity

解决问题。

2. Prompt 幻觉问题

模型在未接入知识库时，会生成“看似真实”的企业制度内容。

通过增加 Prompt 约束：
    未接入知识库时仅提供通用建议

降低幻觉风险。

今日理解
·Prompt 的核心是行为约束，而不只是“让 AI 更聪明”
·企业 AI 不能自由发挥，需要限制幻觉
·Java 与 Python 本质是 HTTP 服务调用
·跨服务 DTO 必须保持一致
·工程排障要区分：现象、猜测、证据、结论
下一步
·文件上传
·文本切片
·Embedding
·RAG 基础

Day5 – Daily Log

日期：2026-05-18

学习内容：

理解 RAG（检索增强生成）的概念及企业价值
理解企业为什么需要 RAG、不能直接问大模型
掌握 RAG 与普通聊天机器人的区别
理解上下文窗口限制与 token 消耗
理解关键词匹配的局限性及语义向量检索的必要性
理解离线向量化 + 在线检索的工程实践思路
项目结构与 Git 工程化思路巩固

代码 / 项目进度：

没有新增实际代码
主要进行项目结构和工程治理的思考
理解 RAG 流程：用户问题 → 知识库检索 → Prompt 拼接 → LLM → 回答

遇到的问题：

理解 RAG 的价值及与传统聊天机器人的区别
理解为什么知识库不能直接全部塞给 LLM

解决方式：

通过对比用户提问与文档内容，理解语义检索与向量化的必要性
梳理企业 AI 系统工程流程

核心知识点：

企业 AI ≠ 聊天机器人
RAG 本质是“知识检索 + 大模型生成”
Prompt 工程是对模型行为的约束
Embedding 和向量检索解决了关键词匹配的语义局限
离线向量化 + 在线检索是工程最佳实践
上下文窗口有限，需要精确检索相关知识

简历 / 面试价值：

能讲解企业 AI 架构和 RAG 流程
能分析为什么要拆 Java / Python 服务
能解释 Prompt 工程、异常处理、traceId 的实际作用
能阐述知识库切片、向量检索对性能和准确性的价值

学习时长：约 3~4 小时

明日计划：

Day6：第一版知识库构建
文档整理 + 文本切片
简单检索测试
Prompt 拼接测试
保持 Git 工程化，提交阶段性成果