from __future__ import annotations

import json
import re
from typing import Any


GRAPH_PROMPT = """你是控制流图结构化助手。
任务：把用户给出的自然语言流程描述转换为控制流图 JSON 草稿。
图约束：
1. 节点类型只能是 start、end、process、decision。
2. condition 只允许出现在 decision 节点上，而且 decision 节点必须有 condition。
3. 每个 decision 节点必须恰好有两条出边。
4. decision 节点两条出边的 label 必须分别是“是”和“否”。
5. 非 decision 节点的边不要使用“是/否”标签。
6. 默认只生成一个 start 节点。
7. 输出必须是 JSON 对象，不要输出解释，不要输出 Markdown 代码块。
输出格式：
{
  "version": "1.0",
  "title": "流程标题",
  "nodes": [
    {"id": "n1", "type": "start", "label": "开始"},
    {"id": "n2", "type": "process", "label": "示例步骤"},
    {"id": "n3", "type": "decision", "label": "是否满足条件", "condition": "满足条件"},
    {"id": "n4", "type": "end", "label": "结束"}
  ],
  "edges": [
    {"id": "e1", "source": "n1", "target": "n2"},
    {"id": "e2", "source": "n2", "target": "n3"},
    {"id": "e3", "source": "n3", "target": "n4", "label": "是"},
    {"id": "e4", "source": "n3", "target": "n2", "label": "否"}
  ],
  "assumptions": []
}
"""

GRAPH_REPAIR_PROMPT = """你是控制流图修复助手。
下面这个控制流图 JSON 草稿未通过校验。请只输出修复后的 JSON 对象，不要输出解释，不要输出 Markdown。
用户原始描述：{description}

当前图草稿：
{graph_json}

校验错误：
{errors}

修复要求：
1. 保持节点 id 和边 id 稳定，除非确实无法修复。
2. decision 节点必须有 condition。
3. decision 节点必须恰好两条出边，且 label 是“是”和“否”。
4. start 节点不能有入边，end 节点不能有出边。
5. 非 decision 节点不要发出“是/否”分支。
6. 输出必须仍然是合法 JSON 对象。
"""

GRAPH_EDIT_PROMPT = """你是控制流图编辑助手。
用户已经有一份控制流图草稿。你的任务不是重新从零生成，而是根据用户这一次的修改要求，在现有图草稿上做最小必要修改。

用户当前修改要求：
{instruction}

当前图草稿：
{graph_json}

编辑要求：
1. 尽量保持原有节点 id 和边 id 稳定。
2. 只修改与本次要求有关的部分。
3. 继续满足控制流图约束：节点类型只能是 start、end、process、decision。
4. decision 节点必须有 condition，且必须恰好有两条出边，标签分别为“是”和“否”。
5. start 节点不能有入边，end 节点不能有出边。
6. 只输出 JSON 对象，不要输出解释，不要输出 Markdown。
"""

GRAPH_EDIT_CONFIRM_PROMPT = """你是控制流图修改确认助手。
用户已经有一份控制流图草稿。你的任务不是直接修改草稿，而是先用自然语言复述你理解到的修改意图，让用户确认。

用户这次的修改要求：
{instruction}

当前图草稿：
{graph_json}

输出要求：
1. 只输出给用户看的简短说明，不要输出 JSON，不要输出 Markdown 代码块。
2. 明确说明你理解到的修改目标。
3. 如果你认为这次修改会影响边的连接关系，要明确说出哪些流程先后关系或分支关系会变化。
4. 用 2 到 4 句中文表达，语气自然，不要像日志。
5. 结尾明确提示用户：如果理解无误，请回复“确认修改”。
"""

