import requests, json

graph = {
"version": "1.0",
"title": "射前自检流程",
"nodes": [
{"id": "n1", "type": "start", "label": "开始"},
{"id": "n2", "type": "process", "label": "系统上电"},
{"id": "n3", "type": "process", "label": "处理器自检（时钟频率、内存读写）"},
{"id": "n4", "type": "decision", "label": "处理器自检是否通过", "condition": "时钟频率正常且内存读写正常"},
{"id": "n5", "type": "process", "label": "标记处理器故障，上报故障码"},
{"id": "n6", "type": "end", "label": "终止流程（禁止发射）"},
{"id": "n7", "type": "process", "label": "惯导系统上电预热，启动陀螺仪与加速度计对准"},
{"id": "n8", "type": "decision", "label": "对准是否在时限内完成", "condition": "对准时间未超过预设阈值"},
{"id": "n9", "type": "process", "label": "触发超时告警，标记惯导故障，上报故障码"},
{"id": "n10", "type": "end", "label": "终止流程（禁止发射）"},
{"id": "n11", "type": "process", "label": "电源管理模块检测主电池电压"},
{"id": "n12", "type": "decision", "label": "电压是否高于下限阈值", "condition": "主电池电压≥下限阈值"},
{"id": "n13", "type": "process", "label": "发出低电告警，标记电源故障，上报故障码"},
{"id": "n14", "type": "end", "label": "终止流程（禁止发射）"},
{"id": "n15", "type": "process", "label": "引信安全模块自检，确认保险处于锁定位"},
{"id": "n16", "type": "decision", "label": "引信自检是否通过", "condition": "保险状态锁定且无异常"},
{"id": "n17", "type": "process", "label": "标记引信故障，上报故障码"},
{"id": "n18", "type": "end", "label": "终止流程（禁止发射）"},
{"id": "n19", "type": "process", "label": "通信模块自检完成，向地面发送'自检完成'状态帧"},
{"id": "n20", "type": "process", "label": "初始化重发计数器（count=0）"},
{"id": "n21", "type": "decision", "label": "是否在规定时间窗口内收到地面应答", "condition": "收到地面应答"},
{"id": "n22", "type": "decision", "label": "重发次数是否已达3次", "condition": "count≥3"},
{"id": "n23", "type": "process", "label": "重发状态帧，count加1"},
{"id": "n24", "type": "process", "label": "标记通信链路异常，上报故障码"},
{"id": "n25", "type": "end", "label": "终止流程（禁止发射）"},
{"id": "n26", "type": "process", "label": "所有模块自检通过，系统进入待命状态"},
{"id": "n27", "type": "process", "label": "等待发射许可指令"},
{"id": "n28", "type": "end", "label": "结束（待命）"}
],
"edges": [
{"id": "e1", "source": "n1", "target": "n2"},
{"id": "e2", "source": "n2", "target": "n3"},
{"id": "e3", "source": "n3", "target": "n4"},
{"id": "e4", "source": "n4", "target": "n7", "label": "是"},
{"id": "e5", "source": "n4", "target": "n5", "label": "否"},
{"id": "e6", "source": "n5", "target": "n6"},
{"id": "e7", "source": "n7", "target": "n8"},
{"id": "e8", "source": "n8", "target": "n11", "label": "是"},
{"id": "e9", "source": "n8", "target": "n9", "label": "否"},
{"id": "e10", "source": "n9", "target": "n10"},
{"id": "e11", "source": "n11", "target": "n12"},
{"id": "e12", "source": "n12", "target": "n15", "label": "是"},
{"id": "e13", "source": "n12", "target": "n13", "label": "否"},
{"id": "e14", "source": "n13", "target": "n14"},
{"id": "e15", "source": "n15", "target": "n16"},
{"id": "e16", "source": "n16", "target": "n19", "label": "是"},
{"id": "e17", "source": "n16", "target": "n17", "label": "否"},
{"id": "e18", "source": "n17", "target": "n18"},
{"id": "e19", "source": "n19", "target": "n20"},
{"id": "e20", "source": "n20", "target": "n21"},
{"id": "e21", "source": "n21", "target": "n26", "label": "是"},
{"id": "e22", "source": "n21", "target": "n22", "label": "否"},
{"id": "e23", "source": "n22", "target": "n24", "label": "是"},
{"id": "e24", "source": "n22", "target": "n23", "label": "否"},
{"id": "e25", "source": "n23", "target": "n21"},
{"id": "e26", "source": "n24", "target": "n25"},
{"id": "e27", "source": "n26", "target": "n27"},
{"id": "e28", "source": "n27", "target": "n28"}
],
"assumptions": [
"处理器自检失败后直接终止，不进行后续模块检查",
"各模块故障均导致独立的终止端节点，实际系统可合并为统一的禁止发射状态",
"通信重发逻辑使用计数器循环建模，最多重发3次",
"引信自检仅判断保险锁定状态，未展开内部子流程"
]
}

resp = requests.post("http://localhost:5000/graph/export-control", json={"graph": graph})
print(resp.json())
# {"ok": true, "filename": "control-flow-xxx.control", "path": "..."}
