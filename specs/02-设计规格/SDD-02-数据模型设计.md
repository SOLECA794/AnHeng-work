# SDD-02 · 数据模型设计

- 规格状态：基线 v1.0；v1.1/v1.2 变更草案随功能分支评审
- 需求依据：SRS-02 F1–F7；设计依据：SDD-01 检测流水线

## 1. 设计约定

- 数据库：PostgreSQL 16；
- 主键：统一 `BIGINT` 自增，命名 `id`；
- 时间字段：`created_at`、`updated_at`（TIMESTAMP）；`deleted` 逻辑删除（0 正常，1 删除）；
- 命名：表名与字段名小写下划线；状态字段 VARCHAR 语义值大写；
- 脚本纳入版本管理（`db/schema.sql`、`db/data.sql`、`db/migration/`），可重复执行（幂等）；
- JSON 字段用 JSONB；金额/体积类无；证据类长文本用 TEXT。

## 2. 实体关系概览

- 资产侧：`sys_user` → `auth_scope`（授权资产）→ `site_page`（页面）；
- 任务侧：`schedule`（巡检计划）→ `detection_task`（检测任务，关联资产/页面）；
- 结果侧：`finding`（风险结果，关联任务/页面）→ `alert`（告警）→ `baseline`（页面基线）；
- 规则侧：`sensitive_rule`（敏感信息定义）→ finding 引用；
- 报告与审计：`report`（报告）、`agent_log`（智能体/操作审计）。

## 3. 表清单

| 表名 | 用途 |
| --- | --- |
| sys_user | 用户（运维/规则管理员/系统管理员） |
| auth_scope | 授权资产（域名、范围、凭证、状态） |
| site_page | 站点页面（URL、哈希、发现信息） |
| schedule | 巡检计划（周期、启停） |
| detection_task | 检测任务（触发方式、状态、起止） |
| finding | 风险结果（类型、级别、证据、状态、去重键） |
| alert | 告警（关联 finding） |
| baseline | 页面内容基线（用于篡改对比） |
| sensitive_rule | 敏感信息规则（关键词/正则/文件类型/样例） |
| report | 巡检报告 |
| agent_log | 智能体工具调用与操作审计 |

## 4. 表结构明细

### 4.1 sys_user（用户）

| 字段 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| id | BIGINT | PK | 用户 ID |
| username | VARCHAR(50) | UNIQUE, NOT NULL | 账号 |
| password | VARCHAR(100) | NOT NULL | 带算法标记的加盐密码哈希；T09 实现为 scrypt（N=16384、r=8、p=1），不保存明文 |
| nickname | VARCHAR(50) | 可空 | 昵称 |
| role | VARCHAR(20) | NOT NULL | SEC_OPS / RULE_ADMIN / ADMIN |
| status | TINYINT | DEFAULT 1 | 1 正常，0 停用 |
| deleted | TINYINT | DEFAULT 0 | 逻辑删除 |
| created_at | TIMESTAMP | NOT NULL | 创建时间 |
| updated_at | TIMESTAMP | NOT NULL | 更新时间 |

索引：`idx_user_role(role)`。

### 4.2 auth_scope（授权资产）

| 字段 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| id | BIGINT | PK | 资产 ID |
| domain | VARCHAR(100) | UNIQUE, NOT NULL | 授权域名 |
| scope_type | VARCHAR(20) | NOT NULL | WHOLE / PATH / SUBDOMAIN |
| scope_value | VARCHAR(500) | 可空 | 范围值（路径前缀/子域规则） |
| auth_note | VARCHAR(500) | 可空 | 授权备注/凭证信息（脱敏存储） |
| owner | VARCHAR(50) | 可空 | 归属部门/负责人 |
| status | VARCHAR(20) | NOT NULL | ENABLED / DISABLED |
| deleted | TINYINT | DEFAULT 0 | 逻辑删除 |
| created_at | TIMESTAMP | NOT NULL | 创建时间 |
| updated_at | TIMESTAMP | NOT NULL | 更新时间 |

索引：`idx_scope_status(status)`。