CONFIRM_CLASSIFY_PROMPT = """你是一个确认意图分类器。
当前场景：助手刚刚复述了一次控制流图修改意图，正在等待用户确认后再执行改图。

待确认的修改内容：
{pending_instruction}

用户现在的回复：
{message}

判断规则：
1. 如果用户这句话是在表示认可、确认、同意按刚才那次修改去执行，输出 confirm。
2. 如果用户这句话是在补充新要求、继续修改、提出疑问、否认、纠正、闲聊，输出 not_confirm。
3. 即使用户只说很短的话，只要语义上是在点头同意，也输出 confirm。
4. 只允许输出一个小写单词：confirm 或 not_confirm。不要输出任何解释。

示例：
待确认内容：删除"重试"节点，直接从"失败"连到"结束"
用户回复：确认修改 → confirm

待确认内容：在"启动"后添加"初始化"节点
用户回复：好的 → confirm

待确认内容：把"超时"分支改为指向"报警"节点
用户回复：对 → confirm

待确认内容：删除多余的判断节点
用户回复：等等，我想保留那个节点 → not_confirm

待确认内容：将"审核"节点移到"提交"之前
用户回复：不对，我的意思是移到"提交"之后 → not_confirm

待确认内容：撤回之前添加的修改，恢复原始结构
用户回复：确认修改 → confirm
"""

REQUEST_INTENT_CLASSIFY_PROMPT = """你是一个请求路由分类器。
你的任务是判断这条用户消息应该进入哪种处理流程。

当前是否已有控制流图草稿：
{has_current_graph}

用户消息：
{message}

可选标签：
1. chat
含义：普通对话、解释、问答、闲聊，不应该修改当前图，也不应该新生成图，也不应该生成路径。

2. graph_create
含义：用户要新建、重新生成、重画一张控制流图，即使当前已经有图，也应视为新建而不是修改旧图。

3. graph_edit
含义：用户要基于当前已有控制流图做修改、补充、删除、重连、调整节点或分支。
如果当前没有图，不要输出这个标签。

4. path_generate
含义：用户要基于当前已有控制流图执行路径生成或调用后续 exe 流水线。
如果当前没有图，不要输出这个标签。

5. undo
含义：用户想撤回、回退、取消上一次对图的修改，恢复到之前的版本。
例如：”撤回修改””回退””撤销””还是不要改了””恢复原来的图””不加了，撤回吧”。
如果当前没有图，不要输出这个标签。

判定要求：
1. “再生成一个新的控制流图””重新生成一个流程图” 归类为 graph_create，不是 graph_edit。
2. 普通问答，即使上下文里有图，也归类为 chat。
3. 只有明确要改当前图时，才归类为 graph_edit。
4. 只有明确要生成路径或执行算法时，才归类为 path_generate。
5. 撤回、回退、取消修改等操作归类为 undo，不是 graph_edit。
6. 只输出一个小写标签：chat、graph_create、graph_edit、path_generate、undo。
"""

GRAPH_REWIRE_PROMPT = """你是控制流图重连助手。
用户已经有一份控制流图草稿。当前任务不是调整视觉位置，而是修改流程语义与边连接关系。

用户当前修改要求：
{instruction}

当前图草稿：
{graph_json}

重连要求：
1. 本次修改必须体现在 edges 上，不能只调整节点文本、节点顺序描述或视觉位置。
2. 当用户说“在……后面”“应该先……再……”“分支应该到……”或“要改变边”时，优先理解为执行顺序和边连接关系要变。
3. 尽量保持原有节点 id 和边 id 稳定；如必须新增或删除边，也要让最终流程符合用户描述。
4. decision 节点必须有 condition，且恰好两条出边，标签分别为“是”和“否”。
5. start 节点不能有入边，end 节点不能有出边。
6. 如果用户在描述“某节点应该在某判断节点后面”，通常意味着该节点应成为该判断节点某条分支的目标节点，而不是该判断节点的前驱节点。
7. 只输出 JSON 对象，不要输出解释，不要输出 Markdown。
"""

