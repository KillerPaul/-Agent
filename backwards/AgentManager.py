import json
from collections.abc import Iterator
from pathlib import Path
from typing import Generator

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent / ".env")

from langchain_openai import ChatOpenAI
from langgraph.types import Command

from backwards.langgraph_workflow import build_graph_workflow
from backwards.flow_graph import (
    CONFIRM_CLASSIFY_PROMPT,
    GRAPH_EDIT_CONFIRM_PROMPT,
    GRAPH_EDIT_PROMPT,
    GRAPH_PROMPT,
    GRAPH_REPAIR_PROMPT,
    GRAPH_REWIRE_PROMPT,
    GRAPH_TYPE_CLASSIFY_PROMPT,
    REQUEST_INTENT_CLASSIFY_PROMPT,
    detect_graph_edit_intent,
    detect_state_diagram_intent,
    extract_json_object,
    detect_graph_intent,
    detect_path_generation_intent,
    normalize_graph,
    validate_graph,
)
from backwards.state_graph import (
    SD_CLARIFICATION_PROMPT,
    SD_EDIT_PROMPT,
    SD_FEEDBACK_CLASSIFY_PROMPT,
    SD_MODIFICATION_PROMPT,
    SD_PREVIEW_EDIT_PROMPT,
    STATE_DIAGRAM_PROMPT,
    STATE_REQUIREMENTS_PROMPT,
    STATE_SUB_DIAGRAM_PROMPT,
    normalize_state_diagram,
    validate_state_diagram,
)


