# C 组真机测试记录

- 状态：v1.0（2026-10-14）
- 范围：C 组承接的 T12–T19 中，**需要真实工具或真实数据库**的验证项
- 目的：让评审人能够**照着复现**每一项结论，而不是只看结论。所有原始输出已随仓库提交。
- 关联：[工具清单与合规声明](../docs/02-质量与验收/03-工具清单与合规声明.md)、
  [SDD-04 工具注册表契约](../specs/02-设计规格/SDD-04-工具注册表契约.md)、
  [对 SDD-04 契约的核对反馈](../docs/01-项目治理/04-C组对SDD-04工具注册表契约的核对反馈.md)

> 本文件是 **C 组专项验证记录**，与 F 组 T48 的《测试报告》分工不同：本文件只记录
> C 组工具/检测器的真机验证过程与证据；指标口径与召回/误报统计仍归 T48。

## 1. 环境

| 项 | 值 |
| --- | --- |
| 宿主 | Windows 11 (10.0.22621)，Git Bash / PowerShell |
| 容器运行时 | Docker Desktop，Engine **29.8.2** |
| Python | 3.13.0；httpx 0.28.1、SQLAlchemy、psycopg2-binary、python-Wappalyzer 0.3.1、aiohttp |
| 授权靶场 | `vulnerables/web-dvwa`，`http://127.0.0.1:8080`（已登记于工具清单 §4.1） |
| 被测工具 | Nuclei v3.11.1（容器）、ZAP 2.17.0（容器守护进程）、python-Wappalyzer 0.3.1（进程内） |
| 数据库 | PostgreSQL 16.15（容器，用于 T12/T13 落库验证） |

### 复现步骤

```bash
# 1) 授权靶场
docker run -d --name dvwa --restart unless-stopped -p 8080:80 vulnerables/web-dvwa

# 2) Nuclei 模板与缓存（务必用命名卷：Windows 绑定挂载下遍历 14036 个模板极慢）
docker volume create nuclei-tpl && docker volume create nuclei-cache
docker run --rm -v nuclei-tpl:/dst -v <本机模板目录>:/src:ro \
  projectdiscovery/nuclei:latest -c "cp -r /src/. /dst/"

# 3) ZAP 守护进程
docker run -d --name zap -p 8090:8090 ghcr.io/zaproxy/zaproxy:stable \
  zap.sh -daemon -host 0.0.0.0 -port 8090 -config api.key=anheng-dev-key \
  -config api.addrs.addr.name=.* -config api.addrs.addr.regex=true

# 4) PostgreSQL（T12/T13 落库验证）
docker run -d --name anheng-pg -e POSTGRES_PASSWORD=anheng-dev \
  -e POSTGRES_DB=anheng -p 5432:5432 postgres:16
docker exec -i anheng-pg psql -U postgres -d anheng < db/schema.sql

# 5) 一键跑 T15/T16/T17 并产出原始证据
python scripts/verify_c_tools_real.py
```

## 2. 评估配置（随结果记录）

`tests/dataset/README.md` 要求检测效果评估须记录**扫描配置**。本次记录如下：

| 项 | 值 | 说明 |
| --- | --- | --- |
| 目标 | `http://127.0.0.1:8080/` | 授权靶场；容器内改写为 `host.docker.internal:8080` |
| Nuclei 模板范围 | `http/misconfiguration/`、`http/exposures/`、`http/technologies/` | 命中 2809 个模板 |
| Nuclei **目标侧**速率 | 1800 次/分钟（30 次/秒） | 见 §5「未覆盖项」：合规速率跑不完此范围 |
| Nuclei 排除标签 | `fuzz,intrusive,dos` | 非破坏要求 |
| Nuclei 带外交互 | `-no-interactsh` 禁用 | 非破坏要求 |
| ZAP 模式 | **仅被动**（`accessUrl` + `alerts`），不启用主动扫描 | 工具清单 §2.1 |

## 3. 结果

### T15 · Nuclei 适配器（容器模式）

