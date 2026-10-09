# 04 · 接口规范与 API 设计

## 1. 通用约定

- 基础路径：`/api`；RESTful 风格；请求与响应均为 JSON（`application/json`；charset=utf-8）；
- 认证：请求头 `Authorization: Bearer <token>`；未携带或失效返回 401；
- 时间格式：`yyyy-MM-dd HH:mm:ss`；日期格式：`yyyy-MM-dd`；
- 分页：查询列表统一使用 `page`（从 1 起）与 `size`（默认 20，最大 100）；
- 幂等与安全：写操作校验角色与状态；Agent 对话接口限流（防刷）；密钥不落库不入包；
- 接口文档维护在 Apifox，字段变更需前后端同步更新。

## 2. 统一响应结构

成功：

```json
{ "code": 0, "message": "ok", "data": { } }
```

失败：

```json
{ "code": 40001, "message": "参数不合法", "data": null }
```

分页数据：

```json
{ "code": 0, "message": "ok", "data": { "list": [], "total": 0, "page": 1, "size": 20 } }
```

Agent 对话流式返回（SSE）：`data: {code, message, data}` 逐行推送，事件类型见 §5.3。

## 3. 错误码约定

| 区间 | 含义 |
| --- | --- |
| 0 | 成功 |
| 40001–40099 | 参数校验错误（业务参数） |
| 40100 | 未登录或 Token 失效 |
| 40300 | 无权限（角色越权） |
| 40400 | 资源不存在 |
| 40901 | 重复操作（重复创建计划/任务已存在） |
| 40902 | 状态不允许（计划/任务状态不满足） |
| 40903 | 知识库无可用内容（题库为空/无复习项） |
| 50000 | 系统异常（兜底） |
| 50001 | 智能体编排层不可用（恒脑/模型调用失败） |

前端按 code 统一提示 message；401 额外触发清除登录态并跳转登录页。

## 4. API 清单

角色标记：公开 / 学习者(LEARNER) / 内容管理员(CONTENT_ADMIN) / 系统管理员(ADMIN)。

### 4.1 认证 auth

| 方法 | 路径 | 角色 | 说明 |
| --- | --- | --- | --- |
| POST | /api/auth/register | 公开 | 注册（username、password、nickname） |
| POST | /api/auth/login | 公开 | 登录，返回 token 与用户信息（含角色） |
| POST | /api/auth/logout | 全部 | 退出（前端清除 Token） |
| POST | /api/auth/password | 全部 | 修改本人密码（原密码、新密码） |
| GET | /api/auth/me | 全部 | 当前登录用户信息 |

### 4.2 学习计划 plans

| 方法 | 路径 | 角色 | 说明 |
| --- | --- | --- | --- |
| POST | /api/plans/generate | LEARNER | 目标解析并生成计划（自然语言目标 → 计划 + 任务序列） |
| GET | /api/plans | LEARNER | 我的计划列表（分页） |
| GET | /api/plans/{id} | LEARNER | 计划详情（含进度、阶段结构） |
| POST | /api/plans/{id}/pause | LEARNER | 暂停计划 |
| POST | /api/plans/{id}/resume | LEARNER | 恢复计划 |
| POST | /api/plans/{id}/complete | LEARNER | 手动完成计划 |

### 4.3 任务 tasks

| 方法 | 路径 | 角色 | 说明 |
| --- | --- | --- | --- |
| GET | /api/tasks/today | LEARNER | 今日任务（含到期复习项并入） |
| GET | /api/tasks | LEARNER | 按日期范围查询任务（dateBegin、dateEnd） |
| PATCH | /api/tasks/{id} | LEARNER | 更新任务状态（action: done / skip） |

### 4.4 知识库 knowledge

| 方法 | 路径 | 角色 | 说明 |
| --- | --- | --- | --- |
| GET | /api/knowledge/syllabus | LEARNER | 考纲图谱（科目 → 章节 → 知识点树） |
| GET | /api/knowledge/{id} | LEARNER | 知识点详情（内容、关联题目数、资料、掌握度） |
| GET | /api/knowledge/search | LEARNER | 向量检索（query、syllabusId?、limit） |
| POST | /api/knowledge/resources | CONTENT_ADMIN | 新增资料（内容入库并向量化） |
| PUT | /api/knowledge/resources/{id} | CONTENT_ADMIN | 修改资料（重新向量化） |
| POST | /api/knowledge/questions | CONTENT_ADMIN | 新增题目 |
| PUT | /api/knowledge/questions/{id} | CONTENT_ADMIN | 修改题目 |

### 4.5 出题与答题 quizzes

| 方法 | 路径 | 角色 | 说明 |
| --- | --- | --- | --- |
| POST | /api/quizzes/generate | LEARNER | 出题（kpId、count、difficulty?；响应题目不含答案） |
| POST | /api/quizzes/submit | LEARNER | 提交答案判分（[{questionId, answer}]，返回逐题对错与解析） |
| GET | /api/quizzes/records | LEARNER | 我的答题记录（分页） |

### 4.6 错题 mistakes

| 方法 | 路径 | 角色 | 说明 |
| --- | --- | --- | --- |
| GET | /api/mistakes | LEARNER | 错题列表（status?、kpId?、分页） |
| POST | /api/mistakes/{id}/review | LEARNER | 错题重做反馈（correct: true/false → 更新掌握度/状态） |
| DELETE | /api/mistakes/{id} | LEARNER | 手动移出错题本 |

