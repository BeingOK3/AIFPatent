# 专利态势重构开发域

本目录只保存新版本专利态势的有效产品基线、分类标准和开发设计。旧 MVP 架构、
历史验收、增量修复方案和追加开发日志已经移除；需要追溯时使用 Git 历史。

## 当前事实源

1. [`spec.md`](spec.md)：已确认的产品需求，是“做什么”的唯一事实源；
2. [`classify.md`](classify.md)：当前技术分类基线，运行时必须使用版本化快照；
3. [`development.md`](development.md)：技术架构和实施规格，回答“怎么做”。

优先级为：产品语义以 `spec.md` 为准；分类成员是否合法以对应版本的
`classify.md` 为准；实现细节以评审通过的 `development.md` 为准。开发文档不得
反向改变产品需求。

## 当前状态

- `spec.md` 已确认为 `approved-product-baseline`；
- `classify.md` 已纳入新开发域，实现必须增加稳定版本和机器校验；
- `development.md` 已确认为 `approved-technical-baseline`；
- 开发按 `development.md` 的 WP-0～WP-10 顺序推进，稳定性发布门高于功能完成度。

当前代码仍可能体现旧实现。源码现状只用于迁移审计，不能用来反推或覆盖新需求。