```
status = SUCCESS    耗时 = 179.0 秒    命中 = 19 条
category : {WEAK_CONFIG: 16, VULN: 3}
severity : {INFO: 18, MEDIUM: 1}
审计留痕 : [{tool_id: nuclei, target: http://127.0.0.1:8080/, status: SUCCESS}]
```

代表性命中（完整 19 条见原始输出）：

| 级别 | category | 标题 |
| --- | --- | --- |
| MEDIUM | WEAK_CONFIG | Sensitive Configuration Files Listing（`/config/` 目录列举） |
| INFO | WEAK_CONFIG | HTTP Missing Security Headers（10 条） |
| INFO | WEAK_CONFIG | Cookie No HttpOnly / without SameSite Attribute |
| INFO | WEAK_CONFIG | README.md file disclosure、Gitignore Config |
| INFO | VULN | WAF Detection、FingerprintHub、Wappalyzer Technology Detection ⚠️ |

⚠️ 最后三条是**已知的归类缺陷**：它们是信息类模板（防护设施识别、技术指纹），不是漏洞。
根因是 `FINGERPRINT` 不在 SDD-02 的 `finding_type` 枚举内（见核对反馈 ⑤）。

### T16 · ZAP 适配器（被动扫描）

```
status = SUCCESS    耗时 = 1.3 秒    命中 = 6 条
category : {VULN: 1, WEAK_CONFIG: 5}
severity : {INFO: 1, LOW: 5}
审计留痕 : [{tool_id: zap, target: http://127.0.0.1:8080/, status: SUCCESS}]

合规后置检查：ZAP 主动扫描记录 = []        ← 全程未触发主动扫描
             被动扫描队列 recordsToScan = 0
```

命中：`Session Management Response Identified`（10112）、`Cookie No HttpOnly Flag` ×2（10010）、
`Cookie without SameSite Attribute` ×2（10054）、`Server Leaks Version Information`（10036）。
证据已脱敏（只含插件号、命中位置、参数名，不含响应正文）。

### T17 · Wappalyzer 适配器（进程内识别）

```
status = SUCCESS    耗时 = 0.1 秒    命中 = 2 条
[INFO] FINGERPRINT  Debian         技术栈：Debian（版本 未知；类别 Operating systems）
[INFO] FINGERPRINT  Apache 2.4.25  技术栈：Apache（版本 2.4.25；类别 Web servers）
```

### T18 / T19 · 篡改与外链挂马检测器

对 **F 组 T40 统一测试集 v1.0.0** 逐条对齐（`tests/integration/test_dataset_alignment.py`，
真机 HTTP 与读文件两条路径均通过）：

| 样例 | category | 期望 | 实测 |
| --- | --- | --- | --- |
| B-01 / B-02 | `MALICIOUS_LINK` | 正例 / 负例 | ✅ / ✅ |
| B-03 / B-04 | `MALICIOUS_SCRIPT` | 正例 / 负例 | ✅ / ✅ |
| C-01 / C-02 | `TAMPER` | 正例 / 负例 | ✅ / ✅ |
| E-02 | `MALICIOUS_LINK` | 负例（干净站） | ✅ |

### T12 / T13 · 真数据库落库

> 位于分支 `feature/c-sqlalchemy-repositories`（含 T12/T13 服务层），
> 测试：`tests/integration/test_sqlalchemy_repositories.py`（12 项，对真 PostgreSQL 16）。

验证要点：**换成 SQLAlchemy 仓储后服务层一行未改**；每条断言都新建仓储实例再读，
证明是真落库而非进程内存储。仓储层吸收了两处差异——时间（DB 列 naive ↔ 服务层 aware UTC）
与布尔（库内 `INTEGER` ↔ 服务层 `bool`）。

## 4. 原始证据清单

均位于 `tests/fixtures/tool_output/`：

