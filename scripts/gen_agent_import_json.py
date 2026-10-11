# -*- coding: utf-8 -*-
"""生成恒脑平台可导入的网站安全智能巡检编排 JSON（对齐 T27，含平台答复修正）
修正（2026-10-11，依据 hengnao_import_format_answers.md）：
  1) 自规划节点 MCP 绑定 -> coding_agent_config.mcps（字符串数组），节点级 mcps 清空
  2) 根 input_parameters 增加非必填 input（提升兼容性）
输出: deploy/恒脑编排导入包/网站安全智能巡检_编排导入.json
"""
import io, json, os, uuid

OUT_DIR = r'D:\Desktop\AnHeng-work\deploy\恒脑编排导入包'
OUT = os.path.join(OUT_DIR, '网站安全智能巡检_编排导入.json')


def gen_uuid():
    return str(uuid.uuid4())


def std_fields(node_id, name, desc, ntype, extra=None):
    base = {
        "id": node_id,
        "name": name,
        "description": desc,
        "type": ntype,
        "input_parameters": [],
        "output_parameters": [],
        "result_key": "result_key",
        "tools": [],
        "mcps": [],
        "agents": [],
        "timeout": 300,
        "max_retries": 3,
        "max_working": 30,
        "max_reflections": 5,
        "use_memory": False,
        "log_memory": False,
        "iteration": False,
        "iteration_values": [],
        "on_error": 0,
        "no_action": False,
        "no_repeat": False,
    }
    if extra:
        base.update(extra)
    return base


MODEL_V4 = {"model": "HengNao-v4", "temperature": 0, "top_p": 1, "max_tokens": 4096, "top_k": -1, "reasoning": 1}

# 白名单 MCP server_id（✅ 档）
MCP_WHITELIST = [
    "859c5d16-83f8-43c6-95b2-ddf36e2df3e5",   # Nuclei 漏洞扫描
    "09e212eb-9241-4ef5-8788-02d3306a2e69",   # SSL/TLS 检测
    "0de71fa7-3b8b-4331-859b-8c1953e9066d",   # 证书透明查询
    "b9f3ce4a-7287-4441-b225-d3f019a9041e",   # 子域名发现
    "047a309d-4be4-40a4-b163-8c46ad25ddb4",   # 时间感知服务
    "78413ad0-85e3-4453-8c7b-21d99a4a2388",   # 数据处理工具
]

# ---------- 节点 0：交互授权确认 ----------
node0 = std_fields("node0", "授权确认与巡检范围收集", "向用户确认授权目标与巡检范围，取得明确授权后开始巡检。", "act_interaction",
    extra={
        "questions": [
            {
                "name": "target_url",
                "type": "type_input",
                "options": [],
                "message": {"type": "literal", "expr": "请提供已授权巡检的目标地址（如 http://127.0.0.1:8080）与巡检范围说明："},
                "timeout": 60,
            },
            {
                "name": "authorize",
                "type": "type_select",
                "options": [
                    {"name": "A", "value": {"type": "literal", "expr": "确认授权，开始巡检"}},
                    {"name": "B", "value": {"type": "literal", "expr": "取消巡检"}},
                ],
                "message": {"type": "literal", "expr": "已记录授权目标，请确认是否对该目标发起非破坏性安全巡检？"},
                "timeout": 30,
            },
        ],
        "interactions": [
            {
                "from": "msg",
                "stream": True,
                "output_expression": {
                    "type": "rendering",
                    "expr": "收到授权目标：{{$.node0.target_url._answer}}\n授权确认：{{$.node0.authorize._answer}}",
                },
                "output_parameters": [],
            }
        ],
    }
)

# ---------- 节点 1：自规划巡检（MCP 绑定在 coding_agent_config.mcps） ----------
node1 = std_fields("node1", "自规划安全巡检执行", "调用白名单安全工具对授权目标执行页面发现、漏洞与配置检测。", "act_coding_agent",
    extra={
        "input_parameters": [
            {"name": "target_url", "type": "string", "value": {"type": "reference", "expr": "$.node0.target_url._answer"}},
            {"name": "authorize", "type": "string", "value": {"type": "reference", "expr": "$.node0.authorize._answer"}},
        ],
        "model": MODEL_V4,
        "coding_agent_config": {
            "name": "网站安全巡检执行",
            "model": "GLM-5.1",
            "prompt": (
                "你是网站安全巡检执行智能体。任务：对授权目标执行非破坏性安全巡检。\n"
                "1) 先确认授权状态（authorize 必须为确认授权）；\n"
                "2) 使用 MCP 工具按顺序执行：页面/子域名发现 → SSL/TLS 检测 → Nuclei 漏洞扫描（仅非破坏模板）→ 证书透明查询；\n"
                "3) 严禁使用注入利用、暴力破解、破坏性操作；\n"
                "4) 每个发现记录 {url, type, level(high/medium/low/info), detail, evidence}；\n"
                "5) 输出 JSON：{\"findings\":[...], \"scan_summary\":\"...\"}。"
            ),
            "mcps": MCP_WHITELIST,
            "skills": ["a1b2fabb-6e0f-41f2-8540-de30f07f8106"],  # 安全审计工具包
        },
    }
)