### 4.7 复习 reviews

| 方法 | 路径 | 角色 | 说明 |
| --- | --- | --- | --- |
| GET | /api/reviews/due | LEARNER | 到期复习项（date?，默认今天） |
| POST | /api/reviews/{id}/done | LEARNER | 复习完成反馈（更新掌握度与下一间隔） |

### 4.8 进度统计 stats

| 方法 | 路径 | 角色 | 说明 |
| --- | --- | --- | --- |
| GET | /api/stats/overview | LEARNER | 学习概览（今日完成、连续天数、总学习时长、掌握度均值） |
| GET | /api/stats/knowledge | LEARNER | 各科目/章节掌握度分布 |
| GET | /api/stats/weak | LEARNER | 薄弱知识点列表（按掌握度升序，limit） |

### 4.9 Agent 对话 agent

| 方法 | 路径 | 角色 | 说明 |
| --- | --- | --- | --- |
| POST | /api/agent/chat | LEARNER | Agent 对话（非流式：返回完整回复与工具轨迹） |
| POST | /api/agent/chat/stream | LEARNER | Agent 对话（SSE 流式：意图 → 工具调用 → 最终回复） |
| GET | /api/agent/sessions | LEARNER | 我的会话列表 |
| GET | /api/agent/sessions/{sessionId} | LEARNER | 会话历史（消息 + 工具调用轨迹） |

### 4.10 通知 notifications

| 方法 | 路径 | 角色 | 说明 |
| --- | --- | --- | --- |
| GET | /api/notifications | 全部登录角色 | 我的通知（分页，含未读标记） |
| POST | /api/notifications/{id}/read | 全部登录角色 | 标记已读（仅本人） |

### 4.11 管理端 admin

| 方法 | 路径 | 角色 | 说明 |
| --- | --- | --- | --- |
| GET | /api/admin/users | ADMIN | 用户列表（分页） |
| PUT | /api/admin/users/{id} | ADMIN | 启用/停用用户 |
| GET | /api/admin/logs | ADMIN | Agent 审计日志查询（分页、按用户/工具/时间筛选） |
| GET | /api/admin/stats/usage | ADMIN | 系统使用概览（会话数、工具调用数、Token 消耗） |

## 5. 关键接口字段说明

### 5.1 计划生成（POST /api/plans/generate）

请求：

```json
{ "goal": "12 月底前完成 408 一轮复习", "syllabusIds": [1,2,3,4], "endDate": "2026-12-31" }
```

响应 data：

```json
{
  "planId": 1,
  "title": "408 一轮复习计划",
  "stageCount": 4,
  "taskCount": 120,
  "stages": [ { "syllabusId": 1, "syllabusName": "数据结构", "kpCount": 30, "taskCount": 30 } ],
  "preview": true
}
```

生成流程：目标解析（可预览确认）→ 按考纲拆解 → 生成任务序列；`preview=true` 表示需用户确认后落库（确认接口：复用 POST /api/plans/generate 传 `confirm=true`，或拆分 create 接口，实现期二选一）。

### 5.2 出题（POST /api/quizzes/generate）

请求：`{ "kpId": 12, "count": 3, "difficulty": 3 }`

响应 data.list 每项（不含答案与解析）：

```json
{
  "questionId": 101,
  "type": "SINGLE",
  "stem": "下列关于虚拟存储的说法，正确的是（ ）",
  "options": ["A. ...", "B. ...", "C. ...", "D. ..."],
  "difficulty": 3
}
```

### 5.3 Agent 对话流式（POST /api/agent/chat/stream）

SSE 事件序列：

```
data: {"event":"intent","intent":"task_execute","message":"识别到学习任务：复习计组第三章"}
data: {"event":"tool","tool":"knowledge.query","status":"SUCCESS","message":"已获取知识点内容"}
data: {"event":"tool","tool":"quiz.generate","status":"SUCCESS","message":"已生成 3 道题"}
data: {"event":"answer","questionId":101,"stem":"..."}
data: {"event":"done","message":"今日计组第三章任务完成，明日 1 项复习已排期"}
```

### 5.4 判分（POST /api/quizzes/submit）

请求：`{ "records": [ { "questionId": 101, "answer": "B" } ] }`

响应 data.list 每项：

```json
{ "questionId": 101, "correct": true, "userAnswer": "B", "correctAnswer": "B", "analysis": "解析……", "mistakeId": null }
```

`correct=false` 时 `mistakeId` 返回错题本 ID（自动归因），`masteryDelta` 一并返回。

### 5.5 今日任务（GET /api/tasks/today）

响应 data：

```json
{
  "date": "2026-10-05",
  "studyCount": 2,
  "quizCount": 1,
  "reviewCount": 3,
  "tasks": [
    { "id": 12, "type": "STUDY", "kpId": 8, "kpTitle": "页置换算法", "status": "PENDING" },
    { "id": 13, "type": "REVIEW", "kpId": 3, "kpTitle": "栈与队列", "status": "PENDING", "intervalDays": 4 }
  ]
}
```

## 6. 开发期建议

- 能力服务先行按本文档实现并维护 Apifox；应用端按 Apifox 联调；
- 字段增删改必须同步更新本文档与 Apifox；涉及数据库变更先走全组评审；
- Agent 对话接口先实现非流式（简单可测），流式在核心闭环跑通后补齐；
- 恒脑编排适配层接口（内部）单独成文档，与对外 API 分离。
