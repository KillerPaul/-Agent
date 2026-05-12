from __future__ import annotations

import json
import re
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

# ── constants ─────────────────────────────────────────────────────────────────

VALID_NODE_TYPES: set[str] = {
    "initial", "final", "simple",
    "sequential_composite", "concurrent_composite",
    "fork", "join",
}
VALID_EVENT_TYPES: set[str] = {
    "call_event", "change_event", "time_event", "signal_event",
}

# ── Pydantic models ───────────────────────────────────────────────────────────

EventType = Literal["call_event", "change_event", "time_event", "signal_event"]
NodeType = Literal[
    "initial", "final", "simple",
    "sequential_composite", "concurrent_composite",
    "fork", "join",
]


class TransitionEvent(BaseModel):
    event_id: str
    event_type: EventType
    condition: Optional[str] = None


class Transition(BaseModel):
    id: str
    source: str
    target: str
    event: Optional[TransitionEvent] = None


class StateNode(BaseModel):
    id: str
    type: NodeType
    label: str
    entry_actions: list[str] = Field(default_factory=list)
    do_actions: list[str] = Field(default_factory=list)
    exit_actions: list[str] = Field(default_factory=list)
    sub_diagram: Optional["StateDiagram"] = None


class StateDiagram(BaseModel):
    version: str = "1.0"
    title: str
    nodes: list[StateNode] = Field(default_factory=list)
    transitions: list[Transition] = Field(default_factory=list)


StateNode.model_rebuild()


class CandidateState(BaseModel):
    name: str
    type: str = "simple"
    description: str = ""


class StateRequirements(BaseModel):
    title: str
    description: str = ""
    candidate_states: list[CandidateState] = Field(default_factory=list)
    candidate_transitions: list[str] = Field(default_factory=list)


# ── prompts ───────────────────────────────────────────────────────────────────

STATE_REQUIREMENTS_PROMPT = """你是状态图需求分析助手。
从用户自然语言描述中提取候选状态和迁移，输出 JSON（不要 Markdown，不要解释）。

用户描述：
{description}

输出格式：
{{
  "title": "状态图标题",
  "description": "一句话概括",
  "candidate_states": [
    {{"name": "状态名", "type": "simple", "description": "状态职责简述"}}
  ],
  "candidate_transitions": [
    "状态A 收到 事件X 后迁移到 状态B（条件：...）"
  ]
}}

类型说明：
- simple：简单状态
- sequential_composite：内部有顺序子流程的复合状态
- concurrent_composite：内部有并发分支的复合状态
"""

STATE_DIAGRAM_PROMPT = """你是状态图生成助手。
根据描述生成状态图 JSON（不要 Markdown 代码块，不要解释）。

描述：
{description}

节点类型：
- initial：初始伪状态（整图唯一）
- final：终止状态（至少一个）
- simple：简单状态，可有 entry_actions / do_actions / exit_actions（字符串数组）
- sequential_composite：顺序复合状态，sub_diagram 设为 null（系统另行生成）
- concurrent_composite：并发复合状态，sub_diagram 设为 null（系统另行生成）
- fork：分叉伪状态（1入多出，无 sub_diagram）
- join：汇合伪状态（多入1出，无 sub_diagram）

转移：event 可为 null；有事件时包含 event_id、event_type（call_event|change_event|time_event|signal_event）、condition（可为 null）。

当前嵌套深度：{depth}（深度 >= 3 时禁止生成 sequential_composite / concurrent_composite 节点）

示例输出：
{{
  "version": "1.0",
  "title": "示例状态图",
  "nodes": [
    {{"id": "n1", "type": "initial", "label": "初始"}},
    {{"id": "n2", "type": "simple", "label": "空闲", "entry_actions": ["初始化"], "do_actions": [], "exit_actions": []}},
    {{"id": "n3", "type": "final", "label": "终止"}}
  ],
  "transitions": [
    {{"id": "t1", "source": "n1", "target": "n2", "event": null}},
    {{"id": "t2", "source": "n2", "target": "n3", "event": {{"event_id": "ev1", "event_type": "signal_event", "condition": null}}}}
  ]
}}
"""

STATE_SUB_DIAGRAM_PROMPT = """你是状态图子图生成助手。
为以下复合状态生成子状态图 JSON（不要 Markdown，不要解释）。

父图背景：{parent_description}
复合状态名：{label}（{node_type}型）
约束：{constraint}
当前嵌套深度：{depth}（深度 >= 3 时禁止生成 sequential_composite / concurrent_composite 节点）

输出格式：包含 version、title、nodes、transitions 字段，节点和转移规则与顶层状态图相同。
"""


