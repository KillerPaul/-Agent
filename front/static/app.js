const SVG_NS = "http://www.w3.org/2000/svg";

const appShell = document.getElementById("appShell");
const composerForm = document.getElementById("composerForm");
const promptInput = document.getElementById("promptInput");
const chatScroll = document.getElementById("chatScroll");
const workspace = document.getElementById("workspace");
const graphPanel = document.getElementById("graphPanel");
const workspaceResizer = document.getElementById("workspaceResizer");
const closeGraphPanelButton = document.getElementById("closeGraphPanel");
const reopenGraphPanelButton = document.getElementById("reopenGraphPanel");
const sidebarToggleButton = document.getElementById("sidebarToggle");
const graphCanvas = document.getElementById("graphCanvas");
const graphPanLayer = document.getElementById("graphPanLayer");
const graphZoomLayer = document.getElementById("graphZoomLayer");
const edgeLayer = document.getElementById("edgeLayer");
const nodeLayer = document.getElementById("nodeLayer");
const graphTitle = document.getElementById("graphTitle");
const graphStatus = document.getElementById("graphStatus");
const modeBadge = document.getElementById("modeBadge");
const errorList = document.getElementById("errorList");
const jsonPreview = document.getElementById("jsonPreview");
const selectionInfo = document.getElementById("selectionInfo");
const progressList = document.getElementById("progressList");
const graphProgress = document.getElementById("graphProgress");
const zoomInButton = document.getElementById("zoomInButton");
const zoomOutButton = document.getElementById("zoomOutButton");
const zoomResetButton = document.getElementById("zoomResetButton");
const exportControlButton = document.getElementById("exportControlButton");
const exportXmlButton = document.getElementById("exportXmlButton");
const zoomLabel = document.getElementById("zoomLabel");
const confirmEditButton = document.getElementById("confirmEditButton");
const sdNavBar = document.getElementById("sdNavBar");
const sdBackButton = document.getElementById("sdBackButton");
const sdBreadcrumb = document.getElementById("sdBreadcrumb");

const graphState = {
    graph: null,
    positions: {},
    selectedNodeId: null,
    draggingNodeId: null,
    dragOffset: { x: 0, y: 0 },
    isPanning: false,
    panStart: { x: 0, y: 0 },
    panOrigin: { x: 0, y: 0 },
    panX: 0,
    panY: 0,
    zoom: 1,
    pendingEditInstruction: "",
    graphType: "cfg",
    sdDiagram: null,
    sdNavStack: [],
    sdCurrentDiagram: null,
    sdPositions: {},
};

