# AIFPatent 提交台账

本文件按里程碑记录提交主题、范围和验证证据。Git 哈希在提交产生后由后续台账提交补录；Git 自身仍是完整历史的权威来源。

| 里程碑 | 提交主题 | 范围 | 验证 |
|---|---|---|---|
| AIF-BOOT-001 | `chore: bootstrap independent AIFPatent repository` (`ae2331c`) | 从 AI4Patent 当前工作树建立无旧 Git 历史、无运行数据、无凭证的新项目基线 | `compileall`、密钥模式扫描、目录排除检查 |
| AIF-OC-001 | `refactor: remove OpenCode runtime dependency` | 删除旧通用任务接口、运行客户端、健康探针、下载逻辑与认证文件回退；CLI 迁入 `tools/` | 179 项离线测试、`compileall`、依赖引用扫描 |