SD_FEEDBACK_CLASSIFY_PROMPT = """你是状态图草案反馈分类器。
用户看完草案后给出了反馈，判断反馈属于哪种类型。

当前草案：
{draft}

用户反馈：
{message}

判断规则：
1. 用户表示认可/确认/同意/好的 → 输出 confirm
2. 用户提出修改要求（增加/删除/修改状态或迁移）→ 输出 modify
3. 用户提出疑问/需要解释/不明白某处 → 输出 clarify

只输出一个小写单词：confirm 或 modify 或 clarify。不要任何解释。
"""

SD_MODIFICATION_PROMPT = """你是状态图需求修改助手。
根据用户的修改指令更新需求草案，输出修改后的完整 JSON（不要 Markdown，不要解释）。

当前草案：
{draft}

用户修改指令：
{instruction}

输出与原草案格式相同：
{{
  "title": "...",
  "description": "...",
  "candidate_states": [{{"name": "...", "type": "...", "description": "..."}}],
  "candidate_transitions": ["..."]
}}
"""

SD_CLARIFICATION_PROMPT = """你是状态图需求澄清助手。
用户对草案有疑问，请用 1-2 句中文针对性地回应并追问，帮助收集遗漏信息。

当前草案：
{draft}

用户疑问：
{question}

要求：直接回答，结尾提示用户提供更多说明。不要输出 JSON，不要解释框架。
"""


SD_PREVIEW_EDIT_PROMPT = """你是状态图编辑预览助手。
用户想对已生成的状态图进行修改，请用简洁的中文（2-4句）描述你将要做的变更，不要直接执行修改。

当前状态图：
{current_diagram}

用户修改指令：
{instruction}

要求：描述变更内容（增删哪些节点/迁移），以及变更对整体流程的影响。结尾询问用户是否确认此修改。
"""


SD_EDIT_PROMPT = """你是状态图编辑助手。
根据用户的修改指令更新状态图，输出修改后的完整状态图 JSON（不要 Markdown，不要解释）。

当前状态图：
{current_diagram}

用户修改指令：
{instruction}

节点类型：initial/final/simple/sequential_composite/concurrent_composite/fork/join
转移事件类型：call_event/change_event/time_event/signal_event

输出与原状态图格式相同：
{{
  "version": "1.0",
  "title": "...",
  "nodes": [...],
  "transitions": [...]
}}

保持所有未涉及的节点和迁移不变，仅修改用户指定的部分。
"""


# ── normalize ─────────────────────────────────────────────────────────────────

