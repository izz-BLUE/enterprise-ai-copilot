# 文档索引

本文档按阅读目的整理项目资料。当前系统的事实以代码、测试、配置和部署文件为准；Markdown 只负责解释、导航或记录已明确的历史决策。

## 当前系统

- [架构](architecture.md)：Java/Python/React 边界、Planner-first、Runtime Context、Checkpoint、Memory 与接受限制。
- [API 契约](api.md)：公开、内部、业务动作、HITL、外部审批和 Mock OA 接口。
- [受控业务动作](controlled-business-actions.md)：Proposal、PendingAction、确认时重验、HITL 和外部审批。
- [部署](deployment.md)：Compose、配置、网络、前端发布和运维边界。
- [质量保证](quality-assurance.md)：测试范围、Eval 分类、CI 门禁和限制。
- [RAG 质量工程](rag-quality-engineering.md)：当前检索配置、质量评估和实验模式。
- [性能](performance.md) / [并发与压测](concurrency-and-load-test.md)：性能基线、压测方法与结果边界。
- [Memory 架构](memory-architecture.md) / [Memory 安全](memory-security.md)：会话任务连续性、作用域和终态边界。
- [本地 Demo](demo-guide.md) / [Demo 脚本](demo-script.md)：启动、报销闭环、年假和故障排查。
- [路线图](roadmap.md)：当前尚未完成且没有被代码提前宣称的工作。

## 历史与维护

- [发布记录](releases/)：按发布时点保存，不用当前 HEAD 的事实改写历史数字或结论。
- [文档维护规则](documentation-maintenance.md)：文档类别、准入和防漂移规则。
- [RAG Gate 实验记录](rag-retrieval-gate-experiment.md)：运行时证据门控失败实验；它与当前 CI 的生产 Retrieval quality gate 是两类不同机制。

`AGENTS.md`、`CLAUDE.md`、`CONTRIBUTING.md`、`SECURITY.md` 和各子目录 README 属于仓库协作或运行入口，不在本索引中重复展开；需要修改时仍按其自身职责维护。