# ---------- 节点 2：代码聚合分级 ----------
NODE2_CODE = (
    "import json\n"
    "def handler(args):\n"
    "    raw = args.input.scan_result\n"
    "    try:\n"
    "        data = json.loads(raw) if isinstance(raw, str) else (raw or {})\n"
    "    except Exception:\n"
    "        data = {}\n"
    "    findings = data.get(\"findings\") or []\n"
    "    seen, uniq = set(), []\n"
    "    for f in findings:\n"
    "        key = str(f.get(\"type\")) + \"|\" + str(f.get(\"url\")) + \"|\" + str(f.get(\"detail\"))\n"
    "        if key not in seen:\n"
    "            seen.add(key)\n"
    "            uniq.append(f)\n"
    "    level_map = {\"high\": \"高危\", \"medium\": \"中危\", \"low\": \"低危\", \"info\": \"信息\"}\n"
    "    counts = {\"高危\": 0, \"中危\": 0, \"低危\": 0, \"信息\": 0}\n"
    "    for f in uniq:\n"
    "        lv = level_map.get(str(f.get(\"level\", \"info\")), \"信息\")\n"
    "        f[\"level_cn\"] = lv\n"
    "        counts[lv] = counts.get(lv, 0) + 1\n"
    "    report = {\"total\": len(uniq), \"counts\": counts, \"findings\": uniq}\n"
    "    return {\"result\": json.dumps(report, ensure_ascii=False, indent=2)}\n"
)

node2 = std_fields("node2", "巡检结果聚合与分级报告", "对检测结果去重、分级并生成可读的风险报告。", "act_interpreter",
    extra={
        "input_parameters": [
            {"name": "scan_result", "type": "string", "value": {"type": "reference", "expr": "$.node1.result_key"}},
        ],
        "interpreter": {"lang": "python", "code": NODE2_CODE},
        "output_parameters": [
            {
                "name": "report_text",
                "type": "string",
                "value": {"type": "reference", "expr": "$.result_key"},
            },
            {
                "name": "report_json",
                "type": "string",
                "value": {"type": "jsonpath", "expr": "$.result", "text": {"type": "reference", "expr": "$.result_key"}},
            },
        ],
    }
)

# ---------- 根 ----------
root = {
    "cot_version": "v2",
    "eng_version": "v2.3.8",
    "name": "网站安全智能巡检智能体",
    "description": "对已授权网站开展周期性安全巡检，自动发现页面与资源，编排白名单安全工具完成漏洞、弱配置、敏感信息与篡改检测，形成可解释、可跟踪、可复测的风险闭环。",
    "prologue": "本智能体仅对已授权目标执行非破坏性安全巡检，请先提供授权目标与巡检范围。",
    "entry": "node0",
    "uid": "",
    "org": "",
    "id": gen_uuid(),
    "type": 0,
    "model": MODEL_V4,
    "session_round": 10,
    "input_parameters": [
        {"name": "input", "type": "string", "description": "用户输入（可选，非必填）：巡检指令或补充说明"},
    ],
    "output_parameters": [],
    "output_expression": {
        "type": "rendering",
        "expr": "巡检完成，风险分级统计：\n{{$.node2.report_text}}",
    },
    "private_envs": [],
    "public_envs": [],
    "nodes": [node0, node1, node2],
    "edges": [
        {"src_node": "node0", "dst_node": "node1"},
        {"src_node": "node1", "dst_node": "node2"},
    ],
}

os.makedirs(OUT_DIR, exist_ok=True)
with io.open(OUT, "w", encoding="utf-8") as f:
    json.dump(root, f, ensure_ascii=False, indent=2)
print("generated:", OUT)
print("root id:", root["id"])
