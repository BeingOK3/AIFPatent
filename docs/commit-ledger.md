# AIFPatent 提交台账

本文件按里程碑记录提交主题、范围和验证证据。Git 哈希在提交产生后由后续台账提交补录；Git 自身仍是完整历史的权威来源。

| 里程碑 | 提交主题 | 范围 | 验证 |
|---|---|---|---|
| AIF-BOOT-001 | `chore: bootstrap independent AIFPatent repository` (`ae2331c`) | 从 AI4Patent 当前工作树建立无旧 Git 历史、无运行数据、无凭证的新项目基线 | `compileall`、密钥模式扫描、目录排除检查 |
| AIF-OC-001 | `refactor: remove OpenCode runtime dependency` (`0a32493`) | 删除旧通用任务接口、运行客户端、健康探针、下载逻辑与认证文件回退；CLI 迁入 `tools/` | 179 项离线测试、`compileall`、依赖引用扫描 |
| AIF-GRAPH-001 | `feat: migrate IDEA workflow to LangGraph and LangChain` (`20587d9`) | 固定 11 节点 StateGraph、SQLite Checkpointer、节点重试/超时/取消、LangChain ChatOpenAI BYOK 适配、前端节点事件 | 181 项离线测试、拓扑与密钥不落盘测试、编译/JS/Shell/pip 检查 |
| AIF-STABILITY-001 | `fix: harden inventive evidence correction and graph concurrency` (`5e44e1f`) | 创造性 D2 证据绑定定向纠错、双 thread 并发、节点取消、AIFPatent 品牌与真实 E2E | 184 项离线测试、真实方舟结构化调用、真实 quick 11/11、Manifest/中文/评分/链接/脱敏检查 |
| AIF-DELIVERY-001 | `docs: record migration delivery evidence` | 补录远端、提交哈希、原项目零改动哈希和交付状态 | `git diff --check`、远端分支同步、AI4Patent 三项基线比对 |