### 4.3 site_page（站点页面）

| 字段 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| id | BIGINT | PK | 页面 ID |
| scope_id | BIGINT | NOT NULL | 所属授权资产 |
| url | VARCHAR(500) | NOT NULL | 页面 URL（规范化） |
| normalized_url | VARCHAR(500) | UNIQUE, NOT NULL | 规范化 URL（去参数序/片段） |
| title | VARCHAR(200) | 可空 | 页面标题 |
| content_hash | VARCHAR(64) | 可空 | 内容哈希（篡改对比） |
| depth | TINYINT | DEFAULT 0 | 发现深度 |
| status | VARCHAR(20) | NOT NULL | ACTIVE / CHANGED / GONE |
| first_seen_at | TIMESTAMP | NOT NULL | 首次发现时间 |
| last_seen_at | TIMESTAMP | NOT NULL | 最近发现时间 |
| created_at | TIMESTAMP | NOT NULL | 创建时间 |

索引：`idx_page_scope(scope_id)`、`idx_page_status(status)`。

### 4.4 schedule（巡检计划）

| 字段 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| id | BIGINT | PK | 计划 ID |
| name | VARCHAR(100) | NOT NULL | 计划名 |
| scope_id | BIGINT | NOT NULL | 关联授权资产 |
| cron | VARCHAR(100) | 可空 | cron 表达式（自定义周期） |
| interval_type | VARCHAR(20) | 可空 | DAILY / WEEKLY / MANUAL |
| execute_window | VARCHAR(50) | 可空 | 执行时段（如 02:00-06:00） |
| enabled | TINYINT | DEFAULT 1 | 启停 |
| last_run_at | TIMESTAMP | 可空 | 上次执行时间 |
| next_run_at | TIMESTAMP | 可空 | 下次执行时间 |
| created_at | TIMESTAMP | NOT NULL | 创建时间 |
| updated_at | TIMESTAMP | NOT NULL | 更新时间 |

索引：`idx_schedule_scope(scope_id, enabled)`。

### 4.5 detection_task（检测任务）

| 字段 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| id | BIGINT | PK | 任务 ID |
| schedule_id | BIGINT | 可空 | 来源计划（手动为空） |
| scope_id | BIGINT | NOT NULL | 巡检资产 |
| trigger_type | VARCHAR(20) | NOT NULL | MANUAL / SCHEDULED / AGENT |
| status | VARCHAR(20) | NOT NULL | QUEUED / DISCOVERING / SCANNING / ANALYZING / DONE / FAILED |
| retry_count | TINYINT | DEFAULT 0 | 重试次数 |
| started_at | TIMESTAMP | 可空 | 开始时间 |
| finished_at | TIMESTAMP | 可空 | 结束时间 |
| created_at | TIMESTAMP | NOT NULL | 创建时间 |

索引：`idx_task_scope(scope_id, status)`、`idx_task_time(created_at)`。

### 4.6 finding（风险结果）

| 字段 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| id | BIGINT | PK | 风险 ID |
| task_id | BIGINT | NOT NULL | 来源任务 |
| page_id | BIGINT | 可空 | 关联页面 |
| scope_id | BIGINT | NOT NULL | 关联资产 |
| finding_type | VARCHAR(30) | NOT NULL | VULN / WEAK_CONFIG / MALICIOUS_LINK / MALICIOUS_SCRIPT / TAMPER / SENSITIVE |
| severity | VARCHAR(10) | NOT NULL | CRITICAL / HIGH / MEDIUM / LOW / INFO |
| title | VARCHAR(200) | NOT NULL | 标题 |
| url | VARCHAR(500) | 可空 | 目标 URL |
| evidence | TEXT | 可空 | 证据（已脱敏） |
| impact | TEXT | 可空 | 影响说明 |
| suggestion | TEXT | 可空 | 修复建议 |
| tool_name | VARCHAR(50) | NOT NULL | 来源工具 |
| raw_data | JSONB | 可空 | 原始输出（归一化保留） |
| dedup_key | VARCHAR(64) | NOT NULL | 去重键 |
| status | VARCHAR(20) | NOT NULL | OPEN / CONFIRMED / FALSE_POSITIVE / FIXED / RETESTED |
| occurrence | INT | DEFAULT 1 | 累计出现次数 |
| first_seen | TIMESTAMP | NOT NULL | 首次发现 |
| last_seen | TIMESTAMP | NOT NULL | 最近发现 |
| created_at | TIMESTAMP | NOT NULL | 创建时间 |
| updated_at | TIMESTAMP | NOT NULL | 更新时间 |

