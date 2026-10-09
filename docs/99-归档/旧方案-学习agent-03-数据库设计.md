# 03 · 数据库设计

## 1. 设计约定

- 数据库：PostgreSQL 16，扩展 pgvector（向量检索）；
- 主键：统一 `BIGINT` 自增，命名 `id`；
- 时间字段：`created_at`、`updated_at`（TIMESTAMP），由应用写入；`deleted` 为逻辑删除标记（0 正常，1 删除）；
- 命名：表名与字段名小写、下划线分隔，语义清晰；
- 状态字段用 VARCHAR 语义值（如 `ACTIVE`）统一大写；
- 掌握度用 SMALLINT（0–100）；
- 建表与初始化脚本纳入版本管理（`db/schema.sql`、`db/data.sql`），可重复执行（IF NOT EXISTS / 幂等）；
- 向量字段用 `vector(n)`（pgvector），配合 HNSW 索引。

## 2. 实体关系概览

- 知识底座：`syllabus`（科目）→ `knowledge_point`（知识点树）→ `question`（题目）与 `knowledge_resource`（资料）；
- 学习主体：`sys_user` → `plan`（计划）→ `plan_task`（任务，关联知识点）；
- 学习证据：`answer_record`（答题）、`mistake_book`（错题）、`review_item`（复习项）；
- Agent 交互：`conversation`（会话）、`agent_log`（审计）、`notification`（通知）。

## 3. 表清单

| 表名 | 用途 |
| --- | --- |
| sys_user | 用户（学习者/内容管理员/系统管理员） |
| syllabus | 考纲科目（数据结构/计组/操作系统/计网） |
| knowledge_point | 知识点（树状结构，含掌握度） |
| question | 题目（单选/多选/判断/代码） |
| knowledge_resource | 知识点资料（讲义/笔记/卡片，含向量） |
| plan | 学习计划 |
| plan_task | 计划任务（学习/测验/复习） |
| answer_record | 答题记录 |
| mistake_book | 错题本 |
| review_item | 复习调度项 |
| conversation | Agent 会话 |
| agent_log | Agent 工具调用审计 |
| notification | 站内通知 |

## 4. 表结构明细

### 4.1 sys_user（用户）

| 字段 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| id | BIGINT | PK, AUTO_INCREMENT | 用户 ID |
| username | VARCHAR(50) | UNIQUE, NOT NULL | 登录账号 |
| password | VARCHAR(100) | NOT NULL | BCrypt 密文 |
| nickname | VARCHAR(50) | 可空 | 昵称 |
| avatar | VARCHAR(255) | 可空 | 头像地址 |
| role | VARCHAR(20) | NOT NULL | LEARNER / CONTENT_ADMIN / ADMIN |
| status | TINYINT | DEFAULT 1 | 1 正常，0 停用 |
| deleted | TINYINT | DEFAULT 0 | 逻辑删除 |
| created_at | TIMESTAMP | NOT NULL | 创建时间 |
| updated_at | TIMESTAMP | NOT NULL | 更新时间 |

索引：`idx_user_role(role)`。

### 4.2 syllabus（考纲科目）

| 字段 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| id | BIGINT | PK, AUTO_INCREMENT | 科目 ID |
| code | VARCHAR(20) | UNIQUE, NOT NULL | 科目编码（DS / CO / OS / NET） |
| name | VARCHAR(50) | NOT NULL | 科目名（数据结构/计组/操作系统/计网） |
| sort | INT | DEFAULT 0 | 排序 |
| description | VARCHAR(500) | 可空 | 简介 |

### 4.3 knowledge_point（知识点，树状）

| 字段 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| id | BIGINT | PK, AUTO_INCREMENT | 知识点 ID |
| syllabus_id | BIGINT | NOT NULL | 科目，关联 syllabus |
| parent_id | BIGINT | 可空 | 父节点（章节/小节），根为空 |
| title | VARCHAR(100) | NOT NULL | 知识点标题 |
| content | TEXT | 可空 | 知识点内容（知识卡片） |
| difficulty | TINYINT | DEFAULT 3 | 难度 1–5 |
| mastery | SMALLINT | DEFAULT 0 | 掌握度 0–100 |
| sort | INT | DEFAULT 0 | 同层排序 |
| deleted | TINYINT | DEFAULT 0 | 逻辑删除 |
| created_at | TIMESTAMP | NOT NULL | 创建时间 |
| updated_at | TIMESTAMP | NOT NULL | 更新时间 |

索引：`idx_kp_syllabus(syllabus_id)`、`idx_kp_parent(parent_id)`。

### 4.4 question（题目）

