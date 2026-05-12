from pathlib import Path
import json
import sys

from flask import Flask, Response, jsonify, render_template, request, stream_with_context

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backwards.AgentManager import AgentManager
from backwards.flow_graph import normalize_graph, validate_graph
from backwards.path_pipeline import (
    export_graph_control as export_graph_control_file,
    export_graph_xml_to_activity,
)

app = Flask(__name__)
agentmanager = AgentManager(str(PROJECT_ROOT / "backwards" / "config.json"))


def ndjson_line(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False) + "\n"


@app.route("/")
def home():
    return render_template("index.html")


@app.route("/chat", methods=["POST"])
def chat():
    data = request.get_json(silent=True) or {}
    message = (data.get("message") or "").strip()
    current_graph = data.get("current_graph")
    thread_id = (data.get("thread_id") or "").strip()

    if not message:
        return jsonify({"error": "message 不能为空"}), 400

    def generate():
        try:
            for event in agentmanager.stream_message(
                message,
                current_graph=current_graph,
                thread_id=thread_id,
            ):
                yield ndjson_line(event)
        except Exception as exc:
            yield ndjson_line({"type": "error", "content": str(exc)})

    return Response(
        stream_with_context(generate()),
        mimetype="application/x-ndjson; charset=utf-8",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/graph/export-xml", methods=["POST"])
def export_graph_xml():
    data = request.get_json(silent=True) or {}
    graph = normalize_graph(data.get("graph") or {})
    errors = validate_graph(graph)
    if errors:
        return jsonify({"error": "graph 校验失败，无法导出 XML", "details": errors}), 400

    try:
        filename, file_path = export_graph_xml_to_activity(graph)
    except OSError as exc:
        return jsonify({"error": f"XML 导出失败: {exc}"}), 500

    return jsonify(
        {
            "ok": True,
            "filename": filename,
            "path": str(file_path),
        }
    )


@app.route("/graph/export-control", methods=["POST"])
def export_graph_control():
    data = request.get_json(silent=True) or {}
    graph = normalize_graph(data.get("graph") or {})
    positions = data.get("positions")
    errors = validate_graph(graph)
    if errors:
        return jsonify({"error": "graph 校验失败，无法导出 .control", "details": errors}), 400

    try:
        filename, file_path = export_graph_control_file(graph, positions=positions)
    except OSError as exc:
        return jsonify({"error": f".control 导出失败: {exc}"}), 500

    return jsonify(
        {
            "ok": True,
            "filename": filename,
            "path": str(file_path),
        }
    )


if __name__ == "__main__":
    app.run(port=5000, threaded=True)
