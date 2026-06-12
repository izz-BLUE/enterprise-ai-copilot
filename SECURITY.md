# Security Policy

## Project Status

This project is **early-stage and not production-ready**. It is an AI application backend demo for learning and portfolio purposes. It has not undergone a professional security audit.

## Reporting Vulnerabilities

If you discover a security issue, please report it responsibly:

- Open a GitHub Issue with a description of the vulnerability
- **Do not include real API keys, tokens, passwords, or private data in the report**
- Do not create a public issue with exploit details that could harm others

## API Key Safety

- Never commit `.env` files or API keys to the repository
- Use environment variables for all secrets
- The `.gitignore` is configured to exclude `.env` files
- If you accidentally commit a key, rotate it immediately

## Input and Output Trust

### User Input

- User input is passed to RAG retrieval and LLM prompts
- The current Safety Guard uses keyword-based rules, not semantic analysis
- Do not treat user input as safe without validation

### RAG Context

- RAG retrieval results come from knowledge base documents
- Retrieved content is injected into LLM prompts
- The quality of answers depends on the quality of the knowledge base

### LLM / RAG / Agent Output

- **LLM outputs are not trustworthy by default**
- RAG answers are grounded in retrieved documents but may still contain errors
- Agent tool outputs should be validated before use
- Do not execute LLM-suggested commands or tool calls without human review

## Prompt Injection

- The project is vulnerable to prompt injection attacks
- User messages are included in LLM prompts without advanced sanitization
- The Safety Guard provides basic keyword filtering but is not a complete defense
- Do not deploy this project in environments where prompt injection is a real risk

## What Is Not Implemented

The following security capabilities are **not yet implemented**:

- User authentication and authorization
- Rate limiting
- Audit logging
- Input sanitization beyond keyword filtering
- Output verification
- Multi-tenant isolation
