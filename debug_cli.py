"""
交互式 debug CLI，直接调用 AgentManager，不需要启动服务器。
用法：python debug_cli.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from backwards.AgentManager import AgentManager

CONFIG_PATH = PROJECT_ROOT / "backwards" / "config.json"
THREAD_ID = "debug"


def print_event(event: dict) -> None:
    t = event.get("type", "")
    if t == "delta":
        print(event.get("content", ""), end="", flush=True)
    elif t == "status":
        print(f"\n[状态] {event.get('content', '')}", flush=True)
    elif t == "mode":
        print(f"\n[模式] {event.get('mode', '')}", flush=True)
    elif t == "activity":
        print(f"  [{event.get('label', '')}] {event.get('content', '')}", flush=True)
    elif t == "graph_result":
        payload = event.get("payload", {})
        graph = payload.get("graph") or {}
        print(f"\n[图结果] 节点数={len(graph.get('nodes', []))} 边数={len(graph.get('edges', []))} 有效={payload.get('valid')}", flush=True)
    elif t == "path_result":
        payload = event.get("payload", {})
        print(f"\n[路径结果] 文件={payload.get('path')} 行数={len(payload.get('rows', []))}", flush=True)
    elif t == "interrupt":
        print(f"\n[中断] 等待确认...", flush=True)
    elif t == "done":
        print()  # 换行
    elif t == "error":
        print(f"\n[错误] {event.get('content', '')}", flush=True)


def show_help() -> None:
    print("""
命令：
  /graph         打印当前图的 JSON
  /history       打印 checkpoint 历史摘要
  /thread <id>   切换 thread（默认 debug）
  /clear         清除当前图（模拟新会话）
  /help          显示此帮助
  /quit          退出
""")


def main() -> None:
    print("=== 控制流图助手 Debug CLI ===")
    print("输入 /help 查看命令，输入消息直接对话\n")

    manager = AgentManager(str(CONFIG_PATH))
    current_graph: dict | None = None
    thread_id = THREAD_ID

    while True:
        try:
            user_input = input(f"[{thread_id}] > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n退出")
            break

        if not user_input:
            continue

        if user_input == "/quit":
            break
        elif user_input == "/help":
            show_help()
            continue
        elif user_input == "/graph":
            if current_graph:
                print(json.dumps(current_graph, ensure_ascii=False, indent=2))
            else:
                print("（当前无图）")
            continue
        elif user_input == "/history":
            history = manager.get_history(thread_id)
            if not history:
                print("（无历史）")
            for i, h in enumerate(history):
                print(f"  {i+1}. [{h.get('checkpoint_id', '')[:12]}...] 消息数={h.get('message_count')} 图={h.get('graph_summary')} 末尾={h.get('last_message')}")
            continue
        elif user_input.startswith("/thread "):
            thread_id = user_input[8:].strip() or THREAD_ID
            current_graph = None
            print(f"已切换到 thread: {thread_id}")
            continue
        elif user_input == "/clear":
            current_graph = None
            print("已清除当前图")
            continue

        # 普通消息
        print()
        for event in manager.stream_message(user_input, current_graph=current_graph, thread_id=thread_id):
            print_event(event)
            # 同步更新本地的 current_graph
            if event.get("type") == "graph_result":
                g = (event.get("payload") or {}).get("graph")
                if isinstance(g, dict) and g.get("nodes"):
                    current_graph = g


if __name__ == "__main__":
    main()
