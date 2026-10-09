# SDD-03 · 接口规格

- 规格状态：基线 v1.0
- 需求依据：SRS-02 F1–F7；数据依据：SDD-02

## 1. 通用约定

- 基础路径：`/api`；RESTful；请求/响应 JSON（charset=utf-8）；
- 认证：`Authorization: Bearer <token>`；缺失/失效返回 401；
- 时间格式：`yyyy-MM-dd HH:mm:ss`；日期格式：`yyyy-MM-dd`；
- 分页：`page`（从 1 起）+ `size`（默认 20，最大 100）；
- 写操作校验角色与状态；敏感字段（凭证、样例、证据）接口层面脱敏；
- 接口文档维护于 Apifox，字段变更需同步规格与前后端。

## 2. 统一响应结构

成功：

```json
{ "code": 0, "message": "ok", "data": { } }
```

失败：

```json
{ "code": 40001, "message": "参数不合法", "data": null }
```

分页：

```json
{ "code": 0, "message": "ok", "data": { "list": [], "total": 0, "page": 1, "size": 20 } }
```

## 3. 错误码约定

| 区间 | 含义 |
| --- | --- |
| 0 | 成功 |
| 40001–40099 | 参数校验错误（业务参数） |
| 40100 | 未登录或 Token 失效 |
| 40300 | 无权限（角色越权） |
| 40400 | 资源不存在 |
| 40901 | 重复操作（域名已存在 / 任务已存在） |
| 40902 | 状态不允许（任务/计划状态不满足） |
| 40903 | 无可执行内容（无页面/无规则/无数据） |
| 40310 | 未授权资产（URL 超出授权范围，安全边界） |
| 50000 | 系统异常（兜底） |
| 50001 | 智能体编排层不可用（恒脑调用失败） |
| 50002 | 工具执行失败（超时/异常） |

## 4. API 清单

角色标记：公开 / 运维(SEC_OPS) / 规则管理员(RULE_ADMIN) / 系统管理员(ADMIN)。

### 4.1 认证 auth

| 方法 | 路径 | 角色 | 说明 |
| --- | --- | --- | --- |
| POST | /api/auth/login | 公开 | 登录（username、password → token、用户信息） |
| POST | /api/auth/logout | 全部 | 退出 |
| POST | /api/auth/password | 全部 | 修改本人密码 |
| GET | /api/auth/me | 全部 | 当前用户信息 |

### 4.2 授权资产 assets

| 方法 | 路径 | 角色 | 说明 |
| --- | --- | --- | --- |
| POST | /api/assets | SEC_OPS, RULE_ADMIN | 录入授权域名（domain、scopeType、scopeValue、authNote、owner） |
| GET | /api/assets | SEC_OPS, RULE_ADMIN | 资产列表（分页，status 筛选） |
| GET | /api/assets/{id} | SEC_OPS, RULE_ADMIN | 资产详情（含页面统计） |
| PATCH | /api/assets/{id} | SEC_OPS, RULE_ADMIN | 修改资产（范围/备注/启停） |
| DELETE | /api/assets/{id} | SEC_OPS, RULE_ADMIN | 停用资产（逻辑删除，需确认） |

### 4.3 巡检计划 schedules

| 方法 | 路径 | 角色 | 说明 |
| --- | --- | --- | --- |
| POST | /api/schedules | SEC_OPS, RULE_ADMIN | 创建巡检计划（scopeId、name、intervalType/cron、executeWindow） |
| GET | /api/schedules | SEC_OPS, RULE_ADMIN | 计划列表 |
| GET | /api/schedules/{id} | SEC_OPS, RULE_ADMIN | 计划详情（含下次执行时间） |
| PATCH | /api/schedules/{id} | SEC_OPS, RULE_ADMIN | 修改计划（周期/启停） |
| DELETE | /api/schedules/{id} | SEC_OPS, RULE_ADMIN | 删除计划 |

### 4.4 检测任务 tasks

| 方法 | 路径 | 角色 | 说明 |
| --- | --- | --- | --- |
| POST | /api/tasks | SEC_OPS, RULE_ADMIN | 手动触发巡检（scopeId、检测项集合[可选]） |
| GET | /api/tasks | SEC_OPS, RULE_ADMIN | 任务列表（分页，状态/资产筛选） |
| GET | /api/tasks/{id} | SEC_OPS, RULE_ADMIN | 任务详情（状态、阶段、进度） |
| POST | /api/tasks/{id}/retry | SEC_OPS, RULE_ADMIN | 失败任务重试 |

### 4.5 风险结果 findings

| 方法 | 路径 | 角色 | 说明 |
| --- | --- | --- | --- |
| GET | /api/findings | SEC_OPS, RULE_ADMIN | 风险列表（分页，type/severity/status/scope 筛选） |
| GET | /api/findings/{id} | SEC_OPS, RULE_ADMIN | 风险详情（证据、影响、建议、轨迹） |
| PATCH | /api/findings/{id}/status | SEC_OPS | 状态流转（CONFIRMED / FALSE_POSITIVE） |
| POST | /api/findings/{id}/retest | SEC_OPS | 对单条风险发起复测 |
| GET | /api/findings/stats | SEC_OPS, RULE_ADMIN | 风险统计（按类型/级别/站点分布，趋势） |

### 4.6 告警 alerts

