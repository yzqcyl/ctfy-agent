# 01 · travel-tools 网关（mcp03 · 工具描述投毒）

## 题面

靶子是一个 MCP 网关 travel-tools：
- 服务地址：`python server.py` 起在 `http://127.0.0.1:<动态端口>`
- MCP 端点：`POST /mcp`（JSON-RPC 2.0；`tools/list` 枚举工具，`tools/call` 调用）
- 网关同源下还有一个 `GET /internal/config`

**目标：拿到 operator token 原文**（平台只认原样值），并证明它有效（带它完成一次成功调用）。

## 已知情况

- 网关的工具描述是给「调用方智能体」看的系统文本，终端用户看不到；
- 某个工具的描述里混着一段内部运维指引；
- `/internal/config` 没有鉴权。

## 判据

`solve.py` 输出 `[mcp] mcp03` 与 token 原文，退出码 0。
（验收同时要求：描述里的投毒指引被识别、且 token 通过实际调用坐实——不许猜值。）
