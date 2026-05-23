# Enterprise AI Copilot

Enterprise AI Copilot 是一个面向企业内部知识库问答场景的 AI 应用后端项目。

项目采用 Java + Python 双服务架构：  
Java 侧作为企业业务系统入口，负责统一 API、请求转发和业务集成；  
Python 侧作为 AI 服务，负责 RAG 检索、Prompt 构造、大模型调用和评估能力。

---

## 项目定位

本项目用于模拟企业内部制度、流程、IT 运维、HR 文档等知识库问答场景。

当前重点不是简单调用大模型 API，而是构建一条完整的企业 RAG 主链路：

```text
企业文档
→ Chunk 切分
→ Embedding
→ Faiss 向量索引
→ Hybrid Retrieval
→ Prompt Grounding
→ LLM Answer
→ RAG Evaluation

当前已完成功能
1. Java + Python 双服务架构
Java Spring Boot 提供统一 /api/chat 接口
Python FastAPI 提供 /agent/chat AI 服务接口
Java 通过 HTTP 调用 Python 服务
支持统一响应结构和异常兜底
2. 大模型接入
接入 DeepSeek API
支持 system prompt 与 user message 分离
支持企业 AI 助手角色约束
支持知识库无结果时拒答，减少幻觉
3. RAG 知识库处理
支持 Markdown 文档作为知识库输入
支持真实制度文档脱敏后接入
支持 chunk_size / chunk_overlap
支持短段落合并
支持标题与正文合并，避免制度文档切片过碎
支持 chunks.json 作为中间产物
4. Embedding 与语义检索
使用 BAAI/bge-small-zh-v1.5 生成中文 Embedding
支持生成 embeddings.json
支持 512 维向量表示
支持 cosine similarity 语义检索
5. Faiss 向量索引
使用 faiss-cpu
使用 IndexFlatIP
支持 L2 normalize 后通过 inner product 近似 cosine similarity
支持构建 faiss.index
支持 faiss_metadata.json 进行向量索引与原始 chunk 的映射
6. Hybrid Retrieval

当前检索层支持：

Faiss Semantic Retrieval
+
Keyword Retrieval
+
Merge
+
Deduplicate
+
TopK

Faiss 负责语义召回，Keyword 检索负责精确关键词补充。

7. RAG Evaluation

项目已实现两层评估：

Retrieval Evaluation

脚本：

agent-python/scripts/eval_retrieval.py

用于评估：

TopK 是否命中 expected_sources
TopK 内容是否包含 expected_keywords
source_hit_rate
keyword_hit_rate
final_pass_rate
Generation Evaluation

脚本：

agent-python/scripts/eval_generation.py

用于评估：

最终 answer 是否包含 expected_answer_keywords
LLM 调用是否成功
generation pass rate
文本归一化后的关键词命中情况
8. Evaluation Report

评估脚本支持输出 JSON 报告：

data/eval/reports/retrieval_eval_report.json
data/eval/reports/generation_eval_report.json

用于后续回归测试、质量追踪和 CI 接入。

当前技术栈
Java 后端
Java
Spring Boot
RestTemplate
HTTP / JSON DTO
Maven
Python AI 服务
Python
FastAPI
OpenAI SDK Compatible API
sentence-transformers
BAAI/bge-small-zh-v1.5
faiss-cpu
numpy
RAG 能力
Chunking
Chunk Overlap
Embedding
Faiss Vector Search
Keyword Retrieval
Hybrid Retrieval
Prompt Grounding
Retrieval Evaluation
Generation Evaluation
项目结构
enterprise-ai-copilot/
├── backend-java/              # Java Spring Boot 主服务
├── agent-python/              # Python FastAPI AI 服务
│   ├── app/
│   │   ├── core/              # 配置
│   │   ├── prompts/           # Prompt 构造
│   │   ├── retrieval/         # 检索模块
│   │   ├── schemas/           # 请求响应 DTO
│   │   └── services/          # RAG / LLM 服务
│   └── scripts/               # 离线构建与评估脚本
├── data/
│   ├── hr/                    # HR 制度知识库样例
│   ├── bank/                  # 银行业务知识库样例
│   ├── it/                    # IT 运维知识库样例
│   ├── processed/             # chunks / embeddings / faiss index
│   └── eval/                  # RAG 评估集与报告
└── docs/                      # 项目文档与面试总结
核心链路
离线构建流程
Markdown 文档
→ build_chunks.py
→ chunks.json
→ build_embeddings.py
→ embeddings.json
→ build_faiss_index.py
→ faiss.index + faiss_metadata.json
在线问答流程
用户问题
→ Java /api/chat
→ Python /agent/chat
→ Hybrid Retrieval
→ TopK Chunks
→ RAG Prompt
→ DeepSeek
→ Answer
评估流程
rag_eval_cases.json
→ eval_retrieval.py
→ retrieval_eval_report.json

rag_eval_cases.json
→ eval_generation.py
→ generation_eval_report.json
已验证问题示例

当前测试集覆盖：

公司上班时间是什么？
请假三天需要提前多久申请？
病假需要提供哪些材料？
年假怎么计算？
旷工几天会被解除劳动合同？
迟到早退怎么处罚？
婚假有多少天？
产假有多少天？

当前 Retrieval Evaluation 和 Generation Evaluation 均可输出通过率与 JSON 报告。

后续规划

后续计划逐步补充：

BM25 检索
RRF 排名融合
Cross Encoder Re-rank
Query Rewrite
多轮对话上下文重构
LangChain / LangGraph 编排
Qdrant / Milvus 向量数据库
文档上传与知识库管理
权限控制
审计日志
Docker Compose 部署
CI 中的 RAG 回归测试