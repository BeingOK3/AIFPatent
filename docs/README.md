# 当前产品文档

`docs/` 只保存与当前实现一致、面向使用和维护的产品文档，不保存已经完成的迁移计划、提交台账、旧项目设计或追加式开发历史。

当前文件：

- `aifpatent-architecture.md`：已实现 IDEA 评审、报告内追问、专利态势分析和部署栈的当前架构、数据流、故障语义及明确限制。
- `aifpatent-architecture-diagram.md`：当前全系统可编辑 Mermaid 架构图，覆盖 IDEA、专利态势、RAG、存储和容器部署。

其他资料：

- 根 `README.md`：安装、启动、使用、CLI 和接口入口；
- `development/core/`：AIFPatent 核心系统开发与迁移历史；
- `development/followup-rag/`：追问、耐久语料和混合 RAG 的设计、实现与验收历史。
- `development/landscape/`：专利态势分析的设计、实现、故障修复和验收历史。

文档与代码不一致时，应先以代码、配置 Schema 和测试确认当前行为，再更新本目录文档。
