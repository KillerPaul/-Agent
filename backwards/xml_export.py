from __future__ import annotations

from xml.etree.ElementTree import Element, SubElement, tostring


def graph_to_xml(graph: dict) -> str:
    root = Element(
        "UML:StateMachine",
        {
            "xmlns:UML": "www.mte-info.com",
            "xmi.id": "control-f-v2",
            "name": graph.get("title", "control-f-v2"),
            "SC": "1",
            "TC": "1",
            "TPC": "1",
            "FPC": "1",
            "ZOT": "0",
            "transpair_percent": "90",
            "zot_percent": "90",
        },
    )

    top = SubElement(root, "UML:StateMachine.top")
    composite = SubElement(
        top,
        "UML:CompositeState",
        {"xmi.id": "CompositeState.0", "name": "TOP", "isConcurrent": "false"},
    )
    subvertex = SubElement(composite, "UML:CompositeState.subvertex")

    outgoing_map: dict[str, list[str]] = {}
    for edge in graph.get("edges", []):
        outgoing_map.setdefault(edge["source"], []).append(edge["id"])

    for node in graph.get("nodes", []):
        node_id = node["id"]
        outgoing = ",".join(outgoing_map.get(node_id, []))
        common = {
            "xmi.id": node_id,
            "name": node.get("label", node_id),
            "flag": "0",
            "description": node.get("label", ""),
            "outgoing": outgoing,
            "update": "",
            "container": "CompositeState.0",
        }
        if node["type"] == "start":
            common["kind"] = "initial"
            SubElement(subvertex, "UML:Pseudostate", common)
        elif node["type"] == "end":
            common["kind"] = "final"
            SubElement(subvertex, "UML:FinalState", common)
        elif node["type"] == "decision":
            common["kind"] = "choice"
            SubElement(subvertex, "UML:Pseudostate", common)
        else:
            common.update({"entry": "", "do": "", "exit": "", "kind": "simple"})
            SubElement(subvertex, "UML:SimpleState", common)

    transitions = SubElement(root, "UML:StateMachine.transitions")
    node_map = {node["id"]: node for node in graph.get("nodes", [])}

    for edge in graph.get("edges", []):
        transition = SubElement(
            transitions,
            "UML:Transition",
            {
                "xmi.id": edge["id"],
                "name": edge["id"],
                "description": edge.get("description", edge["id"]),
                "source": edge["source"],
                "flag": "0",
                "target": edge["target"],
                "color": "",
                "append": "",
                "LineType": edge.get("label", ""),
            },
        )
        source_node = node_map.get(edge["source"], {})
        if source_node.get("type") == "decision" and edge.get("label") in {"是", "否"}:
            condition_text = str(source_node.get("condition") or source_node.get("label") or "")
            expression = f"({condition_text})" if edge["label"] == "是" else f"!({condition_text})"
            uml_condition = SubElement(
                transition,
                "UML:UMLCondition",
                {"xmi.id": f"UMLCondition.{edge['id']}", "name": f"Condition.{edge['id']}", "transition": edge["id"]},
            )
            SubElement(
                uml_condition,
                "UML:Transition.guard",
                {
                    "xmi.id": f"guard_{edge['id']}",
                    "name": f"guard_{edge['id']}",
                    "expression": expression,
                },
            )

    return tostring(root, encoding="utf-8", xml_declaration=True).decode("utf-8")
