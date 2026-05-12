# Enterprise AI Copilot

本项目是一套企业级 RAG + Agent 业务流程辅助平台，采用 Java + Python 双服务架构。

## 项目定位

系统面向企业内部知识库、制度问答、流程辅助、智能客服、IT 运维支持等场景，支持文档上传、知识检索、RAG 问答、Agent 工具调用、权限控制、审计日志和低置信度拒答。

## 技术栈

- Java Spring Boot：企业业务系统、权限、知识库管理、审计日志
- Python FastAPI：AI Agent 服务、LangChain、LangGraph、RAG
- MySQL：业务数据存储
- Redis：缓存、会话、限流
- Qdrant / Milvus：向量检索
- Docker Compose：本地一键部署

## 项目模块

- backend-java：Java 主系统
- agent-python：Python Agent 服务
- data：模拟知识库文档
- docs：项目设计文档
- docker-compose.yml：基础组件编排





github仓库地址:https://github.com/izz-BLUE/enterprise-ai-copilot.git