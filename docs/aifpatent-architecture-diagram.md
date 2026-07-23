# AIFPatent 当前系统架构图

> 更新日期：2026-07-23  
> 范围：当前 `develop` 分支已经实现的 IDEA 评审、专利态势分析、首次报告 RAG、证据追问和容器部署。

## 总体架构

```mermaid
flowchart TB
    subgraph Client["用户与入口层"]
        IdeaUI["IDEA 评审页面"]
        LandscapeUI["专利态势分析页面"]
        CLI["CLI / 运维脚本"]
    end

    subgraph Application["应用与编排层 · App 容器"]
        API["FastAPI API<br/>静态页面 · SSE · 健康检查"]
        IdeaGraph["IDEA LangGraph<br/>固定 11 步 · Checkpoint · 重试"]
        LandscapeGraph["Landscape Workflow<br/>固定 8 步 · Checkpoint · 重试"]
        TaskManager["异步任务管理<br/>取消 · 恢复 · 终态控制"]
    end

    subgraph IdeaDomain["IDEA 评审领域"]
        IdeaParse["IDEA 解析<br/>技术问题 · F1…Fn"]
        QueryPlan["AI 查询规划<br/>中英文术语 · 布尔式 · IPC/CPC"]
        Retrieve["候选检索<br/>合并 · 去重 · 相关性初筛"]
        Evidence["全文与证据<br/>摘要 · 独立权利要求 · 说明书"]
        Review["评审与审计<br/>新颖性 · 创造性 · 价值 · 报告"]
    end

    subgraph LandscapeDomain["专利态势分析领域"]
        Expand["方向扩展与友商别名"]
        LandscapeSearch["双语检索与严格过滤"]
        Rank["唯一全集统计<br/>RRF 排序 · 公司平衡 · 精读递补"]
        Analyze["逐件精读 · 技术聚类<br/>公司分布 · 法域 · 全族状态"]
        LandscapeReport["JSON · Markdown · CSV"]
    end

    subgraph RagDomain["语料与 RAG 领域"]
        Ingest["专利语料摄取<br/>版本化 · Chunk · Evidence"]
        Hybrid["混合检索<br/>PostgreSQL 词法 + pgvector + RRF"]
        InitialRag["首次报告 RAG<br/>冻结 Corpus 快照"]
        Followup["证据追问<br/>线程范围 · 检索 · 引用"]
    end

    subgraph Integration["模型与外部检索层"]
        ModelClient["StructuredModelClient<br/>LangChain ChatOpenAI · Pydantic"]
        LLM["OpenAI-compatible LLM<br/>当前默认 DeepSeek"]
        Provider["统一 SearchProvider / ProviderRunner<br/>缓存 · 超时 · 熔断 · 脱敏"]
        Serp["SerpAPI<br/>Google Patents Search + Details"]
        Optional["可选 Provider<br/>Google Patents 直连 · Exa MCP"]
    end

    subgraph Data["数据与基础设施层"]
        IdeaDB[("IDEA 业务 SQLite<br/>Run · 查询 · 命中 · Evidence · 审计")]
        LandscapeDB[("Landscape SQLite<br/>独立 Run · 候选 · 聚类 · 报告")]
        GraphDB[("LangGraph SQLite<br/>轻量执行游标")]
        Postgres[("PostgreSQL + pgvector<br/>语料 · Chunk · 追问 · 引用")]
        ObjectStore[("MinIO / S3<br/>版本化 Blob 与对象")]
        Redis[("Redis<br/>任务队列 · 限流协调")]
        RunStore[("RunStore<br/>报告 · Manifest · SHA-256")]
        Cache[("本地 FIFO Cache<br/>Provider 响应 · 可重建")]
    end

    IdeaUI --> API
    LandscapeUI --> API
    CLI --> API
    API --> TaskManager
    TaskManager --> IdeaGraph
    TaskManager --> LandscapeGraph

    IdeaGraph --> IdeaParse --> QueryPlan --> Retrieve --> Evidence --> Review
    LandscapeGraph --> Expand --> LandscapeSearch --> Rank --> Analyze --> LandscapeReport

    IdeaParse --> ModelClient
    QueryPlan --> ModelClient
    Review --> ModelClient
    Expand --> ModelClient
    Analyze --> ModelClient
    ModelClient --> LLM

    Retrieve --> Provider
    Evidence --> Provider
    LandscapeSearch --> Provider
    Provider --> Serp
    Provider -. "显式启用" .-> Optional
    Provider --> Cache

    Evidence --> Ingest
    Ingest --> Postgres
    Ingest --> ObjectStore
    Postgres --> Hybrid
    Hybrid --> InitialRag
    Hybrid --> Followup
    InitialRag --> Review
    API --> Followup

    IdeaGraph --> IdeaDB
    IdeaGraph --> GraphDB
    Review --> RunStore
    LandscapeGraph --> LandscapeDB
    LandscapeGraph --> GraphDB
    LandscapeReport --> RunStore
    TaskManager --> Redis

    classDef client fill:#edf7ff,stroke:#3578a8,color:#173b55;
    classDef workflow fill:#f2edff,stroke:#6b52a3,color:#35245c;
    classDef domain fill:#eef8f1,stroke:#3c8255,color:#214b31;
    classDef integration fill:#fff5e8,stroke:#b87826,color:#684312;
    classDef data fill:#f7f7f7,stroke:#666,color:#292929;
    class IdeaUI,LandscapeUI,CLI client;
    class API,IdeaGraph,LandscapeGraph,TaskManager workflow;
    class IdeaParse,QueryPlan,Retrieve,Evidence,Review,Expand,LandscapeSearch,Rank,Analyze,LandscapeReport,Ingest,Hybrid,InitialRag,Followup domain;
    class ModelClient,LLM,Provider,Serp,Optional integration;
    class IdeaDB,LandscapeDB,GraphDB,Postgres,ObjectStore,Redis,RunStore,Cache data;
```