GRAPH_TYPE_CLASSIFY_PROMPT = """你是一个图类型分类器。
判断用户的描述是要生成"控制流图（CFG）"还是"状态图（State Diagram）"。

控制流图特征：描述程序执行步骤、流程控制、判断分支、循环、顺序处理过程。
状态图特征：描述对象/系统在不同状态之间的迁移、状态转换、生命周期、事件驱动的状态变化。

用户描述：
{message}

判断要求：
1. 如果描述中出现"状态""迁移""状态机""状态转移""生命周期""状态转换""转换条件"等词，输出 state_diagram。
2. 如果描述的是"流程""步骤""判断""循环""执行顺序""控制流"等词，输出 cfg。
3. 不确定时默认输出 cfg。
4. 只输出一个小写标签：cfg 或 state_diagram。不要输出任何解释。
"""

STATE_DIAGRAM_INTENT_PATTERNS = [
    r"状态图",
    r"状态机",
    r"状态迁移",
    r"状态转移",
    r"状态转换",
    r"生命周期",
    r"画.*状态",
    r"生成.*状态图",
    r"状态.*迁移",
    r"迁移.*状态",
    r"事件.*触发.*状态",
]


def detect_state_diagram_intent(message: str) -> bool:
    if not message.strip():
        return False
    for pattern in STATE_DIAGRAM_INTENT_PATTERNS:
        if re.search(pattern, message):
            return True
    return False


GRAPH_INTENT_PATTERNS = [
    r"控制流图",
    r"流程图",
    r"画.*流程",
    r"画.*控制流",
    r"生成.*流程图",
    r"生成.*控制流图",
    r"转换.*流程图",
    r"转成.*xml",
    r"输出.*xml",
    r"开始节点",
    r"结束节点",
    r"终止节点",
    r"判断节点",
    r"普通控制流节点",
]

REWIRE_INTENT_PATTERNS = [
    r"改变边",
    r"改边",
    r"重连",
    r"连到",
    r"接到",
    r"分支.*到",
    r"在.*后面",
    r"应该先.*再",
    r"顺序.*错",
    r"流程.*错",
    r"前驱",
    r"后继",
]

PATH_GENERATION_INTENT_PATTERNS = [
    r"生成路径",
    r"帮我生成路径",
    r"生成测试路径",
    r"跑路径",
    r"执行路径生成",
    r"调用.*activity_to_state",
    r"生成.*path",
]

GRAPH_EDIT_INTENT_PATTERNS = [
    r"修改.*控制流图",
    r"改.*控制流图",
    r"调整.*控制流图",
    r"编辑.*控制流图",
    r"更新.*控制流图",
    r"在.*后面",
    r"删除.*节点",
    r"新增.*节点",
    r"增加.*节点",
    r"插入.*节点",
    r"替换.*节点",
    r"修改.*节点",
    r"修改.*分支",
    r"增加.*分支",
    r"删除.*分支",
    r"改.*边",
    r"重连",
]


def detect_graph_intent(message: str) -> bool:
    if not message.strip():
        return False

    for pattern in GRAPH_INTENT_PATTERNS:
        if re.search(pattern, message):
            return True

    keyword_hits = 0
    for keyword in ["流程", "节点", "分支", "判断", "条件", "xml", "开始", "结束"]:
        if keyword in message:
            keyword_hits += 1

    return keyword_hits >= 3


def detect_rewire_intent(message: str) -> bool:
    if not message.strip():
        return False

    for pattern in REWIRE_INTENT_PATTERNS:
        if re.search(pattern, message):
            return True

    keyword_hits = 0
    for keyword in ["后面", "前面", "顺序", "边", "分支", "后继", "前驱", "连接"]:
        if keyword in message:
            keyword_hits += 1

    return keyword_hits >= 2


def detect_path_generation_intent(message: str) -> bool:
    if not message.strip():
        return False

    for pattern in PATH_GENERATION_INTENT_PATTERNS:
        if re.search(pattern, message, re.IGNORECASE):
            return True

    keyword_hits = 0
    for keyword in ["路径", "生成", "exe", "xml", "activity_to_state"]:
        if keyword in message:
            keyword_hits += 1

    return keyword_hits >= 2


