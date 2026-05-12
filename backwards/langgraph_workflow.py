from __future__ import annotations

import json
from pathlib import Path
from typing import Any, TypedDict

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from backwards.state_graph import format_requirements_as_text
from backwards.flow_graph import (
    build_fallback_graph,
    detect_rewire_intent,
    edge_signature,
    local_repair_graph,
    validate_graph,
)
from backwards.path_pipeline import run_full_path_pipeline
import os

_LOG_PATH = os.path.join(os.path.dirname(__file__), "./graph_debug.txt")

def _log(msg: str) -> None:
    with open(_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(msg + "\n")

class WorkflowState(TypedDict, total=False):
    message: str
    thread_id: str
    request_id: int
    conversation_history: list[dict[str, Any]]
    action_history: list[dict[str, Any]]
    current_graph: dict | None
    has_current_graph: bool
    intent: str
    intent_raw: str
    is_graph_request: bool
    is_graph_creation_request: bool
    is_graph_edit_request: bool
    is_path_generation_request: bool
    confirm_intent: bool
    classifier_raw: str
    is_undo_request: bool
    is_rewire_request: bool
    graph: dict
    errors: list[str]
    raw_response: str
    repair_rounds: int
    repair_strategy: str
    model_round: int
    repair_halted: bool
    result: dict
    terminal: bool
    preview_instruction: str
    graph_type: str  # "cfg" or "state_diagram"
    sd_requirements: dict
    sd_result: dict
    sd_feedback_type: str    # "confirm" | "modify" | "clarify"
    sd_feedback_message: str


def build_graph_workflow(manager, checkpointer=None):
    debug_summary_path = Path(__file__).resolve().parent.parent / "graph_debug_summary.txt"

    def write_debug_summary(label: str, graph: dict | None) -> None:
        debug_summary_path.parent.mkdir(parents=True, exist_ok=True)
        if not isinstance(graph, dict):
            with debug_summary_path.open("a", encoding="utf-8") as f:
                f.write(f"[graph-debug-summary] {label}: <none>\n")
            return

        nodes = graph.get("nodes") or []
        edges = graph.get("edges") or []
        node_labels = [str(node.get("label") or node.get("id") or "") for node in nodes[:6] if isinstance(node, dict)]
        edge_pairs = [
            f"{edge.get('source')}->{edge.get('target')}:{edge.get('label', '')}"
            for edge in edges[:6]
            if isinstance(edge, dict)
        ]
        fingerprint = hash(
            json.dumps(
                {
                    "nodes": graph.get("nodes", []),
                    "edges": graph.get("edges", []),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        with debug_summary_path.open("a", encoding="utf-8") as f:
            f.write(
                f"[graph-debug-summary] {label}: "
                f"nodes={len(nodes)} edges={len(edges)} "
                f"labels={node_labels} edge_sample={edge_pairs} "
                f"fingerprint={fingerprint}\n"
            )

    def append_debug_line(text: str) -> None:
        debug_summary_path.parent.mkdir(parents=True, exist_ok=True)
        with debug_summary_path.open("a", encoding="utf-8") as f:
            f.write(text.rstrip("\n") + "\n")

    def emit(event: dict[str, Any]) -> None:
        get_stream_writer()(event)

    def append_conversation_entry(
        state: WorkflowState,
        role: str,
        content: str,
        *,
        kind: str = "message",
    ) -> list[dict[str, Any]]:
        history = list(state.get("conversation_history") or [])
        text = (content or "").strip()
        if text:
            history.append({"role": role, "content": text, "kind": kind})
        return history

    def append_action_entry(
        state: WorkflowState,
        action_type: str,
        summary: str,
        **extra: Any,
    ) -> list[dict[str, Any]]:
        history = list(state.get("action_history") or [])
        entry: dict[str, Any] = {"type": action_type, "summary": summary}
        entry.update({key: value for key, value in extra.items() if value not in (None, "", [], {})})
        history.append(entry)
        return history

    def summarize_graph(graph: dict | None) -> str:
        if not isinstance(graph, dict):
            return "无图"
        title = str(graph.get("title") or "未命名流程")
        nodes = len(graph.get("nodes") or [])
        edges = len(graph.get("edges") or [])
        return f"{title}（{nodes} 节点，{edges} 边）"

    def is_request_stale(state: WorkflowState) -> bool:
        return not manager.is_request_active(
            str(state.get("thread_id") or ""),
            int(state.get("request_id") or 0),
        )

    def build_cancelled_result(state: WorkflowState, content: str) -> WorkflowState:
        emit({"type": "status", "content": content})
        emit({"type": "done"})
        return {"terminal": True}

    def detect_request(state: WorkflowState) -> WorkflowState:
        has_current_graph = isinstance(state.get("current_graph"), dict) and bool(
            (state.get("current_graph") or {}).get("nodes")
        )
        intent, intent_raw = manager.classify_request_intent(state["message"], has_current_graph)
        is_graph_creation_request = intent == "graph_create"
        is_graph_edit_request = intent == "graph_edit"
        is_path_generation_request = intent == "path_generate"
        is_undo_request = intent == "undo"
        is_graph_request = is_graph_creation_request or is_graph_edit_request or is_path_generation_request or is_undo_request

        if is_graph_request:
            emit({"type": "mode", "mode": "graph"})
            emit({"type": "activity", "label": "Skill", "content": "nl-to-control-flow"})
        else:
            emit({"type": "mode", "mode": "chat"})
        emit({"type": "activity", "label": "Intent", "content": intent})

        return {
        "thread_id": state.get("thread_id", ""),
        "request_id": state.get("request_id", 0),
        "conversation_history": append_conversation_entry(state, "user", state.get("message", "")),
        "action_history": list(state.get("action_history") or []),
        "has_current_graph": has_current_graph,
        "intent": intent,
        "intent_raw": intent_raw,
        "is_graph_request": is_graph_request,
        "is_graph_creation_request": is_graph_creation_request,
        "is_graph_edit_request": is_graph_edit_request,
        "is_path_generation_request": is_path_generation_request,
        "is_undo_request": is_undo_request,
        "current_graph": state.get("current_graph"),  # ✅ 显式保留，防止 resume 时丢失
        "preview_instruction": None,  # 每次新请求清除，避免旧指令污染 prepare_preview
        }

    def route_after_detect(state: WorkflowState) -> str:
        if state.get("is_undo_request"):
            return "execute_undo"
        return "graph_prepare" if state.get("is_graph_request") else "chat_reply"

    def chat_reply(state: WorkflowState) -> WorkflowState:
        emit({"type": "activity", "label": "Action", "content": "普通对话"})
        emit({"type": "activity", "label": "Tool", "content": "LLM 流式回复"})
        emit(
            {
                "type": "tool_stream",
                "phase": "start",
                "name": "chat_reply",
                "content": "正在生成回复",
            }
        )
        full_text = ""
        for chunk in manager.stream_chat(state["message"], state.get("conversation_history")):
            full_text += chunk
            emit({"type": "delta", "content": chunk})
        emit({"type": "tool_stream", "phase": "end", "name": "chat_reply"})
        emit({"type": "done"})
        return {
            "conversation_history": append_conversation_entry(state, "assistant", full_text),
            "action_history": append_action_entry(state, "chat", "完成了一次普通对话回复"),
        }

    def execute_undo(state: WorkflowState) -> WorkflowState:
        emit({"type": "activity", "label": "Action", "content": "撤回上一次修改"})
        thread_id = str(state.get("thread_id") or "default")
        prev_graph = manager.get_previous_graph(thread_id, state.get("current_graph"))
        if prev_graph is None:
            emit({"type": "delta", "content": "没有可以撤回的历史版本。"})
            emit({"type": "done"})
            return {
                "conversation_history": append_conversation_entry(state, "assistant", "没有可以撤回的历史版本。"),
            }
        result = {
            "graph": prev_graph,
            "valid": True,
            "errors": [],
            "raw_response": "",
            "repair_rounds": 0,
            "repair_strategy": "undo",
        }
        emit({"type": "delta", "content": "已撤回到上一个版本的控制流图。"})
        emit({"type": "graph_result", "payload": result})
        emit({"type": "done"})
        return {
            "current_graph": prev_graph,
            "result": result,
            "conversation_history": append_conversation_entry(state, "assistant", "已撤回到上一个版本的控制流图。", kind="status"),
            "action_history": append_action_entry(state, "undo", "撤回到上一个版本的图"),
        }

    def graph_prepare(state: WorkflowState) -> WorkflowState:
        return {}

    def route_after_graph_prepare(state: WorkflowState) -> str:
        if state.get("is_path_generation_request"):
            return "execute_path_generation"
        if state.get("is_graph_creation_request"):
            return "determine_graph_type"
        if state.get("is_graph_edit_request") and state.get("has_current_graph"):
            if state.get("graph_type") == "state_diagram":
                return "sd_prepare_preview"
            return "prepare_preview"
        if not state.get("has_current_graph"):
            return "determine_graph_type"
        return "chat_reply"

    def determine_graph_type(state: WorkflowState) -> WorkflowState:
        _log("[determine_graph_type] classifying graph type")
        emit({"type": "activity", "label": "Action", "content": "判断图类型"})
        graph_type = manager.classify_graph_type(state["message"])
        _log(f"[determine_graph_type] graph_type={graph_type}")
        emit({"type": "activity", "label": "Intent", "content": f"图类型：{graph_type}"})
        return {"graph_type": graph_type}

    def route_after_determine_graph_type(state: WorkflowState) -> str:
        return "generate_initial" if state.get("graph_type", "cfg") == "cfg" else "sd_parse_requirements"

    def sd_parse_requirements(state: WorkflowState) -> WorkflowState:
        _log("[sd_parse_requirements] parsing state diagram requirements")
        emit({"type": "activity", "label": "Action", "content": "解析状态图需求"})
        emit({"type": "activity", "label": "Tool", "content": "LLM 需求提取"})
        emit({"type": "tool_stream", "phase": "start", "name": "sd_parse_requirements", "content": "正在解析状态图需求"})
        reqs = manager.parse_state_requirements(state["message"])
        emit({"type": "tool_stream", "phase": "end", "name": "sd_parse_requirements"})
        _log(f"[sd_parse_requirements] title={reqs.get('title')} states={len(reqs.get('candidate_states') or [])}")
        return {"sd_requirements": reqs}

    def sd_present_draft(state: WorkflowState) -> WorkflowState:
        _log("[sd_present_draft] presenting requirements draft")
        reqs = state.get("sd_requirements") or {}
        draft_text = format_requirements_as_text(reqs)
        emit({"type": "activity", "label": "Action", "content": "展示状态图草案"})
        emit({"type": "status", "content": "已解析需求，草案如下："})
        emit({"type": "delta", "content": draft_text})
        return {}

    def sd_interrupt_negotiate(state: WorkflowState) -> WorkflowState:
        _log("[sd_interrupt_negotiate] waiting for user feedback on draft")
        emit({"type": "activity", "label": "Action", "content": "等待用户确认状态图草案"})
        emit({"type": "status", "content": "草案已展示，请确认或提出修改意见。"})
        emit({"type": "done"})
        resume_payload = interrupt({
            "kind": "sd_negotiate",
            "requirements": state.get("sd_requirements", {}),
        })
        resumed_message = manager.extract_resume_message(resume_payload)
        feedback_type, classifier_raw = manager.classify_sd_feedback(
            resumed_message,
            state.get("sd_requirements", {}),
        )
        _log(f"[sd_interrupt_negotiate] feedback_type={feedback_type} msg={resumed_message[:60]}")
        # Do NOT overwrite "message" — it holds the original description needed by sd_generate.
        # Feedback text goes into sd_feedback_message only.
        return {
            "request_id": manager.extract_resume_request_id(resume_payload) or state.get("request_id", 0),
            "sd_feedback_type": feedback_type,
            "sd_feedback_message": resumed_message,
            "conversation_history": append_conversation_entry(state, "user", resumed_message, kind="resume"),
        }

    def route_after_sd_negotiate(state: WorkflowState) -> str:
        ft = state.get("sd_feedback_type", "confirm")
        if ft == "modify":
            return "sd_apply_modification"
        if ft == "clarify":
            return "sd_clarify"
        return "sd_generate"

    def sd_apply_modification(state: WorkflowState) -> WorkflowState:
        _log("[sd_apply_modification] applying user modification to requirements")
        emit({"type": "activity", "label": "Action", "content": "应用用户修改意见"})
        emit({"type": "activity", "label": "Tool", "content": "LLM 需求更新"})
        emit({"type": "tool_stream", "phase": "start", "name": "sd_apply_modification", "content": "正在更新草案"})
        updated = manager.apply_sd_modification(
            state.get("sd_requirements", {}),
            state.get("sd_feedback_message", ""),
        )
        emit({"type": "tool_stream", "phase": "end", "name": "sd_apply_modification"})
        _log(f"[sd_apply_modification] states={len((updated.get('candidate_states') or []))}")
        return {"sd_requirements": updated}

    def sd_clarify(state: WorkflowState) -> WorkflowState:
        _log("[sd_clarify] generating clarifying question")
        emit({"type": "activity", "label": "Action", "content": "追问澄清"})
        question = manager.generate_sd_clarification(
            state.get("sd_requirements", {}),
            state.get("sd_feedback_message", ""),
        )
        emit({"type": "delta", "content": question})
        emit({"type": "done"})
        resume_payload = interrupt({"kind": "sd_clarify", "question": question})
        user_answer = manager.extract_resume_message(resume_payload)
        _log(f"[sd_clarify] got answer: {user_answer[:60]}")
        combined = state.get("message", "") + "\n补充说明：" + user_answer
        return {
            "message": combined,
            "request_id": manager.extract_resume_request_id(resume_payload) or state.get("request_id", 0),
            "conversation_history": append_conversation_entry(state, "user", user_answer, kind="resume"),
        }

    def sd_generate(state: WorkflowState) -> WorkflowState:
        if is_request_stale(state):
            return build_cancelled_result(state, "状态图生成已被新请求接管。")
        _log("[sd_generate] generating state diagram with recursive sub-diagrams")
        emit({"type": "activity", "label": "Action", "content": "生成状态图"})
        emit({"type": "activity", "label": "Tool", "content": "LLM 结构化生成（含子图递归，最多3层）"})
        emit({"type": "tool_stream", "phase": "start", "name": "sd_generate", "content": "正在生成状态图草稿"})
        try:
            requirements = state.get("sd_requirements") or {}
            diagram, errors, raw = manager.generate_state_diagram(state["message"], requirements)
            emit({"type": "tool_stream", "phase": "end", "name": "sd_generate"})
            _log(f"[sd_generate] nodes={len(diagram.get('nodes') or [])} errors={len(errors)}")
            return {
                "sd_result": diagram,
                "errors": errors,
                "raw_response": raw,
            }
        except Exception as exc:
            emit({"type": "tool_stream", "phase": "end", "name": "sd_generate"})
            _log(f"[sd_generate] exception: {exc}")
            emit({"type": "delta", "content": f"状态图生成失败：{exc}"})
            emit({"type": "done"})
            return {"terminal": True}

    def sd_finalize(state: WorkflowState) -> WorkflowState:
        _log("[sd_finalize] finalizing state diagram result")
        diagram = state.get("sd_result") or {}
        errors = state.get("errors") or []
        valid = len(errors) == 0
        emit({"type": "status", "content": f"状态图生成完成，{'已通过校验' if valid else f'存在 {len(errors)} 个校验问题'}。"})
        emit({"type": "delta", "content": "状态图草稿已生成并通过校验。" if valid else "状态图草稿已生成，但存在待处理问题。"})
        emit({"type": "state_diagram_result", "payload": {"diagram": diagram, "errors": errors, "valid": valid}})
        emit({"type": "done"})
        return {
            "sd_result": diagram,
            "current_graph": diagram,
            "graph_type": "state_diagram",
            "result": {"graph": diagram, "valid": valid, "errors": errors},
            "conversation_history": append_conversation_entry(
                state, "assistant",
                "状态图草稿已生成。" if valid else "状态图草稿已生成，存在待处理问题。",
                kind="status",
            ),
            "action_history": append_action_entry(
                state, "state_diagram_result",
                f"生成状态图：{diagram.get('title', '未命名')}",
                valid=valid,
                node_count=len(diagram.get("nodes") or []),
            ),
        }

    def execute_path_generation(state: WorkflowState) -> WorkflowState:
        if is_request_stale(state):
            return build_cancelled_result(state, "路径生成任务已被新的请求接管。")
        graph = state.get("current_graph") or {}
        errors = validate_graph(graph)

        emit({"type": "activity", "label": "Action", "content": "生成路径"})
        emit(
            {
                "type": "activity",
                "label": "Tool",
                "content": "control2control.exe -> activity_to_state.exe -> path_generation.exe -> path_convert.exe -> trans.exe",
            }
        )

        if errors:
            emit({"type": "delta", "content": f"当前图未通过校验，不能生成路径：{'；'.join(errors)}"})
            emit({"type": "done"})
            return {}

        emit(
            {
                "type": "tool_stream",
                "phase": "start",
                "name": "path_pipeline",
                "content": "正在导出 .control/XML 并执行完整路径生成流水线",
            }
        )
        try:
            result = run_full_path_pipeline(graph)
            emit({"type": "tool_stream", "phase": "end", "name": "path_pipeline"})
            emit(
                {
                    "type": "status",
                    "content": (
                        f"已生成结果文件：{result['result']['path']}，"
                        f"control2control={result['steps']['control2control']['returncode']}，"
                        f"activity_to_state={result['steps']['activity_to_state']['returncode']}，"
                        f"path_generation={result['steps']['path_generation']['returncode']}，"
                        f"path_convert={result['steps']['path_convert']['returncode']}，"
                        f"trans={result['steps']['trans']['returncode']}"
                    ),
                }
            )
            emit(
                {
                    "type": "path_result",
                    "payload": {
                        "path": result["result"]["path"],
                        "rows": result["result"]["rows"],
                    },
                }
            )
            if not result["result"]["rows"]:
                emit({"type": "delta", "content": "路径流水线已执行完成，但结果表为空。"})
            else:
                emit({"type": "delta", "content": "路径生成完成，结果已整理为表格。"})
            return {
                "conversation_history": append_conversation_entry(state, "assistant", "路径生成完成，结果已整理为表格。", kind="status"),
                "action_history": append_action_entry(
                    state,
                    "path_generate",
                    f"生成了路径结果文件：{result['result']['path']}",
                    result_path=result["result"]["path"],
                    row_count=len(result["result"]["rows"]),
                ),
            }
        except TimeoutError:
            emit({"type": "tool_stream", "phase": "end", "name": "path_pipeline"})
            emit({"type": "delta", "content": "路径生成流水线执行超时。"})
        except FileNotFoundError as exc:
            emit({"type": "tool_stream", "phase": "end", "name": "path_pipeline"})
            emit({"type": "delta", "content": str(exc)})
        except OSError as exc:
            emit({"type": "tool_stream", "phase": "end", "name": "path_pipeline"})
            emit({"type": "delta", "content": f"路径生成流水线启动失败：{exc}"})

        emit({"type": "done"})
        return {}

    def prepare_preview(state: WorkflowState) -> WorkflowState:
        # 从 await_confirmation 回来时，message 已被覆盖为用户的确认/否认文本，
        # 应优先使用上一轮保存的 preview_instruction 作为真正的修改指令。
        instruction = state.get("preview_instruction") or state["message"]
        is_rewire_request = detect_rewire_intent(instruction)
        emit({"type": "activity", "label": "Action", "content": "理解修改意图"})
        emit({"type": "activity", "label": "Tool", "content": "LLM 修改理解确认"})
        emit(
            {
                "type": "tool_stream",
                "phase": "start",
                "name": "preview_graph_edit",
                "content": "正在整理这次修改的理解",
            }
        )
        emit(
            {
                "type": "status",
                "content": "检测到已有图草稿，我先复述这次修改意图，等你确认后再真正改图。",
            }
        )
        preview = manager.preview_graph_edit(
            instruction,
            state["current_graph"],
            rewire=is_rewire_request,
        )
        emit({"type": "tool_stream", "phase": "end", "name": "preview_graph_edit"})
        emit({"type": "delta", "content": preview})
        emit(
            {
                "type": "graph_edit_preview",
                "payload": {
                    "instruction": instruction,
                    "preview": preview,
                    "rewire": is_rewire_request,
                },
            }
        )
        emit({"type": "done"})
        return {
            "preview_instruction": instruction,
            "is_rewire_request": is_rewire_request,
            "conversation_history": append_conversation_entry(state, "assistant", preview, kind="preview"),
        }

    def await_confirmation(state: WorkflowState) -> WorkflowState:
        resume_payload = interrupt(
            {
                "kind": "confirm_edit",
                "instruction": state.get("preview_instruction", ""),
                "rewire": state.get("is_rewire_request", False),
            }
        )
        resumed_message = manager.extract_resume_message(resume_payload)
        confirm_intent, classifier_raw = manager.classify_confirm_intent(
            resumed_message,
            state.get("preview_instruction", ""),
        )
        if confirm_intent:
            return {
                "message": resumed_message,
                "request_id": manager.extract_resume_request_id(resume_payload) or state.get("request_id", 0),
                "conversation_history": append_conversation_entry(state, "user", resumed_message, kind="resume"),
                "confirm_intent": True,
                "classifier_raw": classifier_raw,
                "is_rewire_request": state.get("is_rewire_request", False),
            }
        return {
            "message": resumed_message,
            "request_id": manager.extract_resume_request_id(resume_payload) or state.get("request_id", 0),
            "conversation_history": append_conversation_entry(state, "user", resumed_message, kind="resume"),
            "confirm_intent": False,
            "classifier_raw": classifier_raw,
        }

    def route_after_preview(state: WorkflowState) -> str:
        return "await_confirmation"

    def route_after_confirmation(state: WorkflowState) -> str:
        if state.get("confirm_intent"):
            return "execute_sd_edit" if state.get("graph_type") == "state_diagram" else "execute_edit"
        return "sd_prepare_preview" if state.get("graph_type") == "state_diagram" else "prepare_preview"

    def sd_prepare_preview(state: WorkflowState) -> WorkflowState:
        instruction = state.get("preview_instruction") or state["message"]
        _log(f"[sd_prepare_preview] instruction='{instruction[:60]}'")
        emit({"type": "activity", "label": "Action", "content": "理解状态图修改意图"})
        emit({"type": "activity", "label": "Tool", "content": "LLM 修改理解确认"})
        emit({"type": "tool_stream", "phase": "start", "name": "preview_sd_edit", "content": "正在整理状态图修改意图"})
        emit({"type": "status", "content": "检测到已有状态图，我先复述这次修改意图，等你确认后再真正修改。"})
        preview = manager.preview_sd_edit(instruction, state.get("current_graph") or {})
        emit({"type": "tool_stream", "phase": "end", "name": "preview_sd_edit"})
        emit({"type": "delta", "content": preview})
        emit({"type": "graph_edit_preview", "payload": {"instruction": instruction, "preview": preview}})
        emit({"type": "done"})
        return {
            "preview_instruction": instruction,
            "conversation_history": append_conversation_entry(state, "assistant", preview, kind="preview"),
        }

    def execute_sd_edit(state: WorkflowState) -> WorkflowState:
        if is_request_stale(state):
            return build_cancelled_result(state, "状态图修改任务已被新的请求接管。")
        instruction = state.get("preview_instruction", "")
        _log(f"[execute_sd_edit] instruction='{instruction[:60]}'")
        emit({"type": "activity", "label": "Action", "content": "执行已确认的状态图修改"})
        emit({"type": "activity", "label": "Tool", "content": "LLM 状态图修改"})
        emit({"type": "status", "content": "正在按照确认的指令修改状态图。"})
        emit({"type": "tool_stream", "phase": "start", "name": "edit_state_diagram", "content": "正在修改状态图"})
        diagram, errors, raw = manager.edit_state_diagram(instruction, state.get("current_graph") or {})
        emit({"type": "tool_stream", "phase": "end", "name": "edit_state_diagram"})
        _log(f"[execute_sd_edit] nodes={len(diagram.get('nodes') or [])} errors={len(errors)}")
        return {
            "sd_result": diagram,
            "errors": errors,
            "raw_response": raw,
            "action_history": append_action_entry(
                state, "sd_edit",
                f"修改状态图：{instruction[:80]}",
                instruction=instruction,
            ),
        }

    def generate_initial(state: WorkflowState) -> WorkflowState:
        if is_request_stale(state):
            return build_cancelled_result(state, "控制流图生成已被新的请求接管。")
        emit({"type": "activity", "label": "Action", "content": "生成新草稿"})
        emit({"type": "activity", "label": "Tool", "content": "LLM 结构化生成"})
        emit({"type": "status", "content": "已识别为控制流图生成请求，开始生成图草稿。"})
        emit(
            {
                "type": "tool_stream",
                "phase": "start",
                "name": "draft_graph",
                "content": "正在生成控制流图草稿",
            }
        )
        try:
            graph, errors, raw_response = manager.generate_graph(state["message"])
            emit({"type": "tool_stream", "phase": "end", "name": "draft_graph"})
            return {
                "graph": graph,
                "errors": errors,
                "raw_response": raw_response,
                "repair_rounds": 0,
                "repair_strategy": "model",
                "repair_halted": False,
                "action_history": append_action_entry(
                    state,
                    "graph_create_started",
                    f"开始生成控制流图：{state.get('message', '')[:80]}",
                ),
            }
        except Exception as exc:
            emit({"type": "tool_stream", "phase": "end", "name": "draft_graph"})
            fallback = build_fallback_graph(state["message"], str(exc))
            fallback_errors = validate_graph(fallback)
            result = {
                "graph": fallback,
                "valid": len(fallback_errors) == 0,
                "errors": fallback_errors,
                "raw_response": str(exc),
                "repair_rounds": 0,
                "repair_strategy": "fallback",
            }
            emit({"type": "status", "content": f"模型生成失败，异常信息：{exc}"})
            emit({"type": "delta", "content": "模型生成失败，已回退到占位图。"})
            emit({"type": "graph_result", "payload": result})
            emit({"type": "done"})
            return {
                "result": result,
                "terminal": True,
            }

    def execute_edit(state: WorkflowState) -> WorkflowState:
        if is_request_stale(state):
            return build_cancelled_result(state, "图修改任务已被新的请求接管。")
        instruction = state.get("preview_instruction", "")
        is_rewire_request = detect_rewire_intent(instruction)
        append_debug_line(
            f"[graph-debug-summary] execute_edit PREVIEW: "
            f"preview_instruction='{state.get('preview_instruction', '')[:50]}'"
        )
        append_debug_line(
            f"[graph-debug-summary] execute_edit CALLED: "
            f"message='{state.get('message', '')[:50]}' "
            f"input_nodes={len(state.get('graph', {}).get('nodes', []))} "
            f"errors={state.get('errors')}"
        )
        emit({"type": "activity", "label": "Action", "content": "执行已确认修改"})
        emit(
            {
                "type": "activity",
                "label": "Tool",
                "content": "LLM 重连边" if is_rewire_request else "LLM 结构化改图",
            }
        )
        emit({"type": "status", "content": "我已按刚才确认过的理解开始修改当前草稿。"})
        emit(
            {
                "type": "tool_stream",
                "phase": "start",
                "name": "edit_graph",
                "content": "正在修改当前图草稿",
            }
        )
        graph, errors, raw_response = manager.edit_graph(
            instruction,
            state["current_graph"],
            rewire=is_rewire_request,
        )
        write_debug_summary("execute_edit.output_graph", graph)
        if is_rewire_request and edge_signature(graph) == edge_signature(state["current_graph"]):
            emit(
                {
                    "type": "status",
                    "content": "检测到本次重连请求还没有真正改动边，正在按更强约束重试一次。",
                }
            )
            graph, errors, raw_response = manager.edit_graph(
                (
                    f"{instruction}\n\n"
                    "注意：这次必须修改 edges，不能只改节点说明、顺序文本或视觉位置。"
                ),
                state["current_graph"],
                rewire=True,
            )
            write_debug_summary("execute_edit.output_graph", graph)
            is_rewire_request = True
        emit({"type": "tool_stream", "phase": "end", "name": "edit_graph"})
        return {
        "graph": graph,
        "errors": errors,
        "raw_response": raw_response,
        "repair_rounds": 0,
        "repair_strategy": "model",
        "repair_halted": False,
        "is_rewire_request": is_rewire_request,
        "message": instruction,  # ✅ 覆盖掉"确认修改"，用真正的修改指令做 repair context
        "action_history": append_action_entry(
            state,
            "graph_edit",
            f"执行图修改：{instruction[:80]}",
            instruction=instruction,
            rewire=is_rewire_request,
        ),
    }

    def route_after_generate_or_edit(state: WorkflowState) -> str:
        _log(f"[route] terminal={state.get('terminal')} strategy={state.get('repair_strategy')} preview_instruction='{(state.get('preview_instruction') or '')[:30]}' errors={state.get('errors')}")
        if state.get("terminal"):
            return "finish"
        return "local_repair" if state.get("errors") else "finalize"

    def local_repair(state: WorkflowState) -> WorkflowState:
        if is_request_stale(state):
            return build_cancelled_result(state, "后续修复已停止，新的请求已接管当前线程。")
        append_debug_line(
            f"[graph-debug-summary] local_repair CALLED: "
            f"input_nodes={len(state.get('graph', {}).get('nodes', []))} "
            f"errors={state.get('errors')}"
        )
        emit({"type": "activity", "label": "Tool", "content": "本地规则校验与修复"})
        emit(
            {
                "type": "status",
                "content": f"首次校验发现 {len(state.get('errors') or [])} 个问题，先尝试本地规则修复。",
            }
        )
        graph = local_repair_graph(state["graph"])
        append_debug_line(
            f"[graph-debug-summary] local_repair DONE: "
            f"output_nodes={len(graph.get('nodes', []))}"
        )
        write_debug_summary("local_repair.graph", graph)
        errors = validate_graph(graph)
        original_node_ids = {n["id"] for n in state["graph"].get("nodes", [])}
        repaired_node_ids = {n["id"] for n in graph.get("nodes", [])}
        if original_node_ids != repaired_node_ids:
            graph = state["graph"]
            errors = validate_graph(graph)
        if errors:
            emit({"type": "status", "content": "本地规则修复后仍有问题，开始调用模型继续修复。"})
        return {
            "graph": graph,
            "errors": errors,
            "repair_rounds": 1,
            "repair_strategy": "local",
            "model_round": 0,
            "repair_halted": False,
        }

    def route_after_local_repair(state: WorkflowState) -> str:
        return "model_repair" if state.get("errors") else "finalize"

    def model_repair(state: WorkflowState) -> WorkflowState:
        if is_request_stale(state):
            return build_cancelled_result(state, "模型修复已停止，新的请求已接管当前线程。")
        model_round = int(state.get("model_round", 0)) + 1
        emit({"type": "activity", "label": "Tool", "content": f"LLM 修复第 {model_round} 轮"})
        emit(
            {
                "type": "tool_stream",
                "phase": "start",
                "name": "repair_graph",
                "content": f"正在执行第 {model_round} 轮图结构修复",
            }
        )
        try:
            graph, errors, raw_response = manager.repair_graph(
                state["message"],
                state["graph"],
                state["errors"],
            )
            emit({"type": "tool_stream", "phase": "end", "name": "repair_graph"})
            emit({"type": "status", "content": f"模型修复第 {model_round} 轮已完成。"})
            return {
                "graph": graph,
                "errors": errors,
                "raw_response": raw_response,
                "repair_rounds": int(state.get("repair_rounds", 1)) + 1,
                "repair_strategy": "local+model",
                "model_round": model_round,
                "repair_halted": False,
            }
        except Exception as exc:
            emit({"type": "tool_stream", "phase": "end", "name": "repair_graph"})
            emit({"type": "status", "content": f"模型修复超时或失败，停止自动修复：{exc}"})
            return {
                "model_round": model_round,
                "repair_halted": True,
            }

    def route_after_model_repair(state: WorkflowState) -> str:
        if not state.get("errors"):
            return "finalize"
        if state.get("repair_halted"):
            return "finalize"
        if int(state.get("model_round", 0)) < 2:
            return "model_repair"
        return "finalize"

    def finalize(state: WorkflowState) -> WorkflowState:
        _log(f"[finalize] state_graph_nodes={len((state.get('graph') or {}).get('nodes',[]))} state_result_is_none={state.get('result') is None}")
        result = {
        "graph": state.get("graph"),
        "valid": len(state.get("errors") or []) == 0,
        "errors": state.get("errors") or [],
        "raw_response": state.get("raw_response", ""),
        "repair_rounds": state.get("repair_rounds", 0),
        "repair_strategy": state.get("repair_strategy", "model"),
    }
        write_debug_summary("finalize.result_graph", result.get("graph"))
        if result["valid"]:
            emit({"type": "delta", "content": f"控制流图草稿已生成并通过校验。自动修复轮次：{result['repair_rounds']}。"})
        else:
            emit({"type": "delta", "content": "控制流图草稿已生成，但仍有未修复的结构问题。"})
        emit({"type": "graph_result", "payload": result})
        emit({"type": "done"})
        return {
            "result": result,
            "current_graph": result["graph"],  # ✅ 把最新图写回 state，下轮编辑时才能读到正确版本
            "conversation_history": append_conversation_entry(
                state,
                "assistant",
                "控制流图草稿已生成完成。" if result["valid"] else "控制流图草稿已生成，但仍有待处理问题。",
                kind="status",
            ),
            "action_history": append_action_entry(
                state,
                "graph_result",
                f"得到最新图结果：{summarize_graph(result['graph'])}",
                valid=result["valid"],
                repair_rounds=result["repair_rounds"],
            ),
        }

    graph = StateGraph(WorkflowState)
    graph.add_node("detect_request", detect_request)
    graph.add_node("chat_reply", chat_reply)
    graph.add_node("execute_undo", execute_undo)
    graph.add_node("graph_prepare", graph_prepare)
    graph.add_node("determine_graph_type", determine_graph_type)
    graph.add_node("sd_parse_requirements", sd_parse_requirements)
    graph.add_node("sd_present_draft", sd_present_draft)
    graph.add_node("sd_interrupt_negotiate", sd_interrupt_negotiate)
    graph.add_node("sd_apply_modification", sd_apply_modification)
    graph.add_node("sd_clarify", sd_clarify)
    graph.add_node("sd_generate", sd_generate)
    graph.add_node("sd_finalize", sd_finalize)
    graph.add_node("sd_prepare_preview", sd_prepare_preview)
    graph.add_node("execute_sd_edit", execute_sd_edit)
    graph.add_node("prepare_preview", prepare_preview)
    graph.add_node("await_confirmation", await_confirmation)
    graph.add_node("generate_initial", generate_initial)
    graph.add_node("execute_path_generation", execute_path_generation)
    graph.add_node("execute_edit", execute_edit)
    graph.add_node("local_repair", local_repair)
    graph.add_node("model_repair", model_repair)
    graph.add_node("finalize", finalize)
    graph.add_node("finish", lambda state: {})

    graph.add_edge(START, "detect_request")
    graph.add_conditional_edges(
        "detect_request",
        route_after_detect,
        {
            "graph_prepare": "graph_prepare",
            "chat_reply": "chat_reply",
            "execute_undo": "execute_undo",
        },
    )
    graph.add_conditional_edges(
        "graph_prepare",
        route_after_graph_prepare,
        {
            "chat_reply": "chat_reply",
            "execute_path_generation": "execute_path_generation",
            "determine_graph_type": "determine_graph_type",
            "prepare_preview": "prepare_preview",
            "sd_prepare_preview": "sd_prepare_preview",
        },
    )
    graph.add_conditional_edges(
        "determine_graph_type",
        route_after_determine_graph_type,
        {
            "generate_initial": "generate_initial",
            "sd_parse_requirements": "sd_parse_requirements",
        },
    )
    graph.add_edge("sd_parse_requirements", "sd_present_draft")
    graph.add_edge("sd_present_draft", "sd_interrupt_negotiate")
    graph.add_conditional_edges(
        "sd_interrupt_negotiate",
        route_after_sd_negotiate,
        {
            "sd_generate": "sd_generate",
            "sd_apply_modification": "sd_apply_modification",
            "sd_clarify": "sd_clarify",
        },
    )
    graph.add_edge("sd_apply_modification", "sd_present_draft")
    graph.add_edge("sd_clarify", "sd_parse_requirements")
    graph.add_edge("sd_generate", "sd_finalize")
    graph.add_edge("sd_finalize", END)
    graph.add_conditional_edges(
        "prepare_preview",
        route_after_preview,
        {
            "await_confirmation": "await_confirmation",
        },
    )
    graph.add_conditional_edges(
        "await_confirmation",
        route_after_confirmation,
        {
            "execute_edit": "execute_edit",
            "execute_sd_edit": "execute_sd_edit",
            "prepare_preview": "prepare_preview",
            "sd_prepare_preview": "sd_prepare_preview",
        },
    )
    graph.add_edge("sd_prepare_preview", "await_confirmation")
    graph.add_edge("execute_sd_edit", "sd_finalize")
    graph.add_conditional_edges(
        "generate_initial",
        route_after_generate_or_edit,
        {
            "finish": "finish",
            "local_repair": "local_repair",
            "finalize": "finalize",
        },
    )
    graph.add_conditional_edges(
        "execute_edit",
        route_after_generate_or_edit,
        {
            "finish": "finish",
            "local_repair": "local_repair",
            "finalize": "finalize",
        },
    )
    graph.add_conditional_edges(
        "local_repair",
        route_after_local_repair,
        {
            "model_repair": "model_repair",
            "finalize": "finalize",
        },
    )
    graph.add_conditional_edges(
        "model_repair",
        route_after_model_repair,
        {
            "model_repair": "model_repair",
            "finalize": "finalize",
        },
    )
    graph.add_edge("chat_reply", END)
    graph.add_edge("execute_undo", END)
    graph.add_edge("execute_path_generation", END)
    graph.add_edge("finalize", END)
    graph.add_edge("finish", END)
    return graph.compile(checkpointer=checkpointer)