约束：`UNIQUE uk_finding_dedup(task_id, dedup_key)`。
索引：`idx_finding_scope(scope_id)`、`idx_finding_type(type, severity)`、`idx_finding_status(status)`。

### 4.7 alert（告警）

| 字段 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| id | BIGINT | PK | 告警 ID |
| finding_id | BIGINT | NOT NULL | 关联风险 |
| severity | VARCHAR(10) | NOT NULL | 级别 |
| title | VARCHAR(200) | NOT NULL | 标题 |
| content | VARCHAR(500) | 可空 | 内容摘要 |
| status | VARCHAR(20) | NOT NULL | NEW / HANDLED / CLOSED |
| read_status | TINYINT | DEFAULT 0 | 0 未读，1 已读 |
| created_at | TIMESTAMP | NOT NULL | 创建时间 |

索引：`idx_alert_finding(finding_id)`、`idx_alert_status(status)`。

### 4.8 baseline（页面基线）

| 字段 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| id | BIGINT | PK | 基线 ID |
| page_id | BIGINT | NOT NULL | 关联页面 |
| content_hash | VARCHAR(64) | NOT NULL | 基线内容哈希 |
| created_at | TIMESTAMP | NOT NULL | 创建时间 |

索引：`idx_baseline_page(page_id)`。

### 4.9 sensitive_rule（敏感信息规则）

| 字段 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| id | BIGINT | PK | 规则 ID |
| name | VARCHAR(100) | NOT NULL | 规则名 |
| rule_type | VARCHAR(20) | NOT NULL | KEYWORD / REGEX / FILETYPE / SAMPLE |
| pattern | TEXT | NOT NULL | 关键词 / 正则 / 文件类型 / 样例指纹 |
| sample_demo | TEXT | 可空 | 示例（展示脱敏） |
| scope_bind | VARCHAR(100) | 可空 | 绑定资产（空为全局） |
| severity | VARCHAR(10) | NOT NULL | 命中默认级别 |
| enabled | TINYINT | DEFAULT 1 | 启停 |
| created_at | TIMESTAMP | NOT NULL | 创建时间 |
| updated_at | TIMESTAMP | NOT NULL | 更新时间 |

索引：`idx_rule_type(rule_type, enabled)`。

### 4.10 report（巡检报告）

| 字段 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| id | BIGINT | PK | 报告 ID |
| scope_id | BIGINT | NOT NULL | 报告范围 |
| period_begin | DATE | NOT NULL | 统计开始 |
| period_end | DATE | NOT NULL | 统计结束 |
| report_type | VARCHAR(20) | NOT NULL | PERIODIC / MANUAL |
| file_path | VARCHAR(255) | 可空 | 导出文件路径/URL |
| status | VARCHAR(20) | NOT NULL | GENERATING / DONE / FAILED |
| created_at | TIMESTAMP | NOT NULL | 生成时间 |

索引：`idx_report_scope(scope_id)`。

### 4.11 agent_log（智能体/操作审计）

| 字段 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| id | BIGINT | PK | 日志 ID |
| user_id | BIGINT | 可空 | 操作用户 |
| task_id | BIGINT | 可空 | 关联检测任务 |
| log_type | VARCHAR(20) | NOT NULL | AGENT_TOOL / OPERATION / SCHEDULE / LOGIN；T09/T10 增补 AUTH（凭证生命周期）、AGENT（平台请求） |
| tool_name | VARCHAR(50) | 可空 | 工具名（工具调用） |
| action | VARCHAR(100) | NOT NULL | 动作描述 |
| params_summary | VARCHAR(500) | 可空 | 参数摘要（脱敏） |
| result_status | VARCHAR(20) | NOT NULL | SUCCESS / FAILED / BLOCKED / CANCELLED（流被调用方关闭） |
| latency_ms | INT | 可空 | 耗时 |
| created_at | TIMESTAMP | NOT NULL | 时间 |

