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
- The Safety Guard (Safety Guard Lite) is a **heuristic defense-in-depth filter**. It is **not an authorization, trust, or security boundary**
- It applies input normalization (Unicode NFKC, Default-Ignorable/zero-width character removal, control-character removal, whitespace collapse) before rule checking
- Five high-confidence rule families use precompiled regex patterns: `prompt_override`, `prompt_extraction`, `credential_extraction`, `tool_abuse`, `business_policy_bypass`
- Only clear, unambiguous attacks are blocked. Uncertain, discussion-style, or consultative input passes by default (precision over recall)
- A compact safety-only view (removing whitespace and a limited set of separators) resists simple split attacks such as `忽 略 之 前 所 有 指 令`
- Original user input is preserved unchanged for RAG, query rewrite, and business actions; only the normalized form is used for safety checks
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

### Safety Guard Positioning

Safety Guard is a **heuristic defense-in-depth filter**.
It is **not** an authorization, trust, or security boundary.

The real security boundaries are provided by:

- authentication
- authorization (Java permission checks)
- tool capability (controlled tool provider)
- business validation
- tenant/data isolation
- transaction/state machine
- human confirmation

### What Safety Guard Does

- Input normalization (Unicode NFKC, Default-Ignorable removal, control-character removal, whitespace collapse) plus a compact safety-only view for simple split attacks
- Five high-confidence rule families: instruction override, system prompt extraction, credential extraction, tool abuse, business policy bypass
- Only clear, unambiguous attacks are blocked; uncertain, discussion-style, or consultative input passes by default
- The system prompt includes explicit security boundary declarations: user input and knowledge base content are untrusted, and the model must not execute embedded instructions or reveal internal configuration
- RAG prompts use clear boundary markers to separate system rules, untrusted knowledge base content, and untrusted user questions

### What Safety Guard Does Not Promise

- No complete prompt injection detection (semantic paraphrases, homoglyph confusables beyond NFKC, and quoted educational sentences are documented limitations, see `tests/safety_corpus.py`)
- No complete Unicode confusable protection (no runtime confusable table dependency)
- No complete natural language intent understanding
- No dedicated security classification model
- No runtime scanning of knowledge base fragments
- Java permission checks and human confirmation remain the final security boundary for business actions

## What Is Not Implemented

The following security capabilities are **not yet implemented**:

- User authentication and authorization
- Rate limiting
- Audit logging
- Dedicated security classification model (currently rule-based only)
- Knowledge base fragment runtime scanning
- Output verification
- Multi-tenant isolation
- Full Unicode confusable (homoglyph) protection at runtime