def detect_graph_edit_intent(message: str) -> bool:
    if not message.strip():
        return False

    for pattern in GRAPH_EDIT_INTENT_PATTERNS:
        if re.search(pattern, message, re.IGNORECASE):
            return True

    keyword_hits = 0
    for keyword in ["修改", "改成", "删除", "新增", "增加", "插入", "替换", "调整", "重连"]:
        if keyword in message:
            keyword_hits += 1

    return keyword_hits >= 2


def extract_json_object(text: str) -> dict[str, Any]:
    stripped = (text or "").strip()
    if not stripped:
        raise ValueError("模型返回为空。")

    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{[\s\S]*\}", stripped)
    if not match:
        raise ValueError("未找到合法 JSON 对象。")
    return json.loads(match.group(0))


def normalize_graph(payload: dict[str, Any]) -> dict[str, Any]:
    graph = payload if isinstance(payload, dict) else {}
    normalized = {
        "version": str(graph.get("version") or "1.0"),
        "title": str(graph.get("title") or "未命名流程"),
        "nodes": [],
        "edges": [],
        "assumptions": graph.get("assumptions") if isinstance(graph.get("assumptions"), list) else [],
    }

    seen_node_ids: set[str] = set()
    for index, node in enumerate(graph.get("nodes") or [], start=1):
        if not isinstance(node, dict):
            continue
        node_id = str(node.get("id") or f"n{index}")
        while node_id in seen_node_ids:
            node_id = f"{node_id}_{index}"
        seen_node_ids.add(node_id)

        node_type = str(node.get("type") or "process").lower()
        if node_type not in {"start", "end", "process", "decision"}:
            node_type = "process"

        item = {
            "id": node_id,
            "type": node_type,
            "label": str(node.get("label") or node_id),
        }
        if node_type == "decision":
            item["condition"] = str(node.get("condition") or item["label"])
        normalized["nodes"].append(item)

    seen_edge_ids: set[str] = set()
    for index, edge in enumerate(graph.get("edges") or [], start=1):
        if not isinstance(edge, dict):
            continue
        edge_id = str(edge.get("id") or f"e{index}")
        while edge_id in seen_edge_ids:
            edge_id = f"{edge_id}_{index}"
        seen_edge_ids.add(edge_id)
        item = {
            "id": edge_id,
            "source": str(edge.get("source") or ""),
            "target": str(edge.get("target") or ""),
        }
        if edge.get("label") not in (None, ""):
            item["label"] = str(edge.get("label"))
        normalized["edges"].append(item)

    return normalized


def validate_graph(graph: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])

    if not nodes:
        errors.append("至少需要一个节点。")
        return errors

    node_map = {node.get("id"): node for node in nodes if isinstance(node, dict) and node.get("id")}
    incoming = {node_id: [] for node_id in node_map}
    outgoing = {node_id: [] for node_id in node_map}

    start_nodes = [node for node in nodes if node.get("type") == "start"]
    if len(start_nodes) != 1:
        errors.append("必须恰好存在一个 start 节点。")

    for node in nodes:
        node_id = node.get("id")
        node_type = node.get("type")
        label = str(node.get("label") or "").strip()
        if not label:
            errors.append(f"节点 {node_id} 缺少 label。")
        if node_type == "decision":
            if not str(node.get("condition") or "").strip():
                errors.append(f"decision 节点 {node_id} 缺少 condition。")
        elif node.get("condition"):
            errors.append(f"非 decision 节点 {node_id} 不应带有 condition。")

    for edge in edges:
        source = edge.get("source")
        target = edge.get("target")
        if source not in node_map:
            errors.append(f"边 {edge.get('id')} 的 source 不存在：{source}")
            continue
        if target not in node_map:
            errors.append(f"边 {edge.get('id')} 的 target 不存在：{target}")
            continue
        outgoing[source].append(edge)
        incoming[target].append(edge)

    for node in nodes:
        node_id = node["id"]
        node_type = node["type"]
        node_outgoing = outgoing.get(node_id, [])
        node_incoming = incoming.get(node_id, [])

        if node_type == "start":
            if node_incoming:
                errors.append(f"start 节点 {node_id} 不能有入边。")
            if not node_outgoing:
                errors.append(f"start 节点 {node_id} 必须至少有一条出边。")
        elif node_type == "end":
            if node_outgoing:
                errors.append(f"end 节点 {node_id} 不能有出边。")
            if not node_incoming:
                errors.append(f"end 节点 {node_id} 必须至少有一条入边。")
        elif node_type == "decision":
            if len(node_outgoing) != 2:
                errors.append(f"decision 节点 {node_id} 必须恰好有两条出边。")
            if node_outgoing:
                labels = {str(edge.get("label") or "") for edge in node_outgoing}
                if labels != {"是", "否"}:
                    errors.append(f"decision 节点 {node_id} 的两条出边标签必须是“是”和“否”。")
        else:
            for edge in node_outgoing:
                if str(edge.get("label") or "") in {"是", "否"}:
                    errors.append(f"非 decision 节点 {node_id} 的出边不应使用“是/否”标签。")

    return errors