索引：`idx_log_user(user_id)`、`idx_log_type(log_type)`、`idx_log_time(created_at)`。

## 5. 一致性设计汇总

| 场景 | 保障 |
| --- | --- |
| 同一任务同风险不重复 | finding 唯一约束 (task_id, dedup_key) |
| 页面不重复 | site_page 规范化 URL 唯一 |
| 授权域名唯一 | auth_scope.domain 唯一 |
| 告警不重复 | 应用层按 dedup_key + 状态控制首次告警 |
| 基线唯一 | baseline.page_id 唯一（重建采用删除重建） |
| 逻辑删除 | deleted 字段 + 查询过滤 |
| 幂等落库 | dedup_key / 唯一约束 + ON CONFLICT DO NOTHING |

## 6. 初始化数据（db/data.sql）

- 开发管理员：首次启动时创建 `admin`，密码从服务端 `DEV_ADMIN_PASSWORD` 读取；已存在时不覆盖。生产不自动创建账号；
- 运维/规则管理员/生产管理员由 `scripts/create_user.py` 显式创建，密码隐藏输入，不在初始化 SQL 或 Git 中保存固定口令；
- 示例授权资产：演示域名（如 `demo.example.com`，仅演示环境）；
- 敏感信息规则样例：关键词类（如"密码"、密钥特征）、正则类（手机号/邮箱/身份证）、文件类型类（.env / .git / .sql / .bak）；
- 内置可选公共规则，可停用。

【待确认】正式演示域名与样例规则由安全运维人员/规则管理员在演示准备阶段录入。

## 7. 脚本管理

- `db/schema.sql`：建表（含索引、唯一约束），可重复执行；
- `db/data.sql`：初始化数据（幂等：INSERT ... ON CONFLICT DO NOTHING）；
- `db/migration/Vxxx__desc.sql`：增量脚本；每次变更同步更新本文档对应表并登记变更记录。

## 8. 变更记录

T09 退出吊销复用 `agent_log`：`log_type=AUTH`、`action=logout`、`params_summary=sha256(jti)`；密码修改记录 action=password。平台请求记录 `log_type=AGENT`，摘要只存 providerCode。以上变化不新增表/列、不改变 SQL 结构；scrypt 自描述哈希长度 73，兼容现有 VARCHAR(100)。v1.2 纠正前一版 B 认证代码与 BCrypt 文档的既有不一致；既有 BCrypt 账号不能直接导入当前实现，须通过受控方式重新设置密码。

| 版本 | 日期 | 变更人 | 变更内容 | 影响范围 |
| --- | --- | --- | --- | --- |
| v1.0 | 2026-10-09 | 课题立项 | 基线发布（资产/任务/风险/告警/报告/审计模型） | 全量 |
| v1.2 | 2026-10-19 | B（待评审） | 对齐既有 scrypt 认证实现，明确 AUTH/AGENT/CANCELLED 审计语义及无固定口令的账号初始化方式；表结构无需迁移 | T09/T10、§4.1/§4.11/§6 |
| v1.1 | 2026-10-12 | C 组（待评审） | `finding_type` 枚举补充 `MALICIOUS_SCRIPT`：挂马脚本类（混淆脚本、外部脚本、注入 iframe、外跳）与 `MALICIOUS_LINK`（页面指向外部的链接）分离。缘由：T40 样例集 v1.0.0 已按此口径标注（B-01/B-02 = MALICIOUS_LINK，B-03/B-04 = MALICIOUS_SCRIPT），原五类枚举无法表达；SRS-02 F3.3 与验收标准 §1 本就按「恶意外链 / 挂马」两项分别要求 | T19、T20、T42 检测效果统计口径 |
