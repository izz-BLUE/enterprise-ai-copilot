# 安全策略

## 项目状态

本项目仍处于**早期阶段，尚未达到生产可用状态**。这是一个用于学习和作品集展示的 AI 应用后端 Demo，尚未经过专业安全审计。

## 报告漏洞

如果发现安全问题，请负责任地报告：

- 创建 GitHub Issue，并说明漏洞情况。
- **报告中不要包含真实 API key、token、密码或隐私数据**。
- 不要创建包含可能伤害他人的利用细节的公开 issue。

## API Key 安全

- 绝不要向仓库提交 `.env` 文件或 API key。
- 所有 secret 都应使用环境变量。
- `.gitignore` 已配置为排除 `.env` 文件。
- 如果误提交了 key，请立即轮换。

## 输入与输出信任边界

### 用户输入

- 用户输入会传给 RAG 检索和 LLM Prompt。
- Safety Guard（Safety Guard Lite）是**启发式纵深防御过滤器**，**不是授权、信任或安全边界**。
- 规则检查前会进行输入规范化（Unicode NFKC、移除 Default-Ignorable/零宽字符、移除控制字符、折叠空白）。
- 五类高置信规则使用预编译正则：`prompt_override`、`prompt_extraction`、`credential_extraction`、`tool_abuse`、`business_policy_bypass`。
- 只拦截清晰、无歧义的攻击；不确定、讨论式或咨询式输入默认放行（优先保证 precision，而不是 recall）。
- 紧凑的仅用于安全检查的视图（移除空白和有限分隔符）可以抵抗 `忽 略 之 前 所 有 指 令` 这类简单拆分攻击。
- 原始用户问题仍用于最终 RAG Prompt、AgentState 和业务动作；检索阶段可以额外使用 `normalize_retrieval_query()` 做少量语义等价规范化。安全检查使用自己的安全规范化视图，三者职责互不替代。
- 未经校验，不要把用户输入视为安全内容。

### RAG 上下文

- RAG 检索结果来自知识库文档。
- 检索内容会注入 LLM Prompt。
- 答案质量取决于知识库质量。

### LLM / RAG / Agent 输出

- **LLM 输出默认不可信**。
- RAG 答案以检索文档为依据，但仍可能包含错误。
- Agent Tool 输出在使用前应进行校验。
- 未经人工复核，不要执行 LLM 建议的命令或 Tool 调用。

## Prompt 注入

### Safety Guard 定位

Safety Guard 是**启发式纵深防御过滤器**。
它**不是**授权、信任或安全边界。

真正的安全边界由以下机制提供：

- 认证（authentication）
- authorization（Java 权限检查）
- Tool capability（受控 Tool provider）
- 业务校验
- 租户/数据隔离
- 事务/状态机
- 人工确认

### Safety Guard 的作用

- 输入规范化（Unicode NFKC、移除 Default-Ignorable 和控制字符、折叠空白），并为简单拆分攻击生成紧凑的安全视图。
- 五类高置信规则：指令覆盖、系统 Prompt 提取、凭据提取、Tool 滥用、业务策略绕过。
- 只拦截清晰、无歧义的攻击；不确定、讨论式或咨询式输入默认放行。
- system Prompt 明确声明安全边界：用户输入和知识库内容均不可信，模型不得执行其中嵌入的指令或泄露内部配置。
- RAG Prompt 使用清晰的边界标记区分系统规则、不可信知识库内容和不可信用户问题。

### Safety Guard 不承诺的能力

- 不提供完整的 Prompt Injection 检测（语义改写、超出 NFKC 范围的 homoglyph 混淆，以及带引号的教学句子均是已记录的限制，见 `tests/safety_corpus.py`）。
- 不提供完整的 Unicode confusable 防护（运行时不依赖混淆字符表）。
- 不提供完整的自然语言意图理解。
- 不提供专用安全分类模型。
- 不在运行时扫描知识库片段。
- Java 权限检查和人工确认仍是业务动作的最终安全边界。

## 尚未实现的能力

以下安全能力**尚未实现**：

- 用户认证与授权
- 速率限制
- 审计日志
- 专用安全分类模型（当前仅基于规则）
- 知识库片段运行时扫描
- 输出校验
- 多租户隔离
- 运行时完整 Unicode confusable（homoglyph）防护
