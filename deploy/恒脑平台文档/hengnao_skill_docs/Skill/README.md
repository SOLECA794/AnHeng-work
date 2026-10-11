# 恒脑智能体脚本生成 Skill

可独立打包的 Cursor Agent Skill，用于生成恒脑（HengBrain）平台智能体 JSON 编排脚本。

## 目录结构

```
Skill/
├── SKILL.md                      # Cursor Skill 入口（必需）
├── README.md                     # 本说明
├── OPEN_QUESTIONS.md             # 待确认项（持续维护）
├── docs/                         # 平台知识 Markdown
│   ├── 00-platform-overview.md
│   ├── 01-prompt-practice.md
│   ├── 02-plugins-knowledge-base.md
│   ├── 03-params-iteration.md
│   ├── 04-edges.md
│   ├── 05-files-interaction.md
│   └── 06-faq-best-practices.md
├── reference/
│   ├── import-compatibility.md   # 导入兼容性硬规则（优先阅读）
│   ├── platform-catalog.md       # 运行时拉取当前环境资源
│   ├── node-types.md
│   ├── json-patterns.md
│   ├── prompt-template.md
│   ├── interaction-node.md
│   └── code-node-libraries.md
├── scripts/
│   ├── fetch_platform_catalog.py # 按 DB + user-id 拉插件/MCP/Skill/知识库
│   ├── query_user_tenant.py      # 只读查询用户所属租户（自动用内置驱动）
│   ├── fetch_vendor.sh           # 组装离线数据库驱动到 vendor/python
│   ├── pack_skill.sh             # 打离线 zip（自动含 vendor/python）
│   ├── requirements.txt          # 离线驱动清单（pg8000）
│   └── validate_agent_json.py    # 导入前静态校验（可 --catalog）
├── vendor/
│   └── python/                   # 打包时临时生成；zip 内自带，脚本自动优先加载
└── examples/
    ├── README.md
    ├── coding-agent-keypoints.md
    ├── compliance-check-keypoints.md
    ├── class-info-keypoints.md
    ├── security-roster-keypoints.md
    └── global-file-memory-keypoints.md
```

## 安装到 Cursor

### 项目内（推荐）

```bash
cd hengNaoAgentScriptGen
ln -sfn ../../Skill .cursor/skills/hengnao-agent-script
```

### 全局

```bash
cp -r Skill ~/.cursor/skills/hengnao-agent-script
```

重启 Cursor 或 Reload Window 后，对话中提及「恒脑智能体脚本」或 `@hengnao-agent-script` 即可触发。

## 导入兼容性（必读）

平台导入器对**节点字段完整度**有硬性要求。生成脚本后必须：

```bash
python3 Skill/scripts/validate_agent_json.py <agent.json>
# 若已拉取当前环境目录：
python3 Skill/scripts/validate_agent_json.py <agent.json> --catalog catalog.json
```

用户提供恒脑库连接和用户 ID 时，先拉目录再生成（真实 ID，最大化接线）：

```bash
export HENGNAO_DB_HOST=<host> HENGNAO_DB_PORT=<port>
export HENGNAO_DB_USER=<user> HENGNAO_DB_PASSWORD=<password>
export HENGNAO_DB_NAME=<dbname>
python3 Skill/scripts/fetch_platform_catalog.py --user-id <id> -o catalog.json
```

只需确认用户归属租户时，使用更轻量的只读查询：

```bash
python3 Skill/scripts/query_user_tenant.py --user-id <id> --tenant-id <expected-tenant-id> --pretty
```

数据库信息取值顺序：系统环境变量优先；缺失字段仅从用户显式提供的 input 数据库配置补齐，并作为进程内临时参数使用；仍不完整时提示“未提供恒脑数据库连接信息，部分业务受限”，继续生成占位符骨架。详见 [reference/platform-catalog.md](reference/platform-catalog.md)。

离线包会自带 `vendor/python`（纯 Python 驱动 `pg8000` 及依赖），`fetch_platform_catalog.py` 会自动优先加载，不需要每次沙箱再联网装库。说明见 [reference/platform-catalog.md](reference/platform-catalog.md)。不要把数据库密码提交到 git。

全部 PASS 再交付。完整规则见 [reference/import-compatibility.md](reference/import-compatibility.md)。

## 示例策略

- Skill 包内示例只保留**关键片段版**（拓扑 + 关键字段 + 关键边）
- 不把超长完整 JSON 塞进提示词
- 生成提示词见 `Prompts/SkillGeneratePrompt.md`（已与本 Skill 对齐）

## 配套资源（不在 Skill 包内）

| 路径 | 说明 |
|------|------|
| `Prompts/SkillGeneratePrompt.md` | 与 Skill 对齐的精简生成提示词 |
| `Prompts/*.pdf` | 原始培训材料（已提取至 `Skill/docs/`） |

## 维护流程

1. 用户确认 [OPEN_QUESTIONS.md](OPEN_QUESTIONS.md) 中的项
2. 更新对应 `docs/` 或 `reference/` 章节
3. 在 OPEN_QUESTIONS 更新日志记录日期
4. 若有新 PDF/平台变更，新增 `docs/07-*.md` 并更新 SKILL.md 索引
5. 导入规则变更时同步 `import-compatibility.md` 与 `validate_agent_json.py`

## 离线打包

```bash
bash Skill/scripts/pack_skill.sh
```

该命令会：

1. 临时安装 `scripts/requirements.txt`
2. 复制到 `Skill/vendor/python/`
3. 打成带离线驱动的 `zip`

目标环境直接使用包内 `Skill/scripts/fetch_platform_catalog.py` 或 `Skill/scripts/query_user_tenant.py` 即可，无需再下载数据库驱动。

## 来源

- 安恒信息中央研究院 · 黄冲 · 智能体合伙人培训（2025.3.18–3.26）
- 内部实践文档与 2026-08-16 导入兼容性实测
