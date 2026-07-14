# Roadmap

## 项目当前状态

**Early-stage but actively maintained.** 项目已完成核心 RAG 链路和 Agent 实验链路的搭建，具备知识库问答、评估体系、安全护栏和工具调用能力。尚未达到生产部署标准。

## 已完成能力（Done）

### 基础架构

- [x] Java Spring Boot + Python FastAPI 双服务架构
- [x] Java 统一入口，代理 Python 接口
- [x] React + Vite 前端演示页面
- [x] traceId 全链路透传（Frontend → Java → Python）
- [x] Docker Compose 隔离部署（腾讯云小规格实例验证通过）
- [x] Nginx 反向代理（静态文件 + /api 代理）
- [x] 独立子域名（copilot.jintianchi.cn）
- [x] HTTPS（独立 Let's Encrypt 证书 + 自动续签）
- [x] 前端公网演示
- [x] 基础 API 限流（2 req/s，burst 5）
- [x] Java/Python 双层有界并发保护（短队列超时返回 429）
- [x] k6 分层压测脚本与停止条件
- [x] 目标服务器 L1-L4 受控压测与脱敏结果归档
- [x] Docker 持久化 external network
- [x] GitHub Actions：Java/Python/Frontend/Playwright 质量门禁
- [x] Gitleaks、CodeQL 与 Dependabot 基础安全自动化

### RAG 主链路

- [x] 文档切片（Markdown → chunks.json）
- [x] BGE embedding 编码（BAAI/bge-small-zh-v1.5）
- [x] FAISS 向量索引构建与检索
- [x] 关键词检索（jieba 分词 + n-gram 匹配）
- [x] Hybrid Retrieval（Faiss + Keyword 合并去重 TopK）
- [x] BM25 检索（字符级 n-gram，无外部依赖）
- [x] RRF（Reciprocal Rank Fusion）多路检索融合
- [x] Cross Encoder Re-rank（hybrid_rerank 实验模式）
- [x] Query Rewrite（rule 规则匹配实验模式）
- [x] RAG Prompt 构造（知识库内容 + 严格规则）
- [x] DeepSeek LLM 调用（OpenAI SDK 兼容）
- [x] 稳定 RAG 接口 `/api/chat`
- [x] Torch-free Direct ONNX Runtime（内存从 877 MiB 降至 174 MiB）

### Agent 实验链路

- [x] LangGraph 状态图编排（safety → router → rag/eval/refuse）
- [x] Safety Guard（5 类风险关键词检查）
- [x] 意图路由（评估查询 vs RAG 问答）
- [x] Tool Calling（rag_answer_tool, eval_report_tool）
- [x] LangChain RAG Chain 实验模块
- [x] Agent 接口 `/api/agent/langgraph/chat`

### Evaluation 体系

- [x] Retrieval Evaluation（source_hit + keyword_hit，零 token 消耗）
- [x] Generation Evaluation（answer 关键词匹配 + keyword groups 同义词组 + failure_type 分类，调用 LLM）
- [x] Flaky case 识别（retry 机制，区分随机波动和稳定失败）
- [x] Baseline 管理（手动更新，回归检测）
- [x] 一键评估脚本 `run_rag_eval.py`
- [x] TopK 对比评估 `compare_topk_eval.py`
- [x] Query Rewrite 对比评估 `compare_query_rewrite.py`
- [x] 38 个 eval case（28 answerable + 10 no-answer 负样本，含 13 个口语化 Query Rewrite case）
- [x] 中文数字归一化（"三天" ↔ "3天"）

### 异常兜底

- [x] Python 不可用时 Java 返回 `success=false` + traceId
- [x] LLM 调用失败时返回兜底文案
- [x] 知识库无结果时 Prompt 要求拒答
- [x] 安全问题输入时 Safety Guard 拦截
- [x] Playwright 核心回归（问答、Markdown、拒答、长消息滚动）

## 版本记录

### v0.4.0

- Java/Python 双层有界并发保护（各 3 个并发槽，500ms 短队列）
- Python LLM、Java 下游和 Nginx 递增超时预算（30s / 40s / 45s）
- k6 健康稳定性、Safety、AI 过载和公网限流四层测试
- 目标服务器 L1-L4 受控验收通过并归档脱敏指标
- 公网 Nginx 429 统一为 JSON，补充 `Retry-After`、边缘 traceId 并隐藏具体版本
- CI 增加 Java/Python 并发测试

### v0.3.3

- 修复 Markdown 单个 `~` 被错误渲染为删除线的问题
- 公网部署、浏览器 UAT、CI、Tag 和 Release 已完成

### v0.3.2

- 生产环境启用规则查询重写（REWRITE_MODE=rule），口语化查询命中修复
- ADMIN_TOKEN 非空强制校验，Evaluation 权限边界生效
- 评估报告只读挂载到生产容器，Evaluation 工具返回实际指标（方案 A）
- 公网 UAT、CI、Tag 和 Release 已完成

### v0.3.1

- Public frontend demo (https://copilot.jintianchi.cn)
- Nginx reverse proxy + HTTPS
- Persistent Docker edge network
- Independent Let's Encrypt certificate with auto-renewal
- Basic API rate limiting

### v0.3.0

- Torch-free Direct ONNX Runtime
- Docker Compose isolated deployment
- Tencent Cloud small instance verification

## 短期计划（Planned）

- [ ] [增加更多 eval case 与长时间容量基线](https://github.com/izz-BLUE/enterprise-ai-copilot/issues/14)
- [ ] 优化 API 返回结构（统一错误码）
- [ ] 完善异常兜底和错误提示
- [ ] 扩展跨浏览器、窄屏和视觉回归测试
- [ ] eval 支持 TopK 自动最优选择

## 中期计划（Planned）

- [ ] 多轮对话上下文管理
- [ ] 文档上传与知识库管理接口
- [ ] 结构化日志（JSON 格式）

## 长期计划（Future）

- [ ] Qdrant / Milvus 替代 FAISS（支持增量更新、分布式）
- [ ] [用户级权限与认证（JWT / RBAC）](https://github.com/izz-BLUE/enterprise-ai-copilot/issues/12)
- [ ] 审计日志
- [x] CI 基础验证（Java/Python/Frontend/Playwright）
- [ ] CI RAG 回归测试（generation eval + baseline comparison）
- [ ] [指标、告警与请求级可观测性](https://github.com/izz-BLUE/enterprise-ai-copilot/issues/13)
- [ ] 多模型配置（不同场景用不同模型）
- [ ] 更完整的 Agent 工具体系（工单查询、请假申请等写操作）
- [ ] LLM 自主 Tool Choice（替代规则路由）
- [ ] 内容安全 API（替代关键词 Safety Guard）
