# enterprise-ai-copilot

## 项目目标

企业级 AI 知识库助手。Java Spring Boot 业务主系统 + Python FastAPI AI Agent 服务，支持 RAG 检索增强生成问答。

## 技术栈

| 层 | 技术 |
|---|------|
| 业务后端 | Java 17, Spring Boot 3.x, MyBatis Plus, MySQL, Redis |
| AI 服务 | Python 3.11, FastAPI, sentence-transformers, faiss-cpu |
| 大模型 | DeepSeek V4 (via OpenAI SDK, base_url=https://api.deepseek.com) |
| RAG | BAAI/bge-small-zh-v1.5 embedding + FAISS 向量检索 + keyword 关键词检索 (hybrid) |

## 目录结构

```
g:/跳槽计划/项目/enterprise-ai-copilot/
├── backend-java/           # Java Spring Boot 业务系统
│   ├── src/main/java/com/enterprise/
│   └── pom.xml
├── agent-python/           # Python FastAPI AI Agent
│   ├── app/
│   │   ├── core/           # config.py, 日志
│   │   ├── retrieval/      # faiss_retriever, keyword_retriever, hybrid_retriever
│   │   ├── services/       # rag_service, llm_service
│   │   ├── schemas/        # chat_schema (ChatRequest/ChatResponse)
│   │   └── prompts/        # system_prompt, build_rag_prompt
│   ├── scripts/            # build_chunks, build_embeddings, build_faiss_index, eval_*
│   └── .env                # DEEPSEEK_API_KEY, DEEPSEEK_MODEL
├── data/
│   ├── hr/                 # HR 知识库源文档 (.md)
│   ├── eval/               # rag_eval_cases.json
│   └── processed/          # chunks.json, faiss.index, faiss_metadata.json
└── docs/                   # 项目文档、每日日志
```

## 启动命令

```bash
# Java 后端 (port 8080)
cd backend-java && ./mvnw spring-boot:run

# Python Agent (port 8000)
cd agent-python && .venv/Scripts/uvicorn app.main:app --port 8000
```

## 常用脚本

```bash
# 知识库切片
python agent-python/scripts/build_chunks.py

# 构建 embedding + FAISS 索引
python agent-python/scripts/build_embeddings.py
python agent-python/scripts/build_faiss_index.py

# 检索评估（不调用 LLM）
python agent-python/scripts/eval_retrieval.py

# 生成评估（调用 LLM）
python agent-python/scripts/eval_generation.py
```

## 代码规范

- Java: 标准 Spring Boot 分层架构 (controller → service → mapper)
- Python: FastAPI + Pydantic schemas, 服务模块化
- 不引入不必要的新依赖
- 不修改不相关模块的代码
- 修改前备份配置文件

## 长期约束

- 用户是 4 年 Java 后端转 AI 应用后端候选人
- 代码需要考虑简历可解释性和面试追问
- 不修改 Java 代码，除非明确要求
- 不修改 /agent/chat 接口，除非明确要求
- Python 评估脚本不引入新依赖
- 当前 Claude Code 直连 DeepSeek Anthropic API
