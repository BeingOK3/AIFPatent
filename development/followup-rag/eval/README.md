# RAG 离线评测

本目录固定首次报告与追问共用的 `rag-eval-dataset-v1` 数据契约。评测把人工标注的冻结 Version/Chunk 作为外部 ground truth，不允许由待测报告自行证明正确。

`example-*.json` 只用于演示工具和 CI 契约，是合成数据，不代表产品质量基线。真实评测集必须从已获准留存的 Corpus Version 中抽样，并由人工复核相关 Chunk、相关性等级和 Citation quote hash；不得提交 API Key、用户未公开 IDEA、专利全文或模型原始响应。

每个 Case 明确标记 `INITIAL_REPORT` 或 `FOLLOWUP`，并固定：

- 问题与允许访问的 Version；
- 相关 Chunk 及 1～3 级相关性；
- 比较型问题必须覆盖的 Version；
- 需要验证的 Citation Chunk、Version 与原文 hash。

生成 baseline 和 candidate 的观测文件后执行：

```bash
python3 tools/rag_eval.py evaluate \
  --dataset development/followup-rag/eval/example-dataset.json \
  --run development/followup-rag/eval/example-lexical-run.json \
  --output /tmp/lexical-summary.json --cutoff 2

python3 tools/rag_eval.py evaluate \
  --dataset development/followup-rag/eval/example-dataset.json \
  --run development/followup-rag/eval/example-hybrid-run.json \
  --output /tmp/hybrid-summary.json --cutoff 2

python3 tools/rag_eval.py compare \
  --baseline /tmp/lexical-summary.json \
  --candidate /tmp/hybrid-summary.json \
  --min-recall-delta 0.25 --min-ndcg-delta 0
```

`evaluate` 默认要求范围越界和 Citation 违规均为零；召回率、nDCG、Citation recall 门槛可按数据集成熟度显式提高。`compare` 要求候选不降低 recall、nDCG、Citation recall，且不新增范围/Citation 违规；可用 delta 参数要求实际提升。

真实 PostgreSQL Corpus 的词法 baseline 可直接采集；DSN 默认只从当前进程的 `AIFPATENT_POSTGRES_DSN` 读取，输出不包含 DSN、正文或凭证：

```bash
python3 tools/rag_eval.py collect-postgres-lexical \
  --dataset /path/to/curated-dataset.json \
  --output /tmp/postgres-lexical-run.json \
  --run-id lexical-20260722 --system-version GIT_SHA --limit 10
```
