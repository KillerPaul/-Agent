from __future__ import annotations

from xml.etree.ElementTree import Element, SubElement, tostring


NODE_SIZES = {
    "start": (20, 20),
    "end": (30, 30),
    "decision": (80, 80),
    "process": (100, 30),
}


def _default_layout(graph: dict) -> dict[str, dict[str, int]]:
    positions = {}
    level_map: dict[str, int] = {}
    incoming: dict[str, int] = {node["id"]: 0 for node in graph.get("nodes", [])}
    children: dict[str, list[str]] = {node["id"]: [] for node in graph.get("nodes", [])}

    for edge in graph.get("edges", []):
        if edge["target"] in incoming:
            incoming[edge["target"]] += 1
        if edge["source"] in children:
            children[edge["source"]].append(edge["target"])

    queue = [(node_id, 0) for node_id, count in incoming.items() if count == 0]
    while queue:
        node_id, level = queue.pop(0)
        if node_id in level_map and level_map[node_id] >= level:
            continue
        level_map[node_id] = level
        for child in children.get(node_id, []):
            queue.append((child, level + 1))

    rows: dict[int, list[str]] = {}
    for node in graph.get("nodes", []):
        level = level_map.get(node["id"], 0)
        rows.setdefault(level, []).append(node["id"])

    for level, node_ids in rows.items():
        start_x = 900 - ((len(node_ids) - 1) * 220) // 2
        for index, node_id in enumerate(node_ids):
            positions[node_id] = {"x": start_x + index * 220, "y": 60 + level * 170}

    return positions


def _control_node_id(node: dict, counters: dict[str, int]) -> str:
    node_type = node["type"]
    if node_type == "start":
        return "InitJuctionControl1"
    if node_type == "end":
        return "FinalControl1"
    if node_type == "decision":
        counters["decision"] += 1
        return f"DecideControl{counters['decision']}"
    counters["process"] += 1
    return node.get("label") or f"control{counters['process']}"


def graph_to_control(graph: dict, positions: dict | None = None) -> str:
    source_positions = positions or graph.get("positions") or _default_layout(graph)
    chart = Element(
        "Chart",
        {
            "xmlns:xmi": "http://www.omg.org/XMI",
            "xmi:version": "1.0",
            "name": graph.get("title", "control-f-v1"),
            "lastSaveTime": "2026-02-06 19:17:46",
        },
    )
    models = SubElement(chart, "Models")

    counters = {"decision": 0, "process": 0}
    model_id_map: dict[str, str] = {}
    node_name_map: dict[str, str] = {}

    for node in graph.get("nodes", []):
        control_id = _control_node_id(node, counters)
        model_id_map[node["id"]] = control_id
        node_name_map[node["id"]] = node.get("label", control_id)
        class_name = {
            "start": "InitJuctionControl",
            "end": "FinalControl",
            "decision": "DecideConrol",
            "process": "control",
        }[node["type"]]
        model = SubElement(models, "Model", {"name": node_name_map[node["id"]], "Class": class_name})
        position = source_positions.get(node["id"], {"x": 900, "y": 100})
        width, height = NODE_SIZES[node["type"]]

        def prop(name: str, value: str = "", qualifier: str | None = None):
            attrs = {"name": name}
            if qualifier is None:
                attrs["value"] = value
            else:
                attrs["qualifier"] = qualifier
            SubElement(model, "properties", attrs)

        if node["type"] in {"decision", "process"}:
            prop(node_name_map[node["id"]], qualifier="")
        prop("seqid", "1")
        prop("id", control_id)
        prop("name", node_name_map[node["id"]])
        prop("topx", str(int(position["x"])))
        prop("topy", str(int(position["y"])))
        prop("width", str(width))
        prop("height", str(height))
        prop("desc", node_name_map[node["id"]])
        if node["type"] == "process":
            for key in ["fTable", "fsTable", "entry", "update", "exit", "color", "flag"]:
                prop(key, "" if key != "flag" else "0")
        elif node["type"] in {"start", "end"}:
            for key in ["fTable", "fsTable", "update", "color", "flag"]:
                prop(key, "" if key != "flag" else "0")
        else:
            prop("update", "")
            prop("flag", "0")
            prop("cond", str(node.get("condition") or ""))

    for index, edge in enumerate(graph.get("edges", []), start=1):
        SubElement(
            models,
            "Transition",
            {
                "id": edge["id"],
                "name": edge["id"],
                "EventId": "",
                "CondId": "",
                "Logic": "",
                "Source": model_id_map.get(edge["source"], edge["source"]),
                "Target": model_id_map.get(edge["target"], edge["target"]),
                "Desc": f"转移{index}",
                "SourcePort": "1",
                "TargetPort": "1",
                "seqid": str(index),
                "LinkInState": "",
                "type": "TransitionControlModel",
                "flag": "0",
                "LineType": edge.get("label", ""),
            },
        )

    SubElement(chart, "PathModel", {"SC": "1", "TC": "1", "TPC": "0", "FPC": "0", "ZOT": "0"})
    SubElement(chart, "Vars")
    SubElement(chart, "enums")
    return tostring(chart, encoding="utf-8", xml_declaration=True).decode("utf-8")
