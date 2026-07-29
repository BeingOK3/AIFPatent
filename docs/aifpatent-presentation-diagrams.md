# AIFPatent PPT 配图：可编辑 Mermaid 流程图

本文件为 PPT 大纲配套图源。每张图均是 Mermaid，可在支持 Mermaid 的 Markdown 编辑器、Mermaid Live Editor 或 draw.io 的 Mermaid 导入中渲染，再导出 SVG/PNG 放入 PPT。

建议优先导出 SVG，以便在 PPT 中放大不失真。

## 图 1：Skill、Workflow 和数据事实层

对应 PPT 第 3 页“为什么只靠 Skill 或 Prompt 不够”。

~~~mermaid
flowchart TB
    U[用户 IDEA] --> P[Skill 或 Prompt]
    P --> M[模型单次回答]
    M --> R1[结果可能流畅]
    M -. 无法单独保证 .-> X1[没有固定步骤]
    M -. 无法单独保证 .-> X2[没有全文或版本事实]
    M -. 无法单独保证 .-> X3[没有可验证 Citation]
    M -. 无法单独保证 .-> X4[失败后无法恢复]

    U --> W1[Workflow]
    W1 --> W2[按顺序执行]
    W2 --> W3[检索和抓全文]
    W3 --> W4[校验和保存状态]
    W4 --> D1[(PostgreSQL)]
    W4 --> D2[(MinIO S3)]
    D1 --> O[可回查 可重试 可复现]
    D2 --> O

    style P fill:#E8F1FF,stroke:#377DFF
    style M fill:#E8F1FF,stroke:#377DFF
    style X1 fill:#FDE8E7,stroke:#D64545
    style X2 fill:#FDE8E7,stroke:#D64545
    style X3 fill:#FDE8E7,stroke:#D64545
    style X4 fill:#FDE8E7,stroke:#D64545
    style W1 fill:#E9F8EF,stroke:#2F9E44
    style W2 fill:#E9F8EF,stroke:#2F9E44
    style W3 fill:#E9F8EF,stroke:#2F9E44
    style W4 fill:#E9F8EF,stroke:#2F9E44
    style D1 fill:#FFF4D6,stroke:#D68B00
    style D2 fill:#FFF4D6,stroke:#D68B00
    style O fill:#E9F8EF,stroke:#2F9E44
~~~

### 讲解提示

左半边故意画出断点：Skill/Prompt 可以约束一次模型回答，却不能自己产生工作流状态、全文版本、可验证引用和失败恢复。右半边才是项目增加的 Workflow 和数据事实层。

---

## 图 2：IDEA 的 11 步工作流

对应 PPT 第 4 页“从 Skill 到 Workflow”。

~~~mermaid
flowchart TB
    A[用户 IDEA]

    subgraph P1[输入和检索]
        direction LR
        B[1 输入准备] --> C[2 解析 IDEA]
        C --> D[3 验证特征]
        D --> E[4 规划检索式]
        E --> F[5 检索候选]
    end

    subgraph P2[证据和评审]
        direction TB
        G[6 规范化和抓全文] --> H[7 精读专利]
        H --> I[8 新颖性判断]
        I --> J[9 创造性分析]
    end

    subgraph P3[决策和输出]
        direction RL
        K[10 价值评估] --> L[11 审计和报告]
    end

    A --> B
    F --> G
    J --> K
    L --> M[可回查报告]
    M -. 新 IDEA 创建新 Run .-> A

    style A fill:#F3F4F6,stroke:#6B7280
    style P1 fill:#EEF4FF,stroke:#377DFF
    style P2 fill:#EEF9F1,stroke:#2F9E44
    style P3 fill:#FFF7E6,stroke:#D68B00
    style M fill:#E9F8EF,stroke:#2F9E44
~~~

### 讲解提示

三块区域避免把 11 步挤成一条超长直线：输入和检索、证据和评审、决策和输出围成一次 Run 的闭环。报告完成后，下一份 IDEA 会创建新的 Run 再从输入开始；模型不能选择或跳过步骤。

---

## 图 3：从多检索式到全文精读候选的漏斗

对应 PPT 第 5–6 页“检索逻辑”和“精读专利怎样选”。