def local_repair_graph(graph: dict[str, Any]) -> dict[str, Any]:
    repaired = normalize_graph(graph)
    node_map = {node["id"]: node for node in repaired["nodes"]}

    incoming = {node_id: [] for node_id in node_map}
    outgoing = {node_id: [] for node_id in node_map}
    filtered_edges = []

    for edge in repaired["edges"]:
        source = edge.get("source")
        target = edge.get("target")
        if source not in node_map or target not in node_map:
            continue
        filtered_edges.append(edge)
        outgoing[source].append(edge)
        incoming[target].append(edge)

    repaired["edges"] = filtered_edges

    for node in repaired["nodes"]:
        node_id = node["id"]
        node_type = node["type"]
        node_outgoing = outgoing.get(node_id, [])
        if node_type == "decision":
            node["condition"] = str(node.get("condition") or node["label"])
            for index, edge in enumerate(node_outgoing[:2]):
                edge["label"] = "是" if index == 0 else "否"
            if len(node_outgoing) > 2:
                extra_edges = node_outgoing[2:]
                repaired["edges"] = [edge for edge in repaired["edges"] if edge not in extra_edges]
        else:
            node.pop("condition", None)
            for edge in node_outgoing:
                edge.pop("label", None)

    start_nodes = [node for node in repaired["nodes"] if node["type"] == "start"]
    if not start_nodes and repaired["nodes"]:
        repaired["nodes"][0]["type"] = "start"

    if repaired["nodes"] and not any(node["type"] == "end" for node in repaired["nodes"]):
        repaired["nodes"][-1]["type"] = "end"

    return normalize_graph(repaired)


def edge_signature(graph: dict[str, Any]) -> set[tuple[str, str, str, str]]:
    signature: set[tuple[str, str, str, str]] = set()
    for edge in graph.get("edges", []):
        signature.add(
            (
                str(edge.get("id") or ""),
                str(edge.get("source") or ""),
                str(edge.get("target") or ""),
                str(edge.get("label") or ""),
            )
        )
    return signature


def build_fallback_graph(description: str, reason: str = "") -> dict[str, Any]:
    assumptions = ["模型生成失败，当前草稿为占位图。"]
    if reason:
        assumptions.append(f"失败原因：{reason}")
    if description:
        assumptions.append(f"原始描述：{description[:120]}")
    return {
        "version": "1.0",
        "title": "控制流图草稿",
        "nodes": [
            {"id": "fb_n1", "type": "start", "label": "开始"},
            {"id": "fb_n2", "type": "process", "label": "待补充步骤"},
            {"id": "fb_n3", "type": "end", "label": "结束"},
        ],
        "edges": [
            {"id": "fb_e1", "source": "fb_n1", "target": "fb_n2"},
            {"id": "fb_e2", "source": "fb_n2", "target": "fb_n3"},
        ],
        "assumptions": assumptions,
    }
