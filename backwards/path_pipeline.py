from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
from xml.etree import ElementTree as ET

from backwards.control_export import graph_to_control
from backwards.xml_export import graph_to_xml


PROJECT_ROOT = Path(__file__).resolve().parent.parent
EXPORT_ROOT = PROJECT_ROOT / "exports"
PATH_ROOT = PROJECT_ROOT / "path"
PATH_MODELS_ROOT = PATH_ROOT / "models"
ACTIVITY_MODEL_ROOT = PATH_MODELS_ROOT / "activity"
PATH_RESULTS_ROOT = PATH_ROOT / "results"

CONTROL2CONTROL_EXE = PATH_ROOT / "control2control.exe"
ACTIVITY_TO_STATE_EXE = PATH_ROOT / "activity_to_state.exe"
PATH_GENERATION_EXE = PATH_ROOT / "path_generation.exe"
PATH_CONVERT_EXE = PATH_ROOT / "path_convert.exe"
TRANS_EXE = PATH_ROOT / "trans.exe"

CONTROL2CONTROL_INPUT = PATH_ROOT / "control.xml"
ALTER_CONTROL_XML = PATH_ROOT / "Alter_control.xml"
ALTER_CONTROL_JSON = PATH_ROOT / "Alter_control.xml.json"
FINAL_PATHSET_XML = PATH_RESULTS_ROOT / "activity_all_pathSet.xml"


def _graph_name(graph: dict) -> str:
    name = str(graph.get("title") or "control-f-v2").strip()
    return name or "control-f-v2"


def _run_exe(args: list[str], timeout: int = 300, cwd: Path | None = None) -> dict:
    exe_path = Path(args[0])
    if not exe_path.exists():
        raise FileNotFoundError(f"{exe_path} does not exist")
    try:
        result = subprocess.run(
            args,
            cwd=str(cwd or PATH_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError(f"{exe_path.name} timed out") from exc
    return {
        "name": exe_path.name,
        "cwd": str(cwd or PATH_ROOT),
        "command": args,
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def export_graph_control(graph: dict, positions: dict | None = None) -> tuple[str, Path]:
    control_text = graph_to_control(graph, positions=positions)
    EXPORT_ROOT.mkdir(parents=True, exist_ok=True)
    filename = f"{_graph_name(graph)}.control"
    file_path = EXPORT_ROOT / filename
    file_path.write_text(control_text, encoding="utf-8")
    return filename, file_path


def export_graph_xml_to_activity(graph: dict) -> tuple[str, Path]:
    xml_text = graph_to_xml(graph)
    ACTIVITY_MODEL_ROOT.mkdir(parents=True, exist_ok=True)
    filename = f"{_graph_name(graph)}.xml"
    file_path = ACTIVITY_MODEL_ROOT / filename
    file_path.write_text(xml_text, encoding="utf-8")
    return filename, file_path


def prepare_control2control_input(control_path: Path) -> Path:
    if not control_path.exists():
        raise FileNotFoundError(f"{control_path} does not exist")
    shutil.copyfile(control_path, CONTROL2CONTROL_INPUT)
    return CONTROL2CONTROL_INPUT


def run_control2control(timeout: int = 300) -> dict:
    return _run_exe(
        [str(CONTROL2CONTROL_EXE)],
        timeout=timeout,
        cwd=PATH_ROOT,
    )

def _locate_alter_outputs() -> tuple[Path, Path]:
    xml_candidates = [
        PATH_ROOT / "Alter_control.xml",
    ]
    json_candidates = [
        PATH_ROOT / "Alter_control.xml.json",
    ]

    xml_path = next((path for path in xml_candidates if path.exists()), None)
    json_path = next((path for path in json_candidates if path.exists()), None)

    if xml_path is None:
        raise FileNotFoundError("control2control.exe 未生成 Alter_control.xml")
    if json_path is None:
        raise FileNotFoundError("control2control.exe 未生成 Alter_control.xml.json")
    return xml_path, json_path


def rename_alter_control_xml(source_xml_path: Path, graph_name: str) -> Path:
    target_path = PATH_ROOT / f"{graph_name}.xml"
    if target_path.exists():
        target_path.unlink()
    shutil.move(str(source_xml_path), str(target_path))
    return target_path


def run_activity_to_state(timeout: int = 300) -> dict:
    return _run_exe([str(ACTIVITY_TO_STATE_EXE)], timeout=timeout)


def run_path_generation(timeout: int = 300) -> dict:
    return _run_exe([str(PATH_GENERATION_EXE)], timeout=timeout)


def run_path_convert(timeout: int = 300) -> dict:
    return _run_exe([str(PATH_CONVERT_EXE)], timeout=timeout)


def run_trans(pathset_xml_path: Path, alter_control_json_path: Path, timeout: int = 300) -> dict:
    return _run_exe(
        [str(TRANS_EXE), str(pathset_xml_path), str(alter_control_json_path)],
        timeout=timeout,
    )


def parse_pathset_table(pathset_xml_path: Path, graph: dict | None = None) -> list[dict]:
    if not pathset_xml_path.exists():
        raise FileNotFoundError(f"{pathset_xml_path} does not exist")

    root = ET.parse(pathset_xml_path).getroot()
    rows: list[dict] = []
    for group in root.findall(".//Group"):
        group_id = group.attrib.get("xmi.id", "")
        for path in group.findall("./Path"):
            path_id = path.attrib.get("xmi.id", "")
            state_ids = [
                item.attrib.get("xmi.id", "")
                for item in path.findall("./PathInfo/State")
                if item.attrib.get("xmi.id")
            ]
            rows.append(
                {
                    "group_id": group_id,
                    "path_id": path_id,
                    "path_info": " -> ".join(state_ids),
                }
            )

    return rows


def run_full_path_pipeline(graph: dict, positions: dict | None = None, timeout: int = 300) -> dict:
    graph_name = _graph_name(graph)

    control_filename, control_path = export_graph_control(graph, positions=positions)
    prepare_control2control_input(control_path)
    control2control = run_control2control(timeout=timeout)
    alter_control_xml, alter_control_json = _locate_alter_outputs()
    renamed_alter_control_xml = rename_alter_control_xml(alter_control_xml, graph_name)

    activity_xml_filename, activity_xml_path = export_graph_xml_to_activity(graph)
    activity_to_state = run_activity_to_state(timeout=timeout)
    path_generation = run_path_generation(timeout=timeout)
    path_convert = run_path_convert(timeout=timeout)

    trans = run_trans(FINAL_PATHSET_XML, alter_control_json, timeout=timeout)
    table = parse_pathset_table(FINAL_PATHSET_XML, graph=graph)

    return {
        "graph_name": graph_name,
        "control": {
            "filename": control_filename,
            "path": str(control_path),
            "control2control_input_path": str(CONTROL2CONTROL_INPUT),
        },
        "alter_control": {
            "xml_path": str(renamed_alter_control_xml),
            "json_path": str(alter_control_json),
        },
        "activity_xml": {
            "filename": activity_xml_filename,
            "path": str(activity_xml_path),
        },
        "result": {
            "path": str(FINAL_PATHSET_XML),
            "rows": table,
        },
        "steps": {
            "control2control": control2control,
            "activity_to_state": activity_to_state,
            "path_generation": path_generation,
            "path_convert": path_convert,
            "trans": trans,
        },
    }