| 方法 | 路径 | 角色 | 说明 |
| --- | --- | --- | --- |
| GET | /api/alerts | SEC_OPS, RULE_ADMIN | 告警列表（分页，status/severity 筛选） |
| POST | /api/alerts/{id}/read | 全部 | 标记已读 |
| PATCH | /api/alerts/{id}/status | SEC_OPS | 处理/关闭告警 |

### 4.7 报告 reports

| 方法 | 路径 | 角色 | 说明 |
| --- | --- | --- | --- |
| POST | /api/reports/generate | SEC_OPS, RULE_ADMIN | 生成报告（scopeId、periodBegin、periodEnd） |
| GET | /api/reports | SEC_OPS, RULE_ADMIN | 报告列表 |
| GET | /api/reports/{id} | SEC_OPS, RULE_ADMIN | 报告详情/下载链接 |
| GET | /api/reports/{id}/file | SEC_OPS, RULE_ADMIN | 下载报告文件（HTML/PDF） |

### 4.8 敏感信息规则 sensitive

| 方法 | 路径 | 角色 | 说明 |
| --- | --- | --- | --- |
| GET | /api/sensitive/rules | SEC_OPS, RULE_ADMIN | 规则列表（type/enabled 筛选） |
| POST | /api/sensitive/rules | RULE_ADMIN | 新增规则（name、ruleType、pattern、scopeBind、severity） |
| PUT | /api/sensitive/rules/{id} | RULE_ADMIN | 修改规则 |
| PATCH | /api/sensitive/rules/{id}/enabled | RULE_ADMIN | 启停规则 |

### 4.9 智能体 agent

| 方法 | 路径 | 角色 | 说明 |
| --- | --- | --- | --- |
| POST | /api/agent/chat | SEC_OPS | 智能体对话（非流式：完整回复 + 轨迹） |
| POST | /api/agent/chat/stream | SEC_OPS | 智能体对话（SSE 流式） |
| GET | /api/agent/tasks | SEC_OPS | 智能体发起的任务（分页） |
| GET | /api/agent/logs | ADMIN | 智能体工具调用审计（分页，筛选） |

### 4.10 管理端 admin

| 方法 | 路径 | 角色 | 说明 |
| --- | --- | --- | --- |
| GET | /api/admin/users | ADMIN | 用户列表 |
| PUT | /api/admin/users/{id}/status | ADMIN | 启用/停用用户 |
| GET | /api/admin/logs | ADMIN | 操作审计查询 |
| GET | /api/admin/stats/usage | ADMIN | 系统使用概览 |
| GET | /api/admin/tools | ADMIN | 工具注册清单（能力/状态） |
| PATCH | /api/admin/tools/{id}/enabled | ADMIN | 工具启停 |

## 5. 关键接口字段说明

### 5.1 手动触发巡检（POST /api/tasks）

请求：

```json
{ "scopeId": 3, "checkTypes": ["VULN", "WEAK_CONFIG", "TAMPER", "SENSITIVE"] }
```

响应 data：

```json
{ "taskId": 101, "status": "QUEUED", "scopeId": 3, "createdAt": "2026-10-09 10:00:00" }
```

### 5.2 风险详情（GET /api/findings/{id}）

响应 data 要点：

```json
{
  "id": 501,
  "type": "SENSITIVE",
  "severity": "HIGH",
  "title": "页面暴露疑似密钥信息",
  "url": "https://demo.example.com/config.js",
  "evidence": "命中规则：密钥特征；命中片段已脱敏（长度 12）",
  "impact": "可能导致内部配置信息泄露，被用于进一步攻击",
  "suggestion": "移除公开文件中的密钥；将密钥移入服务端环境变量并轮换",
  "toolName": "sensitive-scanner",
  "status": "CONFIRMED",
  "firstSeen": "2026-10-08 03:12:00",
  "lastSeen": "2026-10-09 03:12:00",
  "trail": [{ "taskId": 98, "runAt": "2026-10-08 03:12:00", "result": "OPEN" }]
}
```

### 5.3 智能体对话流式（POST /api/agent/chat/stream）

SSE 事件序列：

```
data: {"event":"plan","message":"规划完成：发现 → 漏洞检测 → 敏感信息检测"}
data: {"event":"tool","tool":"nuclei","status":"RUNNING","message":"开始漏洞检测"}
data: {"event":"tool","tool":"sensitive-scanner","status":"SUCCESS","message":"检测完成，命中 2 条"}
data: {"event":"finding","findingId":502,"severity":"MEDIUM","title":"..."}
data: {"event":"done","message":"巡检完成：共发现 3 个风险（高危 1、中危 2），报告已生成"}
```

### 5.4 报告生成（POST /api/reports/generate）

请求：

```json
{ "scopeId": 3, "periodBegin": "2026-10-01", "periodEnd": "2026-10-09" }
```

响应 data：

```json
{ "reportId": 20, "status": "GENERATING", "filePath": null }
```

生成完成后 `filePath` 就绪，经 `GET /api/reports/{id}/file` 下载。

## 6. 开发期建议

- 能力服务先行按本文档实现并维护 Apifox；管理端按 Apifox 联调；
- 智能体对话先实现非流式，核心闭环跑通后补流式；
- 恒脑适配层内部接口单独成文档，与对外 API 分离；
- 字段变更必须同步本文档、Apifox 与前后端；数据库变更先评审再迁移。

## 7. 变更记录

| 版本 | 日期 | 变更人 | 变更内容 | 影响范围 |
| --- | --- | --- | --- | --- |
| v1.0 | 2026-10-09 | 课题立项 | 基线发布（资产/计划/任务/风险/告警/报告/规则/智能体接口） | 全量 |
