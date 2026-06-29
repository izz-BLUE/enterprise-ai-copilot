# Roadmap

## 项目当前状态

**Early-stage but actively maintained.** 项目已完成核心 RAG 链路和 Agent 实验链路的搭建，具备知识库问答、评估体系、安全护栏和工具调用能力。尚未达到生产部署标准。

## 已完成能力（Done）

### 基础架构

- [x] Java Spring Boot + Python FastAPI 双服务架构
- [x] Java 统一入口，代理 Python 接口
- [x] React + Vite 前端演示页面
- [x] traceId 全链路透传（Frontend → Java → Python）

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

### Agent 实验链路

- [x] LangGraph 状态图编排（safety → router → rag/eval/refuse）
- [x] Safety Guard（5 类风险关键词检查）
- [x] 意图路由（评估查询 vs RAG 问答）
- [x] Tool Calling（rag_answer_tool, eval_report_tool）
- [x] LangChain RAG Chain 实验模块
- [x] Agent 接口 `/api/agent/langgraph/chat`

### Evaluation 体系

- [x] Retrieval Evaluation（source_hit + keyword_hit，零 token 消耗）
- [x] Generation Evaluation（answer 关键词匹配，调用 LLM）
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

## 短期计划（Planned）

- [ ] README 与 docs 持续完善
- [ ] 增加更多 eval case（跨域、对抗样本、边界条件）
- [ ] 优化 API 返回结构（统一错误码）
- [ ] 完善异常兜底和错误提示
- [ ] 补充单元测试和集成测试
- [ ] eval 支持 TopK 自动最优选择

## 中期计划（Planned）

- [ ] 多轮对话上下文管理
- [ ] 文档上传与知识库管理接口
- [ ] Docker Compose 一键部署
- [ ] 结构化日志（JSON 格式）

## 长期计划（Future）

- [ ] Qdrant / Milvus 替代 FAISS（支持增量更新、分布式）
- [ ] 用户权限与认证（JWT / API Key）
- [ ] 审计日志
- [x] CI 基础验证（GitHub Actions：Java compile + Python retrieval eval + Frontend build）
- [ ] CI RAG 回归测试（generation eval + baseline comparison）
- [ ] 可观测性（RAG 延迟 P99、token 消耗、评估趋势）
- [ ] 多模型配置（不同场景用不同模型）
- [ ] 更完整的 Agent 工具体系（工单查询、请假申请等写操作）
- [ ] LLM 自主 Tool Choice（替代规则路由）
- [ ] 内容安全 API（替代关键词 Safety Guard）