| 字段 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| id | BIGINT | PK, AUTO_INCREMENT | 题目 ID |
| kp_id | BIGINT | NOT NULL | 知识点，关联 knowledge_point |
| q_type | VARCHAR(20) | NOT NULL | SINGLE / MULTI / JUDGE / CODE |
| stem | TEXT | NOT NULL | 题干 |
| options | JSONB | 可空 | 选项（SINGLE/MULTI 用） |
| answer | TEXT | NOT NULL | 答案（客观题）/ 参考要点（代码题） |
| analysis | TEXT | 可空 | 解析 |
| difficulty | TINYINT | DEFAULT 3 | 难度 1–5 |
| source | VARCHAR(50) | 可空 | 来源（自建/用户导入） |
| deleted | TINYINT | DEFAULT 0 | 逻辑删除 |
| created_at | TIMESTAMP | NOT NULL | 创建时间 |
| updated_at | TIMESTAMP | NOT NULL | 更新时间 |

索引：`idx_question_kp(kp_id)`、`idx_question_type(q_type)`。

### 4.5 knowledge_resource（知识点资料）

| 字段 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| id | BIGINT | PK, AUTO_INCREMENT | 资料 ID |
| kp_id | BIGINT | NOT NULL | 关联知识点 |
| title | VARCHAR(100) | NOT NULL | 资料标题 |
| content | TEXT | NOT NULL | 资料内容（卡片/讲义） |
| embedding | vector(1024) | 可空 | 内容向量（RAG 检索） |
| source | VARCHAR(50) | 可空 | 来源 |
| deleted | TINYINT | DEFAULT 0 | 逻辑删除 |
| created_at | TIMESTAMP | NOT NULL | 创建时间 |

索引：`idx_res_kp(kp_id)`、HNSW 向量索引 `idx_res_embedding(embedding vector_cosine_ops)`。

### 4.6 plan（学习计划）

| 字段 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| id | BIGINT | PK, AUTO_INCREMENT | 计划 ID |
| user_id | BIGINT | NOT NULL | 学习者 |
| title | VARCHAR(100) | NOT NULL | 计划标题 |
| goal | TEXT | 可空 | 目标描述（原始文本） |
| syllabus_id | BIGINT | 可空 | 目标科目（空为全科） |
| start_date | DATE | NOT NULL | 开始日期 |
| end_date | DATE | NOT NULL | 截止日期 |
| status | VARCHAR(20) | NOT NULL | ACTIVE / PAUSED / COMPLETED |
| deleted | TINYINT | DEFAULT 0 | 逻辑删除 |
| created_at | TIMESTAMP | NOT NULL | 创建时间 |
| updated_at | TIMESTAMP | NOT NULL | 更新时间 |

索引：`idx_plan_user(user_id, status)`。

### 4.7 plan_task（计划任务）

| 字段 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| id | BIGINT | PK, AUTO_INCREMENT | 任务 ID |
| plan_id | BIGINT | NOT NULL | 关联计划 |
| user_id | BIGINT | NOT NULL | 学习者 |
| kp_id | BIGINT | 可空 | 关联知识点 |
| task_type | VARCHAR(20) | NOT NULL | STUDY / QUIZ / REVIEW |
| task_date | DATE | NOT NULL | 任务日期 |
| status | VARCHAR(20) | NOT NULL | PENDING / IN_PROGRESS / DONE / SKIPPED |
| done_at | TIMESTAMP | 可空 | 完成时间 |
| created_at | TIMESTAMP | NOT NULL | 创建时间 |
| updated_at | TIMESTAMP | NOT NULL | 更新时间 |

索引：`idx_task_user_date(user_id, task_date, status)`、`idx_task_plan(plan_id)`。

### 4.8 answer_record（答题记录）

| 字段 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| id | BIGINT | PK, AUTO_INCREMENT | 答题 ID |
| user_id | BIGINT | NOT NULL | 学习者 |
| question_id | BIGINT | NOT NULL | 题目 |
| task_id | BIGINT | 可空 | 关联任务（若由任务出题） |
| user_answer | TEXT | NOT NULL | 用户答案 |
| correct | BOOLEAN | NOT NULL | 是否答对 |
| duration | INT | 可空 | 耗时（秒） |
| answered_at | TIMESTAMP | NOT NULL | 答题时间 |

索引：`idx_ans_user(user_id, answered_at)`、`idx_ans_question(question_id)`。

### 4.9 mistake_book（错题本）

| 字段 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| id | BIGINT | PK, AUTO_INCREMENT | 错题 ID |
| user_id | BIGINT | NOT NULL | 学习者 |
| question_id | BIGINT | NOT NULL | 题目 |
| first_wrong_at | TIMESTAMP | NOT NULL | 首次答错时间 |
| wrong_count | INT | DEFAULT 1 | 答错次数 |
| status | VARCHAR(20) | NOT NULL | REVIEWING（待复习）/ MASTERED（已掌握） |
| last_review_at | TIMESTAMP | 可空 | 最近复习时间 |
| created_at | TIMESTAMP | NOT NULL | 创建时间 |