~~~mermaid
flowchart TB
    A[IDEA Feature 与技术术语] --> B[模型规划<br/>8–16 条中英文检索式]
    B --> C[多个检索 Provider]
    C --> D[大量 Search Hits]

    D --> E[按公开号 / 申请号 / Family ID<br/>合并与去重]
    E --> F[标题 + Snippet<br/>术语覆盖相关性评分]
    F --> G{日期合格？<br/>公开号可识别？}
    G -- 否 --> X[排除并记录原因]
    G -- 是 --> H[按模式与范围宽度<br/>选择精读目标数]

    H --> I[首选全文抓取]
    I --> J{有摘要且有<br/>独立权利要求？}
    J -- 否 / 抓取失败 --> K[同轮候补补位]
    K --> I
    J -- 是 --> L[进入深读 Corpus]

    classDef external fill:#E8F1FF,stroke:#377DFF;
    classDef program fill:#E9F8EF,stroke:#2F9E44;
    classDef reject fill:#FDE8E7,stroke:#D64545;
    classDef corpus fill:#FFF4D6,stroke:#D68B00;
    class B,C,D external;
    class E,F,G,H,I,J,K program;
    class X reject;
    class L corpus;
~~~

### 讲解提示

这里有两个层次：摘要筛选只决定“值不值得抓全文”；最终结论只能来自抓到全文后的 Chunk 证据。候补补位是为了在抓取失败时仍尽可能达到精读下限。

---

## 图 4：PostgreSQL 与 MinIO 的数据边界

对应 PPT 第 7 页“为什么需要 PostgreSQL + MinIO”。

~~~mermaid
flowchart LR
    F[抓取到的专利全文] --> N[规范化并计算 SHA-256]
    N --> S3[(MinIO / S3<br/>完整不可变 JSON Blob)]
    N --> PG

    subgraph PG[PostgreSQL]
        P1[patent_documents<br/>专利身份与元数据]
        P2[patent_document_versions<br/>冻结版本]
        P3[corpus_blobs<br/>hash、object_key、状态]
        P4[patent_chunks<br/>可检索段落]
        P5[retrieval hits / Context / Citation]
        P1 --> P2 --> P3
        P2 --> P4 --> P5
    end

    P3 -. object_key + hash .-> S3
    S3 -. 原文校验与回查 .-> P5

    classDef object fill:#FFF4D6,stroke:#D68B00;
    classDef relational fill:#E9F8EF,stroke:#2F9E44;
    class S3 object;
    class PG,P1,P2,P3,P4,P5 relational;
~~~

### 讲解提示

MinIO 不负责理解专利语义，只保存完整不可变对象；PostgreSQL 才负责版本、Chunk、检索和引用关系。数据库里保存的是“原文在哪里、如何使用”，不是让所有业务关系埋在大 JSON 里。

---

## 图 5：首次报告 RAG 的动态装载

对应 PPT 第 8–10 页“RAG 原理、Chunk、动态装载”。

~~~mermaid
flowchart TB
    A[冻结专利 P1 的 Chunk] --> B[摘要]
    A --> C[独立 Claim]
    A --> D[说明书段落]

    F[IDEA 特征 F1 到 F7] --> G[对 P1 Version 分别检索]
    B --> H[合并检索命中]
    C --> H
    D --> H
    G --> H

    H --> I[强制加入摘要和独立 Claim]
    I --> J{是否在 token 预算内}
    J -- 是 --> K[Context P1: C1 到 Cn]
    J -- 否 --> L[记录排除原因]
    K --> M[模型逐项判断 F1 到 F7]
    M --> N[校验 C 引用]
    N --> O[P1 的 Feature 映射]

    style A fill:#FFF4D6,stroke:#D68B00
    style B fill:#FFF4D6,stroke:#D68B00
    style C fill:#FFF4D6,stroke:#D68B00
    style D fill:#FFF4D6,stroke:#D68B00
    style F fill:#E8F1FF,stroke:#377DFF
    style G fill:#E8F1FF,stroke:#377DFF
    style H fill:#E9F8EF,stroke:#2F9E44
    style I fill:#E9F8EF,stroke:#2F9E44
    style J fill:#E9F8EF,stroke:#2F9E44
    style K fill:#E9F8EF,stroke:#2F9E44
    style L fill:#FDE8E7,stroke:#D64545
    style M fill:#F3E8FF,stroke:#8B5CF6
    style N fill:#E9F8EF,stroke:#2F9E44
    style O fill:#E9F8EF,stroke:#2F9E44
~~~

### 讲解提示

以 7 个 Feature 为例，对同一篇专利会发生 7 次受范围限制的 Chunk 检索；但它们最终合成 P1 的一份 Context，发起一次模型精读。若精读 15 篇专利，就是 15 份独立 Context，不是把 15 篇全文一起塞进模型。