## 两条核心业务数据流

```mermaid
flowchart LR
    subgraph Idea["IDEA 评审"]
        I1["发明创意"] --> I2["AI 解析 F1…Fn"]
        I2 --> I3["AI 生成中英文检索式"]
        I3 --> I4["SerpAPI 候选召回"]
        I4 --> I5["标题/摘要初筛"]
        I5 --> I6["摘要 + 独立权利要求 + 说明书"]
        I6 --> I7["特征证据映射"]
        I7 --> I8["新颖性 / 创造性 / 报告"]
    end

    subgraph Landscape["专利态势分析"]
        L1["技术方向 / 友商 / 时间"] --> L2["中英文合并检索式 / 友商别名"]
        L2 --> L3["SerpAPI 召回"]
        L3 --> L4["时间与友商硬过滤"]
        L4 --> L5["跨查询去重的唯一合格全集"]
        L5 --> L6["公司统计 + 候选排序"]
        L6 --> L7["公司保底 + 数量加权 + 同族优先"]
        L7 --> L8["聚类 / 全族状态 / 报告"]
    end
```

## 关键边界

- 大模型负责受 Schema 约束的理解与语义分析，不负责自由改变工作流、绕过证据门或直接操作存储。
- SerpAPI 是当前默认外部专利搜索与详情源；Google Patents 直连和 Exa 保留为显式启用的补充源。
- IDEA 候选召回是 Google Patents 全文关键词检索，本地初筛使用标题与搜索摘要，入选后才核验独立权利要求和说明书。
- 专利态势的公司柱状图使用严格过滤并跨查询去重后的全部唯一合格专利；技术聚类只覆盖成功精读集合。
- SQLite 保存运行与审计事实，PostgreSQL/pgvector 和 MinIO 保存耐久语料与 RAG 数据，LangGraph SQLite 只保存轻量执行状态。
- API Key 不进入 Git、业务数据库、Graph State、报告或浏览器持久存储。