约束与索引：
- `UNIQUE uk_mistake(user_id, question_id)`：同一用户同一题仅一条；
- `idx_mistake_user(user_id, status)`。

### 4.10 review_item（复习调度项）

| 字段 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| id | BIGINT | PK, AUTO_INCREMENT | 复习项 ID |
| user_id | BIGINT | NOT NULL | 学习者 |
| kp_id | BIGINT | NOT NULL | 知识点 |
| due_date | DATE | NOT NULL | 到期日 |
| interval_days | INT | NOT NULL | 当前间隔（天） |
| status | VARCHAR(20) | NOT NULL | PENDING / DONE |
| done_at | TIMESTAMP | 可空 | 完成时间 |
| created_at | TIMESTAMP | NOT NULL | 创建时间 |

索引：`idx_review_due(user_id, due_date, status)`。

### 4.11 conversation（Agent 会话）

| 字段 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| id | BIGINT | PK, AUTO_INCREMENT | 消息 ID |
| user_id | BIGINT | NOT NULL | 用户 |
| session_id | VARCHAR(64) | NOT NULL | 会话 ID（应用端会话标识） |
| role | VARCHAR(20) | NOT NULL | USER / AGENT |
| content | TEXT | NOT NULL | 消息内容 |
| tool_calls | JSONB | 可空 | 工具调用轨迹（AGENT 消息可含） |
| created_at | TIMESTAMP | NOT NULL | 创建时间 |

索引：`idx_conv_session(user_id, session_id)`。

### 4.12 agent_log（Agent 工具调用审计）

| 字段 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| id | BIGINT | PK, AUTO_INCREMENT | 日志 ID |
| user_id | BIGINT | 可空 | 用户 |
| session_id | VARCHAR(64) | 可空 | 会话 |
| intent | VARCHAR(50) | 可空 | 识别意图 |
| tool_name | VARCHAR(50) | NOT NULL | 工具名 |
| params_summary | VARCHAR(500) | 可空 | 参数摘要 |
| result_status | VARCHAR(20) | NOT NULL | SUCCESS / FAILED / SKIPPED |
| latency_ms | INT | 可空 | 耗时（毫秒） |
| tokens | INT | 可空 | Token 消耗 |
| created_at | TIMESTAMP | NOT NULL | 时间 |

索引：`idx_log_user(user_id)`、`idx_log_time(created_at)`。

### 4.13 notification（站内通知）

| 字段 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| id | BIGINT | PK, AUTO_INCREMENT | 通知 ID |
| user_id | BIGINT | NOT NULL | 接收人 |
| type | VARCHAR(30) | NOT NULL | REVIEW / PLAN / SYSTEM |
| title | VARCHAR(100) | NOT NULL | 标题 |
| content | VARCHAR(500) | 可空 | 内容 |
| read_status | TINYINT | DEFAULT 0 | 0 未读，1 已读 |
| created_at | TIMESTAMP | NOT NULL | 创建时间 |

索引：`idx_notify_user(user_id, read_status)`。

## 5. 一致性设计汇总

| 场景 | 保障 |
| --- | --- |
| 重复错题 | mistake_book 唯一约束 (user_id, question_id) |
| 掌握度一致 | 掌握度更新与答题/复习同事务 |
| 复习项唯一 | 同知识点同周期仅一条 PENDING（应用层控制） |
| 任务不重复生成 | 按 (user_id, task_date, kp_id, task_type) 幂等生成 |
| 逻辑删除 | deleted 字段 + 查询过滤 |
| 向量检索 | 资料向量入库时生成，检索用 cosine 距离 |

## 6. 初始化数据（db/data.sql）

- 管理员账号：`admin`（初始密码 `admin123`，首登提示修改），角色 ADMIN；
- 考纲科目：数据结构（DS）、计算机组成原理（CO）、操作系统（OS）、计算机网络（NET）；
- 每科样例章节/知识点若干（骨架，覆盖顶层结构）；
- 样例题目每科 10–20 题（原创题，各类型）；
- 样例资料每科 3–5 条（含向量化占位，入库时生成向量）；
- 演示学习者账号：`learner001`（初始密码 `123456`）。

【待确认】正式演示账号与初始知识库规模在内容管理员录入前确认。

## 7. 脚本管理

- `db/schema.sql`：全部建表语句（含索引、唯一约束、向量扩展与索引），可重复执行；
- `db/data.sql`：初始化数据（幂等：先查后插或 INSERT ... ON CONFLICT DO NOTHING）；
- `db/migration/Vxxx__desc.sql`：增量脚本，不直接改历史脚本；每次变更同步更新本文档对应表；
- 向量化脚本：`scripts/embed_knowledge.py`（资料入库/更新时生成 embedding）。
