# Control Flow Graph Assistant

一个基于 LangGraph 和大语言模型的控制流图生成工具。用户可以用自然语言描述业务流程，系统会将描述转换为结构化控制流图 JSON，并支持后续编辑、校验、导出 `.control` / XML，以及调用本地路径生成流水线。

项目同时提供：

- Debug CLI：便于调试工作流和查看生成结果。
- Web 前端：通过 Flask 提供接口和页面。
- 桌面入口：通过 PySide6 WebEngine 包装本地 Web 应用。
- LangGraph 工作流：负责意图识别、图生成、图编辑、自动修复、状态保存和中断恢复。

## Features

- 自然语言生成控制流图。
- 支持 start / end / process / decision 节点。
- 自动校验控制流图结构。
- 本地规则修复和 LLM 修复。
- 支持在已有图上进行二次编辑。
- 支持撤回上一版图。
- 支持状态图生成流程。
- 支持导出 `.control` 和 XML。
- 支持调用本地 exe 流水线生成路径结果。
- 基于 SQLite checkpoint 保存会话状态。

## Project Structure

```text
.
├── debug_cli.py                 # 命令行调试入口
├── start.py                     # PySide6 桌面入口
├── json2control.py              # JSON 到 control 的辅助脚本
├── backwards/
│   ├── AgentManager.py          # LLM、LangGraph、会话流式输出管理
│   ├── langgraph_workflow.py    # 核心 LangGraph 工作流
│   ├── flow_graph.py            # 控制流图 prompt、解析、校验、修复
│   ├── state_graph.py           # 状态图 prompt、解析、校验
│   ├── path_pipeline.py         # 路径生成 exe 流水线封装
│   ├── control_export.py        # 导出 .control
│   ├── xml_export.py            # 导出 XML
│   └── config.json              # 模型配置，本地使用，不建议提交密钥
├── front/
│   ├── port.py                  # Flask API
│   ├── templates/index.html     # Web 页面
│   └── static/                  # 前端资源
├── path/                        # 本地路径生成工具和输出目录
└── exports/                     # .control 导出目录
```

## Requirements

建议使用 Python 3.11。

主要依赖：

```bash
pip install flask python-dotenv langchain-openai langgraph langgraph-checkpoint-sqlite pydantic PySide6
```

如果只运行 `debug_cli.py`，通常不需要 PySide6。

## Configuration

模型配置读取自 `backwards/config.json`，格式如下：

```json
{
  "Chat_model": {
    "base_url": "https://your-model-endpoint/v1",
    "model": "your-model-name",
    "api_key": "your-api-key",
    "timeout": 60,
    "max_tokens": 8192
  }
}
```

上传 GitHub 前不要提交真实 `api_key`。建议改成 `config.example.json`，真实配置只保留在本地，或通过 `.env` 注入。

## Run

### Debug CLI

```bash
python debug_cli.py
```

常用命令：

```text
/graph         打印当前图 JSON
/history       打印 checkpoint 历史摘要
/thread <id>   切换 thread
/clear         清除当前 CLI 中的图引用
/help          显示帮助
/quit          退出
```

直接输入自然语言即可生成或编辑图，例如：

```text
生成一个订单流程控制流图：开始后创建订单，判断库存是否充足，充足则扣减库存并支付，不充足则结束。
```

### Web API

```bash
python front/port.py
```

默认地址：

```text
http://127.0.0.1:5000
```

主要接口：

- `POST /chat`：流式处理用户输入，返回 NDJSON 事件。
- `POST /graph/export-xml`：将当前控制流图导出为 XML。
- `POST /graph/export-control`：将当前控制流图导出为 `.control`。

### Desktop App

```bash
python start.py
```

该入口会启动本地 Flask 服务，并用 PySide6 WebEngine 打开桌面窗口。

## Workflow Overview

控制流图生成主链路：

```text
debug_cli.py / front.port
-> AgentManager.stream_message()
-> LangGraph detect_request
-> graph_prepare
-> determine_graph_type
-> generate_initial
-> AgentManager.generate_graph()
-> extract_json_object()
-> normalize_graph()
-> validate_graph()
-> local_repair / model_repair
-> finalize
-> graph_result
```

其中：

- `detect_request` 判断请求是普通聊天、建图、改图、路径生成还是撤回。
- `determine_graph_type` 判断是控制流图还是状态图。
- `generate_initial` 调用模型生成初始 JSON。
- `validate_graph` 校验结构规则。
- `local_repair` 和 `model_repair` 负责自动修复。
- `finalize` 输出最终 `graph_result`。

## Graph JSON Format

控制流图使用如下结构：

```json
{
  "version": "1.0",
  "title": "流程标题",
  "nodes": [
    { "id": "n1", "type": "start", "label": "开始" },
    { "id": "n2", "type": "process", "label": "处理步骤" },
    { "id": "n3", "type": "decision", "label": "是否满足条件", "condition": "满足条件" },
    { "id": "n4", "type": "end", "label": "结束" }
  ],
  "edges": [
    { "id": "e1", "source": "n1", "target": "n2" },
    { "id": "e2", "source": "n2", "target": "n3" },
    { "id": "e3", "source": "n3", "target": "n4", "label": "是" },
    { "id": "e4", "source": "n3", "target": "n2", "label": "否" }
  ],
  "assumptions": []
}
```

核心约束：

- 节点类型只能是 `start`、`end`、`process`、`decision`。
- 默认只允许一个 `start` 节点。
- `decision` 节点必须有 `condition`。
- `decision` 节点必须正好有两条出边。
- `decision` 节点两条出边标签必须分别是 `是` 和 `否`。
- `end` 节点不能有出边。

## Path Generation

路径生成由 `backwards/path_pipeline.py` 封装，依赖 `path/` 目录下的本地 exe：

```text
control2control.exe
activity_to_state.exe
path_generation.exe
path_convert.exe
trans.exe
```

流水线大致为：

```text
graph
-> .control
-> control2control.exe
-> activity_to_state.exe
-> path_generation.exe
-> path_convert.exe
-> trans.exe
-> pathset result
```

如果这些 exe 不存在或不兼容当前系统，路径生成功能会失败，但图生成和导出仍可单独使用。

## GitHub Notes

上传前建议不要提交以下内容：

```text
backwards/config.json          # 如果包含真实 api_key
backwards/.env
backwards/workflow_state.db*
backwards/graph_debug.txt
graph_debug_summary.txt
graph_debug_log.txt
__pycache__/
build/
dist/
exports/
path/results/
path/models/
```

建议新增 `.gitignore`，并把真实配置改成本地私有文件。

## License

如需开源，请在仓库中补充 `LICENSE` 文件。