const workflowThreadId = (() => {
    const storageKey = "workflow_thread_id";
    const existing = window.sessionStorage.getItem(storageKey);
    if (existing) return existing;
    const created = `thread-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
    window.sessionStorage.setItem(storageKey, created);
    return created;
})();

const layoutState = {
    isGraphVisible: false,
    isResizing: false,
    isSidebarCollapsed: false,
    chatWidthPercent: 50,
};

let activeChatAbortController = null;

function clonePositions(positions) {
    return Object.fromEntries(
        Object.entries(positions || {}).map(([nodeId, point]) => [
            nodeId,
            { x: point.x, y: point.y },
        ]),
    );
}

function mergePositions(nextGraph, previousPositions) {
    const fallback = buildLayout(nextGraph);
    const merged = {};
    nextGraph.nodes.forEach((node) => {
        const existing = previousPositions?.[node.id];
        merged[node.id] = existing && typeof existing.x === "number" && typeof existing.y === "number"
            ? { x: existing.x, y: existing.y }
            : fallback[node.id];
    });
    return merged;
}

function syncGraphPayloadPositions() {
    if (!graphState.graph) return;
    graphState.graph.positions = clonePositions(graphState.positions);
}

function refreshJsonPreview() {
    if (graphState.graphType === "state_diagram") {
        jsonPreview.textContent = graphState.sdDiagram ? JSON.stringify(graphState.sdDiagram, null, 2) : "等待生成...";
        return;
    }
    if (!graphState.graph) {
        jsonPreview.textContent = "等待生成...";
        return;
    }
    syncGraphPayloadPositions();
    jsonPreview.textContent = JSON.stringify(graphState.graph, null, 2);
}

function serializeCurrentGraph() {
    if (graphState.graphType === "state_diagram") return graphState.sdDiagram;
    if (!graphState.graph) return null;
    syncGraphPayloadPositions();
    return graphState.graph;
}

function serializeGraphForModel() {
    if (graphState.graphType === "state_diagram") return graphState.sdDiagram || null;
    if (!graphState.graph) return null;
    const { positions, ...graphWithoutPositions } = graphState.graph;
    return graphWithoutPositions;
}

function hasPendingEdit() {
    return Boolean(graphState.pendingEditInstruction);
}

function updateConfirmEditButton() {
    confirmEditButton.classList.toggle("is-hidden", !hasPendingEdit());
}

function setPendingEdit(instruction) {
    graphState.pendingEditInstruction = instruction || "";
    updateConfirmEditButton();
}

function clearPendingEdit() {
    graphState.pendingEditInstruction = "";
    updateConfirmEditButton();
}

function scrollChatToBottom() {
    chatScroll.scrollTop = chatScroll.scrollHeight;
}

function autoResize() {
    promptInput.style.height = "auto";
    promptInput.style.height = `${Math.min(promptInput.scrollHeight, 180)}px`;
}

function updateGraphReopenButton() {
    const shouldShow = Boolean(graphState.graph) && !layoutState.isGraphVisible;
    reopenGraphPanelButton.classList.toggle("is-hidden", !shouldShow);
}

function applyWorkspaceLayout() {
    appShell.classList.toggle("sidebar-collapsed", layoutState.isSidebarCollapsed);

    if (layoutState.isGraphVisible) {
        workspace.classList.add("with-graph");
        workspace.classList.remove("graph-hidden");
        graphPanel.classList.remove("is-hidden");
        workspaceResizer.classList.remove("is-hidden");
        workspace.style.setProperty("--chat-width", `${layoutState.chatWidthPercent}%`);
    } else {
        workspace.classList.remove("with-graph");
        workspace.classList.add("graph-hidden");
        graphPanel.classList.add("is-hidden");
        workspaceResizer.classList.add("is-hidden");
        workspace.style.removeProperty("--chat-width");
        if (!graphState.graph) {
            modeBadge.textContent = "当前模式：普通聊天";
        }
    }

    updateGraphReopenButton();
}

function showGraphPanel() {
    layoutState.isGraphVisible = true;
    applyWorkspaceLayout();
}

function hideGraphPanel() {
    layoutState.isGraphVisible = false;
    applyWorkspaceLayout();
}

function createMessage(role, content = "") {
    const row = document.createElement("article");
    row.className = `message-row ${role}`;

    const avatar = document.createElement("div");
    avatar.className = "avatar";
    avatar.textContent = role === "user" ? "我" : "AI";

    const bubble = document.createElement("div");
    bubble.className = "message-bubble";

    const author = document.createElement("p");
    author.className = "message-author";
    author.textContent = role === "user" ? "我" : "控制流图助手";

    const status = document.createElement("div");
    status.className = "message-status";

    const toolBubble = document.createElement("div");
    toolBubble.className = "tool-bubble is-hidden";

    const body = document.createElement("p");
    body.textContent = content;

    bubble.append(author, status, toolBubble, body);
    row.append(avatar, bubble);
    chatScroll.appendChild(row);
    scrollChatToBottom();
    return { body, status, row, toolBubble };
}

function appendMessage(role, content) {
    createMessage(role, content);
}

function setMessageThinking(messageRef, thinking) {
    if (!messageRef?.row) return;
    messageRef.row.classList.toggle("is-thinking", thinking);
}

function upsertStatusChip(container, label, content) {
    const chipId = `status-${label}`;
    let chip = container.querySelector(`[data-chip-id="${chipId}"]`);
    if (!chip) {
        chip = document.createElement("span");
        chip.className = "status-chip";
        chip.dataset.chipId = chipId;
        container.appendChild(chip);
    }
    chip.textContent = `${label}: ${content}`;
}

function updateToolBubble(messageRef, active, content = "") {
    if (!messageRef?.toolBubble) return;
    messageRef.toolBubble.classList.toggle("is-hidden", !active);
    messageRef.toolBubble.classList.toggle("is-active", active);
    messageRef.toolBubble.textContent = content;
}

function renderPathResultTable(messageRef, payload) {
    if (!messageRef?.body?.parentElement) return;

    const parent = messageRef.body.parentElement;
    let container = parent.querySelector(".path-result");
    if (!container) {
        container = document.createElement("div");
        container.className = "path-result";
        parent.insertBefore(container, messageRef.body);
    }

    container.innerHTML = "";

    const title = document.createElement("p");
    title.className = "path-result-title";
    title.textContent = `结果文件：${payload.path}`;
    container.appendChild(title);

    const rows = Array.isArray(payload.rows) ? payload.rows : [];
    if (!rows.length) {
        const empty = document.createElement("p");
        empty.className = "empty-state";
        empty.textContent = "未解析到路径结果。";
        container.appendChild(empty);
        return;
    }

    const table = document.createElement("table");
    table.className = "path-result-table";

    const thead = document.createElement("thead");
    const headRow = document.createElement("tr");
    ["GroupId", "PathId", "PathInfo"].forEach((label) => {
        const th = document.createElement("th");
        th.textContent = label;
        headRow.appendChild(th);
    });
    thead.appendChild(headRow);
    table.appendChild(thead);

    const tbody = document.createElement("tbody");
    rows.forEach((row) => {
        const tr = document.createElement("tr");
        [row.group_id, row.path_id, row.path_info].forEach((value) => {
            const td = document.createElement("td");
            td.textContent = value || "";
            tr.appendChild(td);
        });
        tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    container.appendChild(table);
}

function createSvgElement(name, attributes = {}) {
    const element = document.createElementNS(SVG_NS, name);
    Object.entries(attributes).forEach(([key, value]) => {
        element.setAttribute(key, value);
    });
    return element;
}

function renderTextListItem(text) {
    const item = document.createElement("div");
    item.className = "graph-item";
    item.textContent = text;
    return item;
}

function setList(container, items, renderItem, emptyText) {
    container.innerHTML = "";

    if (!items.length) {
        const empty = document.createElement("p");
        empty.className = "empty-state";
        empty.textContent = emptyText;
        container.appendChild(empty);
        return;
    }

    items.forEach((item) => container.appendChild(renderItem(item)));
}

function pushProgress(text) {
    const existing = Array.from(progressList.querySelectorAll(".graph-item")).map((item) => item.textContent);
    const next = [...existing, text];
    setList(progressList, next, renderTextListItem, "识别为控制流图请求后，会在这里显示生成过程。");
    graphProgress.textContent = text;
}

function resetProgress() {
    graphProgress.textContent = "等待生成草稿...";
    setList(progressList, [], renderTextListItem, "识别为控制流图请求后，会在这里显示生成过程。");
}

function renderError(error) {
    const item = document.createElement("div");
    item.className = "graph-item error-item";
    item.textContent = error;
    return item;
}

function renderSelectionLine(label, value) {
    const item = document.createElement("div");
    item.className = "inspector-line";

    const key = document.createElement("span");
    key.className = "inspector-key";
    key.textContent = label;

    const content = document.createElement("strong");
    content.textContent = value;

    item.append(key, content);
    return item;
}

function updateSelectionPanel() {
    if (graphState.graphType === "state_diagram") {
        const node = graphState.selectedNodeId
            ? (graphState.sdCurrentDiagram?.nodes || []).find(n => n.id === graphState.selectedNodeId)
            : null;
        updateSDSelectionPanel(node || null);
        return;
    }

    selectionInfo.innerHTML = "";

    if (!graphState.graph || !graphState.selectedNodeId) {
        const empty = document.createElement("p");
        empty.className = "empty-state";
        empty.textContent = "点击节点后，会在这里显示详细信息。";
        selectionInfo.appendChild(empty);
        return;
    }

    const node = graphState.graph.nodes.find((item) => item.id === graphState.selectedNodeId);
    if (!node) return;

    selectionInfo.append(
        renderSelectionLine("id", node.id),
        renderSelectionLine("type", node.type),
        renderSelectionLine("label", node.label),
    );

    if (node.type === "decision") {
        selectionInfo.appendChild(renderSelectionLine("condition", node.condition || ""));
    }

    const incoming = graphState.graph.edges.filter((edge) => edge.target === node.id);
    const outgoing = graphState.graph.edges.filter((edge) => edge.source === node.id);
    selectionInfo.append(
        renderSelectionLine("入边数", String(incoming.length)),
        renderSelectionLine("出边数", String(outgoing.length)),
    );
}

function buildLayout(graph) {
    const positions = {};
    const incomingCount = new Map();
    const childrenMap = new Map();
    const levelMap = new Map();

    graph.nodes.forEach((node) => {
        incomingCount.set(node.id, 0);
        childrenMap.set(node.id, []);
    });

    graph.edges.forEach((edge) => {
        incomingCount.set(edge.target, (incomingCount.get(edge.target) || 0) + 1);
        if (childrenMap.has(edge.source)) {
            childrenMap.get(edge.source).push(edge.target);
        }
    });

    const roots = graph.nodes.filter((node) => (incomingCount.get(node.id) || 0) === 0);
    const queue = roots.map((node) => ({ id: node.id, level: 0 }));

    const visited = new Set();
    while (queue.length) {
        const current = queue.shift();
        if (visited.has(current.id)) continue;
        visited.add(current.id);
        levelMap.set(current.id, current.level);
        const children = childrenMap.get(current.id) || [];
        children.forEach((childId) => queue.push({ id: childId, level: current.level + 1 }));
    }

    graph.nodes.forEach((node) => {
        if (!levelMap.has(node.id)) levelMap.set(node.id, 0);
    });

    const grouped = new Map();
    graph.nodes.forEach((node) => {
        const level = levelMap.get(node.id);
        if (!grouped.has(level)) grouped.set(level, []);
        grouped.get(level).push(node);
    });

    Array.from(grouped.keys()).sort((a, b) => a - b).forEach((level) => {
        const row = grouped.get(level);
        const spacingX = 250;
        const y = 90 + level * 150;
        const totalWidth = (row.length - 1) * spacingX;
        const startX = 600 - totalWidth / 2;

        row.forEach((node, index) => {
            positions[node.id] = {
                x: startX + index * spacingX,
                y,
            };
        });
    });

    return positions;
}

function getNodeSize(node) {
    if (node.type === "start" || node.type === "end") return { width: 34, height: 34 };
    if (node.type === "decision") return { width: 110, height: 110 };
    return { width: 132, height: 44 };
}

function getAnchor(position, node, direction) {
    const size = getNodeSize(node);

    if (node.type === "start" || node.type === "end") {
        const radius = size.width / 2;
        if (direction === "top") return { x: position.x, y: position.y - radius };
        if (direction === "bottom") return { x: position.x, y: position.y + radius };
        return { x: position.x, y: position.y };
    }

    if (node.type === "decision") {
        if (direction === "top") return { x: position.x, y: position.y - size.height / 2 };
        if (direction === "bottom") return { x: position.x, y: position.y + size.height / 2 };
        if (direction === "left") return { x: position.x - size.width / 2, y: position.y };
        if (direction === "right") return { x: position.x + size.width / 2, y: position.y };
    }

    if (direction === "top") return { x: position.x, y: position.y - size.height / 2 };
    if (direction === "bottom") return { x: position.x, y: position.y + size.height / 2 };
    if (direction === "left") return { x: position.x - size.width / 2, y: position.y };
    if (direction === "right") return { x: position.x + size.width / 2, y: position.y };
    return { x: position.x, y: position.y };
}

function chooseAnchors(sourcePosition, targetPosition, sourceNode, targetNode) {
    const dx = targetPosition.x - sourcePosition.x;
    const dy = targetPosition.y - sourcePosition.y;

    if (Math.abs(dx) > Math.abs(dy)) {
        return {
            source: getAnchor(sourcePosition, sourceNode, dx > 0 ? "right" : "left"),
            target: getAnchor(targetPosition, targetNode, dx > 0 ? "left" : "right"),
        };
    }

    return {
        source: getAnchor(sourcePosition, sourceNode, dy > 0 ? "bottom" : "top"),
        target: getAnchor(targetPosition, targetNode, dy > 0 ? "top" : "bottom"),
    };
}

function renderNodeShape(node, position, isSelected) {
    const group = createSvgElement("g", {
        class: `node node-${node.type}${isSelected ? " is-selected" : ""}`,
        transform: `translate(${position.x}, ${position.y})`,
        "data-node-id": node.id,
    });

    if (node.type === "start" || node.type === "end") {
        group.appendChild(createSvgElement("circle", { cx: 0, cy: 0, r: 17, class: "node-body node-circle" }));
        if (node.type === "end") {
            group.appendChild(createSvgElement("circle", { cx: 0, cy: 0, r: 11, class: "node-core" }));
        }
    } else if (node.type === "decision") {
        group.appendChild(createSvgElement("polygon", {
            points: "0,-55 55,0 0,55 -55,0",
            class: "node-body node-diamond",
        }));
    } else {
        group.appendChild(createSvgElement("rect", {
            x: -66,
            y: -22,
            width: 132,
            height: 44,
            rx: 22,
            ry: 22,
            class: "node-body node-rect",
        }));
    }

    const title = createSvgElement("text", { x: 0, y: node.type === "decision" ? -12 : 3, class: "node-title" });
    title.textContent = node.label;
    group.appendChild(title);

    if (node.type === "decision") {
        const sub = createSvgElement("text", { x: 0, y: 23, class: "node-subtitle" });
        sub.textContent = node.condition || "";
        group.appendChild(sub);
    }

    return group;
}

function renderEdges() {
    edgeLayer.innerHTML = "";
    if (!graphState.graph) return;

    graphState.graph.edges.forEach((edge) => {
        const sourceNode = graphState.graph.nodes.find((node) => node.id === edge.source);
        const targetNode = graphState.graph.nodes.find((node) => node.id === edge.target);
        const sourcePosition = graphState.positions[edge.source];
        const targetPosition = graphState.positions[edge.target];
        if (!sourceNode || !targetNode || !sourcePosition || !targetPosition) return;

        const anchors = chooseAnchors(sourcePosition, targetPosition, sourceNode, targetNode);
        edgeLayer.appendChild(createSvgElement("path", {
            d: `M ${anchors.source.x} ${anchors.source.y} L ${anchors.target.x} ${anchors.target.y}`,
            class: "edge-path",
            "marker-end": "url(#arrowHead)",
        }));

        const labelX = (anchors.source.x + anchors.target.x) / 2;
        const labelY = (anchors.source.y + anchors.target.y) / 2;

        const edgeName = createSvgElement("text", { x: labelX, y: labelY - 14, class: "edge-name" });
        edgeName.textContent = edge.id;
        edgeLayer.appendChild(edgeName);

        if (edge.label) {
            const branch = createSvgElement("text", { x: labelX, y: labelY + 14, class: "edge-label" });
            branch.textContent = edge.label;
            edgeLayer.appendChild(branch);
        }
    });
}

function renderNodes() {
    nodeLayer.innerHTML = "";
    if (!graphState.graph) return;

    graphState.graph.nodes.forEach((node) => {
        const position = graphState.positions[node.id];
        if (!position) return;

        const shape = renderNodeShape(node, position, graphState.selectedNodeId === node.id);
        shape.addEventListener("pointerdown", (event) => startNodeDrag(event, node.id));
        shape.addEventListener("click", () => {
            graphState.selectedNodeId = node.id;
            updateSelectionPanel();
            renderGraph();
        });
        nodeLayer.appendChild(shape);
    });
}

function renderGraph() {
    if (graphState.graphType === "state_diagram") {
        renderSDGraph();
        return;
    }
    renderEdges();
    renderNodes();
    graphPanLayer.setAttribute("transform", `translate(${graphState.panX} ${graphState.panY})`);
    graphZoomLayer.setAttribute("transform", `scale(${graphState.zoom})`);
    graphCanvas.classList.toggle("is-panning", graphState.isPanning);
    zoomLabel.textContent = `${Math.round(graphState.zoom * 100)}%`;
}

function getViewportPoint(clientX, clientY) {
    const point = graphCanvas.createSVGPoint();
    point.x = clientX;
    point.y = clientY;
    return point.matrixTransform(graphZoomLayer.getScreenCTM().inverse());
}

function startNodeDrag(event, nodeId) {
    event.preventDefault();
    event.stopPropagation();
    const point = getViewportPoint(event.clientX, event.clientY);
    const current = graphState.positions[nodeId];
    graphState.draggingNodeId = nodeId;
    graphState.selectedNodeId = nodeId;
    graphState.dragOffset = { x: point.x - current.x, y: point.y - current.y };
    updateSelectionPanel();
    renderGraph();
}

function startPan(event) {
    if (!graphState.graph) return;
    if (event.target.closest("[data-node-id]")) return;
    graphState.isPanning = true;
    graphState.panStart = { x: event.clientX, y: event.clientY };
    graphState.panOrigin = { x: graphState.panX, y: graphState.panY };
    graphCanvas.setPointerCapture?.(event.pointerId);
    renderGraph();
}

function onPointerMove(event) {
    if (graphState.draggingNodeId) {
        const point = getViewportPoint(event.clientX, event.clientY);
        const newPos = {
            x: point.x - graphState.dragOffset.x,
            y: point.y - graphState.dragOffset.y,
        };
        if (graphState.graphType === "state_diagram") {
            graphState.sdPositions[graphState.draggingNodeId] = newPos;
        } else {
            graphState.positions[graphState.draggingNodeId] = newPos;
        }
        renderGraph();
        return;
    }

    if (!graphState.isPanning) return;
    const dx = event.clientX - graphState.panStart.x;
    const dy = event.clientY - graphState.panStart.y;
    graphState.panX = graphState.panOrigin.x + dx;
    graphState.panY = graphState.panOrigin.y + dy;
    renderGraph();
}

function stopGraphInteraction(event) {
    graphState.draggingNodeId = null;
    graphState.isPanning = false;
    if (event?.pointerId !== undefined && graphCanvas.hasPointerCapture?.(event.pointerId)) {
        graphCanvas.releasePointerCapture(event.pointerId);
    }
    syncGraphPayloadPositions();
    refreshJsonPreview();
    renderGraph();
}

function updateGraphPanel(result) {
    const { graph, valid, errors, repair_rounds, repair_strategy } = result;
    const previousPositions = repair_strategy === "fallback" ? {} : clonePositions(graphState.positions);
    graphState.graph = graph;
    graphState.positions = mergePositions(graph, previousPositions);
    graphState.selectedNodeId = graph.nodes[0]?.id || null;
    graphState.zoom = 1;
    graphState.panX = 0;
    graphState.panY = 0;
    graphState.isPanning = false;
    graphState.draggingNodeId = null;
    syncGraphPayloadPositions();

    showGraphPanel();
    graphTitle.textContent = graph.title || "未命名流程";
    graphStatus.textContent = valid ? "校验通过" : "校验失败";
    graphStatus.classList.toggle("is-valid", valid);
    graphStatus.classList.toggle("is-invalid", !valid);
    modeBadge.textContent = "当前模式：控制流图";
    refreshJsonPreview();

    const finalErrors = errors && errors.length ? [...errors] : ["没有发现结构错误。"];
    if (repair_rounds) finalErrors.unshift(`自动修复轮次：${repair_rounds}`);
    setList(errorList, finalErrors, renderError, "没有发现结构错误。");
    graphProgress.textContent = valid ? "草稿已生成并通过校验。" : "草稿已生成，但仍有待处理问题。";

    updateSelectionPanel();
    renderGraph();
    updateGraphReopenButton();
}

function changeZoom(nextZoom) {
    graphState.zoom = Math.min(2.2, Math.max(0.5, nextZoom));
    renderGraph();
}

function startResize(event) {
    if (!layoutState.isGraphVisible || window.innerWidth <= 1320) return;
    layoutState.isResizing = true;
    workspace.classList.add("is-resizing");
    event.preventDefault();
}

function onResizeMove(event) {
    if (!layoutState.isResizing) return;
    const rect = workspace.getBoundingClientRect();
    const width = ((event.clientX - rect.left) / rect.width) * 100;
    layoutState.chatWidthPercent = Math.min(72, Math.max(28, width));
    applyWorkspaceLayout();
}

function stopResize() {
    layoutState.isResizing = false;
    workspace.classList.remove("is-resizing");
}

async function streamMessage(message, assistantMessage) {
    if (activeChatAbortController) {
        activeChatAbortController.abort();
    }
    const abortController = new AbortController();
    activeChatAbortController = abortController;

    const response = await fetch("/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        signal: abortController.signal,
        body: JSON.stringify({
            message,
            current_graph: serializeGraphForModel(),
            thread_id: workflowThreadId,
        }),
    });

    if (!response.ok) {
        const errorText = await response.text();
        throw new Error(errorText || "请求失败");
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder("utf-8");
    let buffer = "";
    let fullText = "";
    setMessageThinking(assistantMessage, true);

    while (true) {
        const { value, done } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() || "";

        for (const line of lines) {
            if (!line.trim()) continue;
            const event = JSON.parse(line);

            if (event.type === "mode") {
                if (event.mode === "graph") {
                    modeBadge.textContent = "当前模式：控制流图";
                    showGraphPanel();
                    graphTitle.textContent = graphState.graph ? "正在修改草稿" : "正在生成草稿";
                    graphStatus.textContent = "生成中";
                    graphStatus.classList.remove("is-valid", "is-invalid");
                    jsonPreview.textContent = graphState.graph
                        ? JSON.stringify(serializeCurrentGraph(), null, 2)
                        : "等待生成...";
                    resetProgress();
                    pushProgress(graphState.graph ? "已识别为基于当前草稿的修改请求。" : "已识别为控制流图生成请求。");
                } else if (event.mode === "state_diagram") {
                    modeBadge.textContent = "当前模式：状态图";
                    showGraphPanel();
                    graphTitle.textContent = graphState.sdDiagram ? "正在修改状态图" : "正在生成状态图";
                    graphStatus.textContent = "生成中";
                    graphStatus.classList.remove("is-valid", "is-invalid");
                    resetProgress();
                    pushProgress(graphState.sdDiagram ? "已识别为状态图修改请求。" : "已识别为状态图生成请求。");
                } else {
                    if (!graphState.graph) {
                        hideGraphPanel();
                    }
                    clearPendingEdit();
                    modeBadge.textContent = "当前模式：普通聊天";
                }
            } else if (event.type === "activity") {
                upsertStatusChip(assistantMessage.status, event.label, event.content);
            } else if (event.type === "status") {
                fullText += `${fullText ? "\n" : ""}${event.content}`;
                assistantMessage.body.textContent = fullText;
                if (layoutState.isGraphVisible) {
                    pushProgress(event.content);
                }
            } else if (event.type === "delta") {
                fullText += event.content;
                assistantMessage.body.textContent = fullText;
                if (layoutState.isGraphVisible && event.content.trim()) {
                    graphProgress.textContent = event.content;
                }
            } else if (event.type === "llm_token") {
                fullText += event.content;
                assistantMessage.body.textContent = fullText;
            } else if (event.type === "tool_stream") {
                if (event.phase === "start") {
                    updateToolBubble(assistantMessage, true, event.content || event.name || "工具执行中");
                } else if (event.phase === "end") {
                    updateToolBubble(assistantMessage, false, "");
                } else if (event.content) {
                    updateToolBubble(assistantMessage, true, event.content);
                }
            } else if (event.type === "node_update") {
                const keys = Object.keys(event.payload || {});
                if (keys.length) {
                    upsertStatusChip(assistantMessage.status, "Node", keys.join(", "));
                }
            } else if (event.type === "graph_edit_preview") {
                setPendingEdit(event.payload?.instruction || "");
                graphProgress.textContent = "已生成修改理解确认，等待你确认。";
            } else if (event.type === "interrupt") {
                upsertStatusChip(assistantMessage.status, "Interrupt", `命中 ${event.count || 1} 个中断点`);
            } else if (event.type === "graph_result") {
                clearPendingEdit();
                updateGraphPanel(event.payload);
            } else if (event.type === "state_diagram_result") {
                clearPendingEdit();
                updateStateDiagramPanel(event.payload);
            } else if (event.type === "path_result") {
                renderPathResultTable(assistantMessage, event.payload || {});
            } else if (event.type === "error") {
                throw new Error(event.content || "流式响应出错");
            } else if (event.type === "done") {
                updateToolBubble(assistantMessage, false, "");
                setMessageThinking(assistantMessage, false);
            }

            scrollChatToBottom();
        }
    }

    updateToolBubble(assistantMessage, false, "");
    setMessageThinking(assistantMessage, false);
    if (activeChatAbortController === abortController) {
        activeChatAbortController = null;
    }
}

// ── State Diagram Rendering ───────────────────────────────────────────────────

function buildStateDiagramLayout(diagram) {
    const positions = {};
    const nodes = diagram.nodes || [];
    const transitions = diagram.transitions || [];
    if (!nodes.length) return positions;

    const incomingCount = new Map(nodes.map(n => [n.id, 0]));
    const childrenMap = new Map(nodes.map(n => [n.id, []]));
    transitions.forEach(t => {
        incomingCount.set(t.target, (incomingCount.get(t.target) || 0) + 1);
        if (childrenMap.has(t.source)) childrenMap.get(t.source).push(t.target);
    });

    const roots = nodes.filter(n => (incomingCount.get(n.id) || 0) === 0);
    const queue = (roots.length ? roots : nodes.slice(0, 1)).map(n => ({ id: n.id, level: 0 }));
    const levelMap = new Map();
    const visited = new Set();
    while (queue.length) {
        const cur = queue.shift();
        if (visited.has(cur.id)) continue;
        visited.add(cur.id);
        levelMap.set(cur.id, cur.level);
        (childrenMap.get(cur.id) || []).forEach(childId => queue.push({ id: childId, level: cur.level + 1 }));
    }
    nodes.forEach(n => { if (!levelMap.has(n.id)) levelMap.set(n.id, 0); });

    const grouped = new Map();
    nodes.forEach(n => {
        const level = levelMap.get(n.id);
        if (!grouped.has(level)) grouped.set(level, []);
        grouped.get(level).push(n);
    });

    Array.from(grouped.keys()).sort((a, b) => a - b).forEach(level => {
        const row = grouped.get(level);
        const spacingX = 210;
        const y = 90 + level * 175;
        const totalWidth = (row.length - 1) * spacingX;
        const startX = 600 - totalWidth / 2;
        row.forEach((n, i) => { positions[n.id] = { x: startX + i * spacingX, y }; });
    });
    return positions;
}

function getSDNodeSize(node) {
    switch (node.type) {
        case "initial": return { width: 20, height: 20 };
        case "final": return { width: 28, height: 28 };
        case "fork": case "join": return { width: 80, height: 8 };
        case "sequential_composite": case "concurrent_composite": return { width: 160, height: 72 };
        default: return { width: 140, height: 52 };
    }
}

function getSDNodeAnchor(pos, node, dir) {
    const s = getSDNodeSize(node);
    const hw = s.width / 2, hh = s.height / 2;
    if (dir === "top") return { x: pos.x, y: pos.y - hh };
    if (dir === "bottom") return { x: pos.x, y: pos.y + hh };
    if (dir === "left") return { x: pos.x - hw, y: pos.y };
    return { x: pos.x + hw, y: pos.y };
}

function chooseSDAnchors(srcPos, tgtPos, srcNode, tgtNode) {
    const dx = tgtPos.x - srcPos.x, dy = tgtPos.y - srcPos.y;
    if (Math.abs(dy) >= Math.abs(dx)) {
        return {
            source: getSDNodeAnchor(srcPos, srcNode, dy > 0 ? "bottom" : "top"),
            target: getSDNodeAnchor(tgtPos, tgtNode, dy > 0 ? "top" : "bottom"),
        };
    }
    return {
        source: getSDNodeAnchor(srcPos, srcNode, dx > 0 ? "right" : "left"),
        target: getSDNodeAnchor(tgtPos, tgtNode, dx > 0 ? "left" : "right"),
    };
}

function renderSDNodeShape(node, pos, isSelected) {
    const g = createSvgElement("g", {
        "data-node-id": node.id,
        transform: `translate(${pos.x},${pos.y})`,
    });
    const s = getSDNodeSize(node);
    const hw = s.width / 2, hh = s.height / 2;

    if (node.type === "initial") {
        g.setAttribute("class", `sd-node-initial${isSelected ? " selected" : ""}`);
        g.appendChild(createSvgElement("circle", { cx: 0, cy: 0, r: 10 }));
    } else if (node.type === "final") {
        g.setAttribute("class", `sd-node-final${isSelected ? " selected" : ""}`);
        g.appendChild(createSvgElement("circle", { class: "outer-circle", cx: 0, cy: 0, r: 14 }));
        g.appendChild(createSvgElement("circle", { class: "inner-circle", cx: 0, cy: 0, r: 8 }));
    } else if (node.type === "fork") {
        g.setAttribute("class", `sd-node-fork${isSelected ? " selected" : ""}`);
        g.appendChild(createSvgElement("rect", { x: -hw, y: -hh, width: s.width, height: s.height }));
    } else if (node.type === "join") {
        g.setAttribute("class", `sd-node-join${isSelected ? " selected" : ""}`);
        g.appendChild(createSvgElement("rect", { x: -hw, y: -hh, width: s.width, height: s.height }));
    } else if (node.type === "sequential_composite") {
        g.setAttribute("class", `sd-node-sequential${isSelected ? " selected" : ""}`);
        g.appendChild(createSvgElement("rect", { class: "outer-rect", x: -hw, y: -hh, width: s.width, height: s.height, rx: 10, ry: 10 }));
        g.appendChild(createSvgElement("rect", { class: "inner-rect", x: -hw + 4, y: -hh + 4, width: s.width - 8, height: s.height - 8, rx: 7, ry: 7 }));
        const lbl = createSvgElement("text", { class: "sd-node-label", x: 0, y: -8 });
        lbl.textContent = node.label;
        g.appendChild(lbl);
        const hint = createSvgElement("text", { class: "sd-enter-hint", x: 0, y: 14 });
        hint.textContent = node.sub_diagram ? "双击进入子图" : "（无子图）";
        g.appendChild(hint);
    } else if (node.type === "concurrent_composite") {
        g.setAttribute("class", `sd-node-concurrent${isSelected ? " selected" : ""}`);
        g.appendChild(createSvgElement("rect", { class: "outer-rect", x: -hw, y: -hh, width: s.width, height: s.height, rx: 10, ry: 10 }));
        g.appendChild(createSvgElement("rect", { class: "inner-rect", x: -hw + 4, y: -hh + 4, width: s.width - 8, height: s.height - 8, rx: 7, ry: 7 }));
        const lbl = createSvgElement("text", { class: "sd-node-label", x: 0, y: -8 });
        lbl.textContent = node.label;
        g.appendChild(lbl);
        const hint = createSvgElement("text", { class: "sd-enter-hint", x: 0, y: 14 });
        hint.textContent = node.sub_diagram ? "双击进入子图" : "（无子图）";
        g.appendChild(hint);
    } else {
        g.setAttribute("class", `sd-node-simple${isSelected ? " selected" : ""}`);
        g.appendChild(createSvgElement("rect", { x: -hw, y: -hh, width: s.width, height: s.height, rx: 8, ry: 8 }));
        const lbl = createSvgElement("text", { class: "sd-node-label", x: 0, y: 0 });
        lbl.textContent = node.label;
        g.appendChild(lbl);
        const actions = [
            ...(node.entry_actions || []).map(a => `entry: ${a}`),
            ...(node.do_actions || []).map(a => `do: ${a}`),
            ...(node.exit_actions || []).map(a => `exit: ${a}`),
        ];
        if (actions.length) {
            const aLbl = createSvgElement("text", { class: "sd-action-label", x: 0, y: 20 });
            aLbl.textContent = actions.slice(0, 2).join(", ");
            g.appendChild(aLbl);
        }
    }
    return g;
}

function renderStateDiagramEdges(diagram, positions) {
    edgeLayer.innerHTML = "";
    const nodes = diagram.nodes || [];
    (diagram.transitions || []).forEach(t => {
        const srcNode = nodes.find(n => n.id === t.source);
        const tgtNode = nodes.find(n => n.id === t.target);
        const srcPos = positions[t.source];
        const tgtPos = positions[t.target];
        if (!srcNode || !tgtNode || !srcPos || !tgtPos) return;

        const anchors = chooseSDAnchors(srcPos, tgtPos, srcNode, tgtNode);
        edgeLayer.appendChild(createSvgElement("path", {
            d: `M ${anchors.source.x} ${anchors.source.y} L ${anchors.target.x} ${anchors.target.y}`,
            class: "edge-path",
            "marker-end": "url(#arrowHead)",
        }));

        if (t.event) {
            const mx = (anchors.source.x + anchors.target.x) / 2;
            const my = (anchors.source.y + anchors.target.y) / 2;
            const parts = [t.event.event_id || "", t.event.condition ? `[${t.event.condition}]` : ""].filter(Boolean);
            if (parts.length) {
                const evLabel = createSvgElement("text", { x: mx, y: my - 8, class: "sd-edge-label" });
                evLabel.textContent = parts.join(" ");
                edgeLayer.appendChild(evLabel);
            }
        }
    });
}

function renderStateDiagramNodes(diagram, positions) {
    nodeLayer.innerHTML = "";
    (diagram.nodes || []).forEach(node => {
        const pos = positions[node.id];
        if (!pos) return;

        const shape = renderSDNodeShape(node, pos, graphState.selectedNodeId === node.id);
        shape.addEventListener("pointerdown", e => startSDNodeDrag(e, node.id));
        shape.addEventListener("click", e => {
            e.stopPropagation();
            graphState.selectedNodeId = node.id;
            updateSDSelectionPanel(node);
            renderSDGraph();
        });
        shape.addEventListener("dblclick", e => {
            e.stopPropagation();
            if ((node.type === "sequential_composite" || node.type === "concurrent_composite") && node.sub_diagram) {
                enterSubDiagram(node, diagram, positions);
            }
        });
        nodeLayer.appendChild(shape);
    });
}

function renderSDGraph() {
    const diagram = graphState.sdCurrentDiagram;
    const positions = graphState.sdPositions;
    if (!diagram) { edgeLayer.innerHTML = ""; nodeLayer.innerHTML = ""; return; }
    renderStateDiagramEdges(diagram, positions);
    renderStateDiagramNodes(diagram, positions);
    graphPanLayer.setAttribute("transform", `translate(${graphState.panX} ${graphState.panY})`);
    graphZoomLayer.setAttribute("transform", `scale(${graphState.zoom})`);
    graphCanvas.classList.toggle("is-panning", graphState.isPanning);
    zoomLabel.textContent = `${Math.round(graphState.zoom * 100)}%`;
}

function startSDNodeDrag(event, nodeId) {
    event.preventDefault();
    event.stopPropagation();
    const point = getViewportPoint(event.clientX, event.clientY);
    const current = graphState.sdPositions[nodeId];
    if (!current) return;
    graphState.draggingNodeId = nodeId;
    graphState.selectedNodeId = nodeId;
    graphState.dragOffset = { x: point.x - current.x, y: point.y - current.y };
}

function enterSubDiagram(node, parentDiagram, parentPositions) {
    graphState.sdNavStack.push({ node, diagram: parentDiagram, positions: { ...parentPositions } });
    graphState.sdCurrentDiagram = node.sub_diagram;
    graphState.sdPositions = buildStateDiagramLayout(node.sub_diagram);
    graphState.selectedNodeId = null;
    graphState.panX = 0; graphState.panY = 0; graphState.zoom = 1;
    updateBreadcrumb();
    renderSDGraph();
    updateSDSelectionPanel(null);
}

function exitSubDiagram() {
    if (!graphState.sdNavStack.length) return;
    const frame = graphState.sdNavStack.pop();
    graphState.sdCurrentDiagram = frame.diagram;
    graphState.sdPositions = frame.positions;
    graphState.selectedNodeId = frame.node.id;
    graphState.panX = 0; graphState.panY = 0; graphState.zoom = 1;
    updateBreadcrumb();
    renderSDGraph();
    updateSDSelectionPanel(frame.node);
}

function updateBreadcrumb() {
    const inSub = graphState.sdNavStack.length > 0;
    sdNavBar.classList.toggle("is-hidden", !inSub);
    if (inSub) {
        const crumbs = graphState.sdNavStack.map(f => f.node.label);
        crumbs.push(graphState.sdCurrentDiagram?.title || "子图");
        sdBreadcrumb.textContent = crumbs.join(" › ");
    }
}

function updateSDSelectionPanel(node) {
    selectionInfo.innerHTML = "";
    if (!node) {
        const empty = document.createElement("p");
        empty.className = "empty-state";
        empty.textContent = "点击节点后，会在这里显示详细信息。";
        selectionInfo.appendChild(empty);
        return;
    }
    selectionInfo.append(
        renderSelectionLine("id", node.id),
        renderSelectionLine("type", node.type),
        renderSelectionLine("label", node.label),
    );
    if (node.entry_actions?.length) selectionInfo.appendChild(renderSelectionLine("entry", node.entry_actions.join(", ")));
    if (node.do_actions?.length) selectionInfo.appendChild(renderSelectionLine("do", node.do_actions.join(", ")));
    if (node.exit_actions?.length) selectionInfo.appendChild(renderSelectionLine("exit", node.exit_actions.join(", ")));
    if ((node.type === "sequential_composite" || node.type === "concurrent_composite") && node.sub_diagram) {
        const hint = document.createElement("p");
        hint.style.cssText = "font-size:12px;color:#888;margin-top:6px;";
        hint.textContent = "双击节点可进入子图";
        selectionInfo.appendChild(hint);
    }
}

function updateStateDiagramPanel(result) {
    const { diagram, errors, valid } = result;
    graphState.graphType = "state_diagram";
    graphState.sdDiagram = diagram;
    graphState.sdCurrentDiagram = diagram;
    graphState.sdNavStack = [];
    graphState.sdPositions = buildStateDiagramLayout(diagram);
    graphState.selectedNodeId = null;
    graphState.panX = 0; graphState.panY = 0; graphState.zoom = 1;
    graphState.isPanning = false; graphState.draggingNodeId = null;
    graphState.graph = diagram;

    showGraphPanel();
    graphTitle.textContent = diagram.title || "未命名状态图";
    graphStatus.textContent = valid ? "校验通过" : "校验失败";
    graphStatus.classList.toggle("is-valid", valid);
    graphStatus.classList.toggle("is-invalid", !valid);
    modeBadge.textContent = "当前模式：状态图";

    const finalErrors = errors && errors.length ? [...errors] : ["没有发现结构错误。"];
    setList(errorList, finalErrors, renderError, "没有发现结构错误。");
    graphProgress.textContent = valid ? "状态图已生成并通过校验。" : "状态图已生成，但仍有待处理问题。";
    jsonPreview.textContent = JSON.stringify(diagram, null, 2);
    updateBreadcrumb();
    updateSDSelectionPanel(null);
    renderSDGraph();
    updateGraphReopenButton();
}

async function exportXml() {
    if (!graphState.graph) return;

    const response = await fetch("/graph/export-xml", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ graph: graphState.graph, positions: graphState.positions }),
    });

    if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        const details = Array.isArray(payload.details) ? `\n${payload.details.join("\n")}` : "";
        throw new Error((payload.error || "XML 导出失败") + details);
    }

    return response.json();
}

async function exportControl() {
    if (!graphState.graph) return;

    const response = await fetch("/graph/export-control", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ graph: graphState.graph, positions: graphState.positions }),
    });

    if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        const details = Array.isArray(payload.details) ? `\n${payload.details.join("\n")}` : "";
        throw new Error((payload.error || ".control 导出失败") + details);
    }

    return response.json();
}

composerForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const value = promptInput.value.trim();
    if (!value) return;

    appendMessage("user", value);
    promptInput.value = "";
    autoResize();

    const assistantMessage = createMessage("ai", "");
    try {
        await streamMessage(value, assistantMessage);
    } catch (error) {
        if (error.name === "AbortError") {
            setMessageThinking(assistantMessage, false);
            assistantMessage.body.textContent = "当前流程已中断，正在处理新的请求。";
            return;
        }
        setMessageThinking(assistantMessage, false);
        assistantMessage.body.textContent = `请求出错：${error.message}`;
    }
});

confirmEditButton.addEventListener("click", async () => {
    if (!hasPendingEdit()) return;

    const confirmText = "确认修改";
    appendMessage("user", confirmText);

    const assistantMessage = createMessage("ai", "");
    try {
        await streamMessage(confirmText, assistantMessage);
    } catch (error) {
        if (error.name === "AbortError") {
            setMessageThinking(assistantMessage, false);
            assistantMessage.body.textContent = "当前流程已中断，正在处理新的请求。";
            return;
        }
        setMessageThinking(assistantMessage, false);
        assistantMessage.body.textContent = `请求出错：${error.message}`;
    }
});

promptInput.addEventListener("input", autoResize);
promptInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        composerForm.requestSubmit();
    }
});

sidebarToggleButton.addEventListener("click", () => {
    layoutState.isSidebarCollapsed = !layoutState.isSidebarCollapsed;
    applyWorkspaceLayout();
});

closeGraphPanelButton.addEventListener("click", hideGraphPanel);
reopenGraphPanelButton.addEventListener("click", showGraphPanel);
sdBackButton.addEventListener("click", exitSubDiagram);
workspaceResizer.addEventListener("pointerdown", startResize);
window.addEventListener("pointermove", onResizeMove);
window.addEventListener("pointerup", stopResize);
graphCanvas.addEventListener("pointerdown", startPan);
graphCanvas.addEventListener("pointermove", onPointerMove);
graphCanvas.addEventListener("pointerup", stopGraphInteraction);
graphCanvas.addEventListener("pointerleave", stopGraphInteraction);
graphCanvas.addEventListener("wheel", (event) => {
    event.preventDefault();
    changeZoom(graphState.zoom + (event.deltaY < 0 ? 0.1 : -0.1));
}, { passive: false });
window.addEventListener("pointerup", stopGraphInteraction);
window.addEventListener("resize", () => {
    applyWorkspaceLayout();
    renderGraph();
});
zoomInButton.addEventListener("click", () => changeZoom(graphState.zoom + 0.1));
zoomOutButton.addEventListener("click", () => changeZoom(graphState.zoom - 0.1));
zoomResetButton.addEventListener("click", () => {
    graphState.zoom = 1;
    graphState.panX = 0;
    graphState.panY = 0;
    renderGraph();
});
exportXmlButton.addEventListener("click", async () => {
    try {
        const result = await exportXml();
        graphProgress.textContent = `XML 已导出到：${result.path}`;
    } catch (error) {
        graphProgress.textContent = `XML 导出失败：${error.message}`;
    }
});
exportControlButton.addEventListener("click", async () => {
    try {
        const result = await exportControl();
        graphProgress.textContent = `.control 已导出到：${result.path}`;
    } catch (error) {
        graphProgress.textContent = `.control 导出失败：${error.message}`;
    }
});

applyWorkspaceLayout();
updateConfirmEditButton();
resetProgress();
autoResize();
renderGraph();
scrollChatToBottom();