---

## 图 6：首次报告与追问的 Context 边界

对应 PPT 第 12 页“追问 RAG 与首次报告的区别”。

~~~mermaid
flowchart TB
    A[首次报告]
    A --> B[F1 到 Fn 对单篇专利 Version 检索]
    B --> C[一个专利一份 Context]
    C --> D[一次模型精读]
    D --> E[单篇专利 Feature 映射]

    E --> F[用户提出追问]
    F --> G[生成查询改写]
    G --> H[只检索冻结 Version 范围]
    H --> I[一个追问 Context]
    I --> J[可比较多篇已选专利]
    J --> K[回答重合 差异 或规避问题]

    style A fill:#E8F1FF,stroke:#377DFF
    style B fill:#E8F1FF,stroke:#377DFF
    style C fill:#E8F1FF,stroke:#377DFF
    style D fill:#E8F1FF,stroke:#377DFF
    style E fill:#E8F1FF,stroke:#377DFF
    style F fill:#E9F8EF,stroke:#2F9E44
    style G fill:#E9F8EF,stroke:#2F9E44
    style H fill:#E9F8EF,stroke:#2F9E44
    style I fill:#E9F8EF,stroke:#2F9E44
    style J fill:#E9F8EF,stroke:#2F9E44
    style K fill:#E9F8EF,stroke:#2F9E44
~~~

### 讲解提示

首次报告的核心是“逐件专利判定”，所以严格一篇一份 Context；追问常需要比较多篇已冻结专利，因此同一 Context 可以包含多篇，但只能引用本轮重新检索出的 C#。

---

## 图 7：Citation 可回查闭环

对应 PPT 第 11 页或备份页 A。

~~~mermaid
flowchart LR
    A[报告中的结论] --> B[Citation：C7]
    B --> C[report_model_citations]
    C --> D[patent_chunks<br/>具体段落/Claim]
    D --> E[patent_document_versions<br/>冻结版本]
    E --> F[corpus_blobs<br/>hash + object_key]
    F --> G[(MinIO / S3<br/>完整原文)]
    G --> H[核对原文、偏移与 SHA-256]

    classDef report fill:#F3E8FF,stroke:#8B5CF6;
    classDef relation fill:#E9F8EF,stroke:#2F9E44;
    classDef object fill:#FFF4D6,stroke:#D68B00;
    class A,B report;
    class C,D,E,F,H relation;
    class G object;
~~~

### 讲解提示

这张图可以回答最关键的质疑：为什么相信这份报告？因为 C7 不是文本装饰，而是一条可以穿过数据库关系回到 MinIO 原文对象、再校对具体 offset 和哈希的证据链。

---

## 图 8：项目现状与演进路线

对应 PPT 第 14 页“现状与不足”。

~~~mermaid
flowchart LR
    A[当前：词法 RAG 和证据闭环] --> B[第一步：人工标注评测]
    B --> C[第二步：跨语言 Embedding]
    C --> D[第三步：验证 Hybrid RRF]
    D --> E[第四步：加入 Reranker]
    E --> F[第五步：优化 Chunk 和复用 Landscape]

    A1[当前基础：版本冻结 Chunk Context Citation] --> A
    B1[指标：Recall at K Citation precision 答案正确率] --> B
    C1[目标：中文 IDEA 与英文专利] --> C
    D1[目标：证明 Hybrid 优于 LEXICAL_ONLY] --> D
    E1[目标：最佳证据排在前面] --> E
    F1[目标：长 Claim 跨段上下文 统一语料] --> F

    style A fill:#E9F8EF,stroke:#2F9E44
    style A1 fill:#E9F8EF,stroke:#2F9E44
    style B fill:#E8F1FF,stroke:#377DFF
    style B1 fill:#E8F1FF,stroke:#377DFF
    style C fill:#E8F1FF,stroke:#377DFF
    style C1 fill:#E8F1FF,stroke:#377DFF
    style D fill:#E8F1FF,stroke:#377DFF
    style D1 fill:#E8F1FF,stroke:#377DFF
    style E fill:#E8F1FF,stroke:#377DFF
    style E1 fill:#E8F1FF,stroke:#377DFF
    style F fill:#E8F1FF,stroke:#377DFF
    style F1 fill:#E8F1FF,stroke:#377DFF
~~~

### 讲解提示

最后强调路线顺序：先做评测，再换模型或加 reranker。否则很难证明系统是“真的更会找证据”，还是只是“看起来回答更好”。
