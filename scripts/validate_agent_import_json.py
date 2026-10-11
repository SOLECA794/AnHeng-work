# -*- coding: utf-8 -*-
"""导入 JSON 自检（正式版，保留在 scripts/）
依据: hengnao_skill_docs import-compatibility.md + 平台答复（MCP 位置/根参数）
"""
import io, json, sys, re

PATH = r'D:\Desktop\AnHeng-work\deploy\恒脑编排导入包\网站安全智能巡检_编排导入.json'
REQ_FIELDS = ["id","name","description","type","input_parameters","output_parameters","result_key",
              "tools","mcps","agents","timeout","max_retries","max_working","max_reflections",
              "use_memory","log_memory","iteration","iteration_values","on_error","no_action","no_repeat"]
fails = []


def chk(cond, msg):
    if not cond:
        fails.append(msg)


with io.open(PATH, encoding="utf-8") as f:
    text = f.read()


def dup_keys(obj, path="root"):
    if isinstance(obj, dict):
        keys = list(obj.keys())
        chk(len(keys) == len(set(keys)), "{} 存在重复键".format(path))
        for k, v in obj.items():
            dup_keys(v, path + "." + k)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            dup_keys(v, "{}[{}]".format(path, i))


dup_keys(json.loads(text))

root = json.loads(text)
chk(root.get("cot_version") != root.get("eng_version"), "cot_version 与 eng_version 相同（禁止）")
chk(root.get("cot_version") == "v2", "cot_version 应为 v2")
chk(root.get("eng_version") == "v2.3.8", "eng_version 应为 v2.3.8")
chk(len(root.get("description", "")) >= 8, "根 description 长度 <8")
chk(len(root.get("prologue", "")) >= 8, "根 prologue 长度 <8 或缺失")
chk(isinstance(root.get("id"), str) and len(root.get("id", "")) > 10, "根 id 缺失/过短（应为真实 UUID）")
chk(isinstance(root.get("model"), dict), "根 model 不是对象")
chk(isinstance(root.get("type"), int), "根 type 缺失")
for p in root.get("input_parameters", []):
    chk("name" in p and "type" in p, "根入参缺 name/type: {}".format(p))

node_ids = set()
for n in root.get("nodes", []):
    nid = n.get("id")
    chk(nid not in node_ids, "节点 id 重复: {}".format(nid))
    node_ids.add(nid)
    missing = [k for k in REQ_FIELDS if k not in n]
    chk(not missing, "节点 {} 缺字段: {}".format(nid, missing))
    chk(len(n.get("description", "")) >= 8, "节点 {} description <8".format(nid))
    if n.get("type") in ("llm_mixup", "act_coding_agent"):
        chk(isinstance(n.get("model"), dict), "节点 {} model 不是对象".format(nid))
    for p in n.get("input_parameters", []):
        chk("value" in p, "节点 {} 入参 {} 缺 value".format(nid, p.get("name")))
    for p in n.get("output_parameters", []):
        chk("value" in p, "节点 {} 出参 {} 缺 value".format(nid, p.get("name")))
    if n.get("type") == "act_interpreter":
        chk("interpreter" in n and n["interpreter"].get("code"), "代码节点缺 interpreter.code")
    if n.get("type") == "act_coding_agent":
        chk("coding_agent_config" in n, "自规划节点缺 coding_agent_config")
        cag = n["coding_agent_config"]
        chk(isinstance(cag.get("model"), str) and cag.get("model"), "coding_agent_config.model 应为非空字符串")
        chk("mcps" in cag and isinstance(cag.get("mcps"), list) and all(isinstance(m, str) for m in cag["mcps"]),
            "coding_agent_config.mcps 应为字符串数组（平台答复要求）")
        chk("skills" in cag and isinstance(cag.get("skills"), list) and all(isinstance(s, str) for s in cag["skills"]),
            "coding_agent_config.skills 应为字符串数组")
        chk(len(n.get("mcps", [])) == 0, "自规划节点级 mcps 应清空（绑定在 coding_agent_config.mcps）")

edges = root.get("edges", [])
adj = {}
for e in edges:
    adj.setdefault(e["src_node"], []).append(e["dst_node"])
reach = set()
stack = [root.get("entry")]
while stack:
    cur = stack.pop()
    if cur in reach:
        continue
    reach.add(cur)
    stack.extend(adj.get(cur, []))
for n in root.get("nodes", []):
    chk(n["id"] in reach, "节点 {} 从 entry 不可达（死节点）".format(n["id"]))
srcs = {}
for e in edges:
    if not e.get("parallel") and "condition" not in e:
        srcs[e["src_node"]] = srcs.get(e["src_node"], 0) + 1
for s, cnt in srcs.items():
    chk(cnt <= 1, "源 {} 有多条普通边（{}）".format(s, cnt))

print("FAILS: {}".format(len(fails)))
for f in fails:
    print(" -", f)
sys.exit(1 if fails else 0)
