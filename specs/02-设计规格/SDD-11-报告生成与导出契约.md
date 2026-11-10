# SDD-11 · T26 报告生成与导出契约

> 2026-10-19，D，v1.0，随功能 PR 评审；依据 SRS-02 F5.5、SDD-02 report、SDD-03 §4.7/§5.4。无数据库结构变更。

## 1. 统计口径

- 日期为上海本地自然日，首尾包含，最多 366 天。选择该资产在区间内**创建的任务**；不以风险最后更新时间筛选任务。
- 既有 TIMESTAMP 无时区列须区分来源：任务创建时间默认按 UTC 存储读取（对齐 C 仓储/数据库默认时钟），查询边界从上海转换为 UTC；仓储可显式选择 `task_timestamp_zone="Asia/Shanghai"` 对接另行确认的任务写入器。Finding 的 first_seen/last_seen 按 T21 既定上海墙钟读取；本模块 Report.created_at 显式写上海时间。禁止在同一来源列混用时区；带时区处理时钟会转换到上海时间。
- 在同一数据库读取快照中汇总资产、当前页面数、任务和其 Finding。最多 1000 个任务、1000 条 Finding；超过上限明确拒绝，不静默截断。页面数、任务状态和风险状态均为生成时现状，不能还原历史时点状态。
- findingRows 是任务内 Finding 条数；distinctRisks 按资产内 dedup_key 去重，代表项取 last_seen、id 最大的记录。严重度/状态分布按代表项统计；activeRisks 仅 OPEN/CONFIRMED。误报和已修复单列，不宣称全站安全。
- 趋势为每天创建任务数、当日任务结果的去重风险数（各天不能相加作为区间去重总数）；明细记录所有来源 Finding ID/任务 ID，支持核对。
- 证据仅提供 SHA256 指纹（优先 source_sha256，否则计算 evidence 指纹）及受控标识，不输出原文、URL 路径/查询、原始工具 JSON 或自由文本。影响/建议使用本模块固定分类模板，不冒充尚待合入的 T23 分级结果。

## 2. 接口与权限

- POST `/api/reports/generate`：`{scopeId,periodBegin,periodEnd}`，同步生成，完成返回 `{reportId,status:"DONE",filePath:"/api/reports/{id}/file?format=html"}`。这是 SDD-03 异步示例的有界同步实现；失败不会返回虚假的 GENERATING 成功。
- GET `/api/reports`：可选 scopeId，page 默认 1、pageSize 默认 20 最大 100；仅列授权且启用资产的报告，包含 FAILED/GENERATING。
- GET `/api/reports/{id}`：公开元数据、下载地址；不返回服务器路径或存储令牌。
- GET `/api/reports/{id}/file?format=html|pdf`：默认 html，附件响应，私有禁止缓存，HTML 禁止脚本和外部资源；两种格式来自同一冻结快照。
- 角色 SEC_OPS/RULE_ADMIN 均允许生成和读取。复用可信 Principal；每次校验资产授权、启用/删除状态。默认无身份即拒绝。当前 B 的 JWT 已合入，但用户到资产的可信授权映射尚无基线，路由以工厂交付，暂不默认挂载；不能将全部资产直接授权给登录用户。

## 3. 事务、存储与故障

- PostgreSQL 在 REPEATABLE READ 事务读取快照；SQLite 显式读事务供隔离验收。初始快照读取、GENERATING 行和开始审计一起提交。
- 事务外生成静态 HTML 和真正的中文 PDF，写入私有暂存目录，然后原子改名发布不可变 bundle。完成事务再次校验资产，置 DONE 并写成功审计。生成失败置 FAILED、无下载地址；失败审计错误不冒充成功。
- 现有 file_path 保存随机令牌及 manifest SHA256，manifest 保存 snapshot/html/pdf 的长度和 SHA256。下载只读取受控文件名，拒绝目录穿越、符号链接、文件缺失、摘要不一致或超限文件。摘要用于完整性校验，不是数字签名。
- 文件系统与数据库不是分布式事务：进程中断可能留下 GENERATING 或无引用 bundle。未 DONE 的文件不可下载；运维按数据库引用清理孤儿，不能盲删可能已提交的文件。同步任务暂无自动恢复、作业队列或跨进程重试保证。
- 文件目录只应由服务账号访问；不提供静态目录。生成/下载审计仅记录报告/范围 ID、格式、结果，不写内容。

## 4. 验收边界

私有 SQLite、合成数据、真实 ASGI 下载、HTML/PDF 内容/中文排版与损坏拒绝可独立验收。生产 PostgreSQL 隔离、真实资产授权、前端按钮和公网检测到报告全链路另行联调。本实现不依赖 T23/T24/T25 尚待评审的功能分支。