def normalize_state_diagram(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        payload = {}

    diagram: dict[str, Any] = {
        "version": str(payload.get("version") or "1.0"),
        "title": str(payload.get("title") or "未命名状态图"),
        "nodes": [],
        "transitions": [],
    }

    seen_node_ids: set[str] = set()
    for idx, node in enumerate(payload.get("nodes") or [], start=1):
        if not isinstance(node, dict):
            continue
        node_id = str(node.get("id") or f"s{idx}")
        while node_id in seen_node_ids:
            node_id = f"{node_id}_{idx}"
        seen_node_ids.add(node_id)

        node_type = str(node.get("type") or "simple").lower()
        if node_type not in VALID_NODE_TYPES:
            node_type = "simple"

        item: dict[str, Any] = {
            "id": node_id,
            "type": node_type,
            "label": str(node.get("label") or node_id),
            "entry_actions": [str(a) for a in (node.get("entry_actions") or []) if a],
            "do_actions": [str(a) for a in (node.get("do_actions") or []) if a],
            "exit_actions": [str(a) for a in (node.get("exit_actions") or []) if a],
        }

        if node_type in ("sequential_composite", "concurrent_composite"):
            raw_sub = node.get("sub_diagram")
            item["sub_diagram"] = normalize_state_diagram(raw_sub) if isinstance(raw_sub, dict) and raw_sub else None
        else:
            item["sub_diagram"] = None

        diagram["nodes"].append(item)

    seen_trans_ids: set[str] = set()
    for idx, trans in enumerate(payload.get("transitions") or [], start=1):
        if not isinstance(trans, dict):
            continue
        trans_id = str(trans.get("id") or f"t{idx}")
        while trans_id in seen_trans_ids:
            trans_id = f"{trans_id}_{idx}"
        seen_trans_ids.add(trans_id)

        item = {
            "id": trans_id,
            "source": str(trans.get("source") or ""),
            "target": str(trans.get("target") or ""),
            "event": None,
        }

        raw_event = trans.get("event")
        if isinstance(raw_event, dict) and raw_event:
            event_type = str(raw_event.get("event_type") or "").lower()
            if event_type not in VALID_EVENT_TYPES:
                event_type = "signal_event"
            item["event"] = {
                "event_id": str(raw_event.get("event_id") or f"ev{idx}"),
                "event_type": event_type,
                "condition": raw_event.get("condition") or None,
            }

        diagram["transitions"].append(item)

    return diagram


# ── validate ──────────────────────────────────────────────────────────────────

def validate_state_diagram(diagram: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    nodes = diagram.get("nodes") or []
    transitions = diagram.get("transitions") or []

    if not nodes:
        errors.append("状态图至少需要一个节点。")
        return errors

    node_map = {n["id"]: n for n in nodes if isinstance(n, dict) and n.get("id")}

    initial_count = sum(1 for n in nodes if n.get("type") == "initial")
    final_count = sum(1 for n in nodes if n.get("type") == "final")
    if initial_count != 1:
        errors.append(f"状态图必须恰好有 1 个初始节点，当前 {initial_count} 个。")
    if final_count < 1:
        errors.append("状态图至少需要 1 个终止节点。")

    incoming: dict[str, list] = {n_id: [] for n_id in node_map}
    outgoing: dict[str, list] = {n_id: [] for n_id in node_map}
    for t in transitions:
        src, tgt = t.get("source"), t.get("target")
        if src not in node_map:
            errors.append(f"转移 {t.get('id')} 源节点不存在：{src}")
            continue
        if tgt not in node_map:
            errors.append(f"转移 {t.get('id')} 目标节点不存在：{tgt}")
            continue
        outgoing[src].append(t)
        incoming[tgt].append(t)

    for node in nodes:
        n_id = node["id"]
        n_type = node.get("type")

        if n_type == "fork":
            if len(incoming.get(n_id, [])) != 1:
                errors.append(f"fork 节点 {n_id} 必须恰好 1 条入边，当前 {len(incoming.get(n_id, []))} 条。")
            if len(outgoing.get(n_id, [])) < 2:
                errors.append(f"fork 节点 {n_id} 必须至少 2 条出边，当前 {len(outgoing.get(n_id, []))} 条。")
        elif n_type == "join":
            if len(incoming.get(n_id, [])) < 2:
                errors.append(f"join 节点 {n_id} 必须至少 2 条入边，当前 {len(incoming.get(n_id, []))} 条。")
            if len(outgoing.get(n_id, [])) != 1:
                errors.append(f"join 节点 {n_id} 必须恰好 1 条出边，当前 {len(outgoing.get(n_id, []))} 条。")

        if n_type in ("sequential_composite", "concurrent_composite"):
            sub = node.get("sub_diagram")
            if isinstance(sub, dict) and sub.get("nodes"):
                sub_node_types = {sn.get("type") for sn in sub.get("nodes", [])}
                if n_type == "sequential_composite":
                    if "fork" in sub_node_types or "join" in sub_node_types:
                        errors.append(f"顺序复合状态 {n_id} 的子图不能包含 fork/join 节点。")
                elif n_type == "concurrent_composite":
                    if "fork" not in sub_node_types:
                        errors.append(f"并发复合状态 {n_id} 的子图必须包含 fork 节点。")
                    if "join" not in sub_node_types:
                        errors.append(f"并发复合状态 {n_id} 的子图必须包含 join 节点。")
                sub_errors = validate_state_diagram(sub)
                errors.extend(f"子图[{n_id}]:{e}" for e in sub_errors)

    return errors


# ── formatting helper ─────────────────────────────────────────────────────────

def format_requirements_as_text(reqs: dict[str, Any]) -> str:
    title = reqs.get("title") or "状态图"
    desc = reqs.get("description") or ""
    states = reqs.get("candidate_states") or []
    transitions = reqs.get("candidate_transitions") or []

    type_labels = {
        "simple": "简单状态",
        "sequential_composite": "顺序复合状态",
        "concurrent_composite": "并发复合状态",
    }

    lines = [f"【{title}】{(' — ' + desc) if desc else ''}", "", "候选状态："]
    for s in states:
        t = type_labels.get(s.get("type", "simple"), "状态")
        d = s.get("description", "")
        lines.append(f"  • {s.get('name', '')}（{t}）{(' — ' + d) if d else ''}")

    if transitions:
        lines += ["", "候选迁移："]
        for tr in transitions:
            lines.append(f"  • {tr}")

    return "\n".join(lines)