| 文件 | 内容 |
| --- | --- |
| `nuclei_dvwa_2026-10-14.jsonl` | Nuclei 原始 JSONL 输出（19 条真实命中） |
| `nuclei_dvwa_2026-10-14.stderr.log` | Nuclei 的 INFO/WRN，含模板加载数与跳过原因（诊断用） |
| `zap_dvwa_2026-10-14.http.jsonl` | ZAP REST 每次请求与响应（含 `accessUrl`、`recordsToScan`、`alerts` 原文） |
| `wappalyzer_dvwa_2026-10-14.json` | python-Wappalyzer 原始识别结果 |
| `results_2026-10-14.json` | 三个适配器的归一化结果与审计留痕（汇总） |
| `nuclei_dvwa_2026-10-13.jsonl` | 早前一次直接以 docker 运行的 Nuclei 输出，用作解析器回归夹具 |

## 5. 真机验证中发现并修复的缺陷

以下缺陷**单测与人工样例均无法发现**，全部由真机运行暴露，并按 TDD 补了回归测试：

| # | 缺陷 | 后果 | 状态 |
| --- | --- | --- | --- |
| 1 | 限速单位错 60 倍：nuclei `-rate-limit` 是「每秒」，适配器传入了 manifest 的「每分钟」 | 实际 1800 次/分钟，为声明值的 60 倍 | 已修 |
| 2 | 速率语义混淆：契约 `rate_limit` 是**工具调用**速率（保护服务），被当成**目标侧**请求速率 | 调高工具调用配额会连带打爆目标 | 已修（解耦为 `params["target_rate_limit"]`） |
| 3 | ZAP 被动扫描是**异步**的，适配器 `accessUrl` 后立刻读告警 | **被动结果 100% 漏报**（实测队列未排空时 0 条，排空后 6 条） | 已修（轮询队列排空，含等待预算） |
| 4 | 超时后不终止工具：`status=TIMEOUT` 后容器仍持续请求目标 ≥3 分钟 | 违反验收标准 §4「目标负载受控」 | 已修（唯一容器名 + 超时/异常时终止） |
| 5 | 超时丢弃全部诊断：stderr 被缓冲，超时连日志文件都不创建 | 长扫描失败时零证据可查 | 已修（stderr 边跑边落盘） |
| 6 | 计划删除后会「复活」：`schedule` 表无 `deleted` 列，逻辑删除无法持久化 | 已删计划重新加载后继续被 `due()` 取到并触发巡检 | 已修（改为真删除） |
| 7 | `host.docker.internal` **间歇性**解析失败（Docker Desktop 内部 DNS） | 整个扫描被跳过、**0 命中**（实测 16.8s 空跑） | 已修（容器命令显式 `--add-host ...:host-gateway`） |

## 6. 未覆盖项与已知限制（如实记录）

1. **合规速率下跑不完真实扫描**：2809 个模板在 1 次/秒下需约 415 秒，而契约
   `timeout_seconds` 上限 300 秒。本次验证用的 30 次/秒是**显式记录的评估配置**，
   不代表生产默认值。根因是同步 `run()` 装不下长任务（核对反馈 ④）。
2. **信息类结果归类未定**：Nuclei 的信息类模板与 ZAP 的 informational 告警被判为 `VULN`；
   `FINGERPRINT` 是否落库为 Finding 待口径确认，未定前不入告警链路。
3. **工具容器非 root 未落地**：官方镜像默认 root，与验收标准 §4 冲突；
   `ContainerSpec.options` 已预留 `--user` 注入位，待部署时定 UID。
4. **T12/T13 路由层未实现**：服务层已真库验证，但 HTTP 接口层未挂载
   （依赖 `fastapi` 与 T09 认证）。
5. **容器内硬超时未实现**：适配器已在超时/异常时终止容器（缺陷 4），但若客户端进程被强杀，
   容器内仍缺乏自终止机制（核对反馈 ⑦ 第二层）。
6. **本记录不含检测效果指标**：召回率/准确率/误报率归 F 组 T48，本文件只记录过程与证据。

## 7. 变更记录

| 版本 | 日期 | 变更人 | 变更内容 | 影响范围 |
| --- | --- | --- | --- | --- |
| v1.0 | 2026-10-14 | C 组 | 首次发布：T15/T16/T17 真机验证结果、评估配置、原始证据清单、7 项真机缺陷 | T12–T19 验收 |