class AgentManager:
    def __init__(self, config_path):
        self.config_path = config_path
        self.Chat_model = None
        self.graph_workflow = None
        self.configuration = None
        self._request_counters: dict[str, int] = {}
        self.configure()

    def load_config(self):
        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            print(f"Configuration file not found: {self.config_path}")
        except json.JSONDecodeError:
            print(f"Invalid JSON in configuration file: {self.config_path}")
        return None

    def configure(self):
        self.configuration = self.load_config() or {}
        chat_model_config = self.configuration.get("Chat_model", {})

        self.Chat_model = ChatOpenAI(
            model=chat_model_config.get("model"),
            base_url=chat_model_config.get("base_url"),
            name=chat_model_config.get("name"),
            api_key=chat_model_config.get("api_key"),
            timeout=chat_model_config.get("timeout", 60),
            max_tokens=chat_model_config.get("max_tokens"),
            max_retries=1,
        )
        import os, sqlite3
        from langgraph.checkpoint.sqlite import SqliteSaver
        db_path = os.path.join(os.path.dirname(os.path.abspath(self.config_path)), "workflow_state.db")
        self._db_conn = sqlite3.connect(db_path, check_same_thread=False)
        self._checkpointer = SqliteSaver(self._db_conn)
        self.graph_workflow = build_graph_workflow(self, self._checkpointer)

    def stream_chat(self, message: str, conversation_history: list | None = None) -> Generator[str, None, None]:
        from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
        messages = []
        for entry in (conversation_history or []):
            role = entry.get("role", "")
            content = entry.get("content", "")
            if not content:
                continue
            if role == "user":
                messages.append(HumanMessage(content=content))
            elif role == "assistant":
                messages.append(AIMessage(content=content))
            elif role == "system":
                messages.append(SystemMessage(content=content))
        # 如果历史末尾已经包含当前消息，则不重复添加
        if not messages or not isinstance(messages[-1], HumanMessage) or messages[-1].content != message:
            messages.append(HumanMessage(content=message))
        for chunk in self.Chat_model.stream(messages):
            content = getattr(chunk, "content", "") or ""
            if content:
                yield content

    def _invoke_graph_prompt(self, prompt: str) -> tuple[dict, list[str], str]:
        response = self.Chat_model.invoke(prompt)
        content = response.content if hasattr(response, "content") else str(response)
        payload = extract_json_object(content)
        graph = normalize_graph(payload)
        errors = validate_graph(graph)
        return graph, errors, content

    def _invoke_text_prompt(self, prompt: str) -> str:
        response = self.Chat_model.invoke(prompt)
        content = response.content if hasattr(response, "content") else str(response)
        return content.strip()

    def generate_graph(self, description: str) -> tuple[dict, list[str], str]:
        prompt = f"{GRAPH_PROMPT}\n\n用户描述如下：\n{description}\n"
        return self._invoke_graph_prompt(prompt)

    def repair_graph(self, description: str, graph: dict, errors: list[str]) -> tuple[dict, list[str], str]:
        prompt = GRAPH_REPAIR_PROMPT.format(
            description=description,
            graph_json=json.dumps(graph, ensure_ascii=False, indent=2),
            errors="\n".join(f"- {error}" for error in errors),
        )
        return self._invoke_graph_prompt(prompt)

    def preview_graph_edit(self, instruction: str, current_graph: dict, rewire: bool = False) -> str:
        prompt = GRAPH_EDIT_CONFIRM_PROMPT.format(
            instruction=instruction,
            graph_json=json.dumps(current_graph, ensure_ascii=False, indent=2),
        )
        try:
            preview = self._invoke_text_prompt(prompt)
        except Exception:
            preview = ""

        if preview:
            return preview

        if rewire:
            return (
                "我理解你的意思是，这次不是单纯调整节点显示位置，而是要修改流程先后关系和边的连接方式。"
                "我会按当前草稿重连相关分支，让目标节点真正出现在你指定的判断节点后面。"
                "如果理解无误，请回复“确认修改”。"
            )

        return (
            "我理解你的意思是，要在当前草稿上做一次定向修改，并尽量保留其余结构不变。"
            "我会先按你的描述更新相关节点或分支，再把结果返回给你。"
            "如果理解无误，请回复“确认修改”。"
        )

    def classify_confirm_intent(self, message: str, pending_instruction: str) -> tuple[bool, str]:
        prompt = CONFIRM_CLASSIFY_PROMPT.format(
            pending_instruction=pending_instruction,
            message=message,
        )
        try:
            result = self._invoke_text_prompt(prompt).strip()
        except Exception as exc:
            return False, f"classifier_error:{exc}"

        normalized = result.lower().strip()
        token = normalized.split()[0] if normalized else ""
        return token == "confirm", result

    def classify_request_intent(self, message: str, has_current_graph: bool) -> tuple[str, str]:
        prompt = REQUEST_INTENT_CLASSIFY_PROMPT.format(
            has_current_graph="yes" if has_current_graph else "no",
            message=message,
        )
        try:
            result = self._invoke_text_prompt(prompt).strip()
            token = result.lower().split()[0] if result else ""
            if token in {"chat", "graph_create", "graph_edit", "path_generate", "undo"}:
                if token in {"graph_edit", "path_generate", "undo"} and not has_current_graph:
                    return "chat", f"invalid_without_graph:{result}"
                return token, result
        except Exception as exc:
            result = f"classifier_error:{exc}"
        return self._fallback_request_intent(message, has_current_graph), result

    def _fallback_request_intent(self, message: str, has_current_graph: bool) -> str:
        if has_current_graph and detect_path_generation_intent(message):
            return "path_generate"
        if has_current_graph and detect_graph_edit_intent(message):
            return "graph_edit"
        if detect_graph_intent(message):
            return "graph_create"
        return "chat"

    def edit_graph(self, instruction: str, current_graph: dict, rewire: bool = False) -> tuple[dict, list[str], str]:
        prompt_template = GRAPH_REWIRE_PROMPT if rewire else GRAPH_EDIT_PROMPT
        prompt = prompt_template.format(
            instruction=instruction,
            graph_json=json.dumps(current_graph, ensure_ascii=False, indent=2),
        )
        return self._invoke_graph_prompt(prompt)

    def extract_resume_message(self, resume_payload) -> str:
        if isinstance(resume_payload, dict):
            return str(resume_payload.get("message") or "").strip()
        return str(resume_payload or "").strip()

    def extract_resume_request_id(self, resume_payload) -> int:
        if isinstance(resume_payload, dict):
            try:
                return int(resume_payload.get("request_id") or 0)
            except (TypeError, ValueError):
                return 0
        return 0

    def start_request(self, thread_id: str) -> int:
        next_id = self._request_counters.get(thread_id, 0) + 1
        self._request_counters[thread_id] = next_id
        return next_id

    def is_request_active(self, thread_id: str, request_id: int) -> bool:
        if not thread_id or request_id <= 0:
            return True
        return self._request_counters.get(thread_id, 0) == request_id

    # ── state diagram ─────────────────────────────────────────────────────────

    def _invoke_state_diagram_prompt(self, prompt: str) -> tuple[dict, list[str], str]:
        response = self.Chat_model.invoke(prompt)
        content = response.content if hasattr(response, "content") else str(response)
        payload = extract_json_object(content)
        diagram = normalize_state_diagram(payload)
        errors = validate_state_diagram(diagram)
        return diagram, errors, content

    def parse_state_requirements(self, description: str) -> dict:
        prompt = STATE_REQUIREMENTS_PROMPT.format(description=description)
        try:
            response = self.Chat_model.invoke(prompt)
            content = response.content if hasattr(response, "content") else str(response)
            payload = extract_json_object(content)
            return payload if isinstance(payload, dict) else {}
        except Exception as exc:
            return {
                "title": "状态图",
                "description": description,
                "candidate_states": [],
                "candidate_transitions": [],
                "_error": str(exc),
            }

    def generate_state_diagram(
        self,
        description: str,
        requirements: dict | None = None,
        depth: int = 0,
    ) -> tuple[dict, list[str], str]:
        prompt = STATE_DIAGRAM_PROMPT.format(description=description, depth=depth)
        diagram, errors, raw = self._invoke_state_diagram_prompt(prompt)

        if depth < 3:
            candidate_map: dict[str, str] = {}
            if requirements:
                for s in requirements.get("candidate_states") or []:
                    name = s.get("name") or ""
                    if name:
                        candidate_map[name] = s.get("description") or ""

            for node in diagram.get("nodes") or []:
                node_type = node.get("type", "")
                if node_type not in ("sequential_composite", "concurrent_composite"):
                    continue
                if node.get("sub_diagram"):
                    continue
                label = node.get("label", "")
                is_concurrent = node_type == "concurrent_composite"
                constraint = (
                    "必须包含 fork 节点（1入多出）和 join 节点（多入1出）"
                    if is_concurrent
                    else "不能包含 fork 和 join 节点"
                )
                sub_prompt = STATE_SUB_DIAGRAM_PROMPT.format(
                    parent_description=description,
                    label=label,
                    node_type="并发" if is_concurrent else "顺序",
                    constraint=constraint,
                    depth=depth + 1,
                )
                sub_diagram, sub_errors, _ = self.generate_state_diagram(
                    sub_prompt, requirements, depth + 1
                )
                node["sub_diagram"] = sub_diagram
                errors.extend(f"子图[{label}]:{e}" for e in sub_errors)

        return diagram, errors, raw

    def classify_sd_feedback(self, message: str, requirements: dict) -> tuple[str, str]:
        prompt = SD_FEEDBACK_CLASSIFY_PROMPT.format(
            draft=json.dumps(requirements, ensure_ascii=False, indent=2),
            message=message,
        )
        try:
            result = self._invoke_text_prompt(prompt).strip().lower()
            token = result.split()[0] if result else ""
            if token in {"confirm", "modify", "clarify"}:
                return token, result
        except Exception as exc:
            return "confirm", f"classifier_error:{exc}"
        return "confirm", result

    def apply_sd_modification(self, requirements: dict, instruction: str) -> dict:
        prompt = SD_MODIFICATION_PROMPT.format(
            draft=json.dumps(requirements, ensure_ascii=False, indent=2),
            instruction=instruction,
        )
        try:
            response = self.Chat_model.invoke(prompt)
            content = response.content if hasattr(response, "content") else str(response)
            payload = extract_json_object(content)
            return payload if isinstance(payload, dict) else requirements
        except Exception:
            return requirements

    def generate_sd_clarification(self, requirements: dict, question: str) -> str:
        prompt = SD_CLARIFICATION_PROMPT.format(
            draft=json.dumps(requirements, ensure_ascii=False, indent=2),
            question=question,
        )
        try:
            return self._invoke_text_prompt(prompt)
        except Exception:
            return f'请问关于"{question}"，您能提供更多说明吗？'

    def preview_sd_edit(self, instruction: str, current_diagram: dict) -> str:
        prompt = SD_PREVIEW_EDIT_PROMPT.format(
            current_diagram=json.dumps(current_diagram, ensure_ascii=False, indent=2),
            instruction=instruction,
        )
        try:
            preview = self._invoke_text_prompt(prompt)
        except Exception:
            preview = ""
        return preview or (
            '我理解你的意思是，要对当前状态图做一次定向修改。'
            '我会按你的描述更新相关节点或迁移，并保持其余结构不变。'
            '如果理解无误，请回复\u201c确认修改\u201d。'
        )

    def edit_state_diagram(self, instruction: str, current_diagram: dict) -> tuple[dict, list[str], str]:
        prompt = SD_EDIT_PROMPT.format(
            current_diagram=json.dumps(current_diagram, ensure_ascii=False, indent=2),
            instruction=instruction,
        )
        return self._invoke_state_diagram_prompt(prompt)

    # ── graph type ────────────────────────────────────────────────────────────

    def classify_graph_type(self, message: str) -> str:
        prompt = GRAPH_TYPE_CLASSIFY_PROMPT.format(message=message)
        try:
            result = self._invoke_text_prompt(prompt).strip().lower()
            token = result.split()[0] if result else ""
            if token in {"cfg", "state_diagram"}:
                return token
        except Exception:
            pass
        return "state_diagram" if detect_state_diagram_intent(message) else "cfg"

    def get_previous_graph(self, thread_id: str, current_graph: dict | None) -> dict | None:
        """遍历 checkpoint 历史，返回与 current_graph 不同的最近一个 current_graph。"""
        config = {"configurable": {"thread_id": thread_id}}
        current_fp = json.dumps(current_graph, ensure_ascii=False, sort_keys=True) if current_graph else None
        for snapshot in self.graph_workflow.get_state_history(config):
            snap_graph = snapshot.values.get("current_graph")
            if not isinstance(snap_graph, dict) or not snap_graph.get("nodes"):
                continue
            snap_fp = json.dumps(snap_graph, ensure_ascii=False, sort_keys=True)
            if snap_fp != current_fp:
                return snap_graph
        return None

    def stream_message(
        self,
        message: str,
        current_graph: dict | None = None,
        thread_id: str | None = None,
    ):
        resolved_thread_id = (thread_id or "default").strip() or "default"
        request_id = self.start_request(resolved_thread_id)
        config = {
            "configurable": {
                "thread_id": resolved_thread_id,
            }
        }
        state_snapshot = self.graph_workflow.get_state(config)
        has_interrupt = bool(getattr(state_snapshot, "interrupts", ()))

        if has_interrupt:
            payload = Command(resume={"message": message, "request_id": request_id})
        else:
            payload = {
                "message": message,
                "current_graph": current_graph,
                "thread_id": resolved_thread_id,
                "request_id": request_id,
            }

        for event in self._stream_graph_events(payload, config):
            yield event

    def _stream_graph_events(self, payload, config) -> Iterator[dict]:
        raw_stream = self.graph_workflow.stream(
            payload,
            config=config,
            stream_mode=["messages", "updates", "custom"],
            subgraphs=True,
            version="v2",
        )
        for raw_event in raw_stream:
            normalized = self._normalize_stream_event(raw_event)
            if normalized is not None:
                yield normalized

    def _normalize_stream_event(self, raw_event):
        if isinstance(raw_event, dict) and "type" in raw_event and "data" in raw_event:
            mode = raw_event.get("type")
            namespace = raw_event.get("ns") or []
            data = raw_event.get("data")

            if mode == "custom":
                return data if isinstance(data, dict) else {"type": "custom", "content": str(data)}

            if mode == "updates":
                if isinstance(data, dict) and "__interrupt__" in data:
                    interrupts = data.get("__interrupt__") or []
                    return {
                        "type": "interrupt",
                        "count": len(interrupts),
                        "payload": [
                            getattr(item, "value", item)
                            for item in interrupts
                        ],
                    }
                return {
                    "type": "node_update",
                    "namespace": list(namespace),
                    "payload": data,
                }

            if mode == "messages":
                message_chunk, metadata = data
                content = getattr(message_chunk, "content", "") or ""
                if not content:
                    return None
                return {
                    "type": "llm_token",
                    "namespace": list(namespace),
                    "content": content,
                    "metadata": metadata,
                }

            return {
                "type": mode,
                "namespace": list(namespace),
                "payload": data,
            }

        namespace: tuple[str, ...] = ()
        mode = None
        data = raw_event

        if isinstance(raw_event, tuple):
            if len(raw_event) == 3:
                namespace, mode, data = raw_event
            elif len(raw_event) == 2:
                mode, data = raw_event

        if mode == "custom":
            return data if isinstance(data, dict) else {"type": "custom", "content": str(data)}

        if mode == "updates":
            return {
                "type": "node_update",
                "namespace": list(namespace) if namespace else [],
                "payload": data,
            }

        if mode == "messages":
            message_chunk, metadata = data
            content = getattr(message_chunk, "content", "") or ""
            if not content:
                return None
            return {
                "type": "llm_token",
                "namespace": list(namespace) if namespace else [],
                "content": content,
                "metadata": metadata,
            }

        if isinstance(data, dict):
            return data

        return {
            "type": "stream_event",
            "namespace": list(namespace) if namespace else [],
            "mode": mode,
            "payload": data,
        }
