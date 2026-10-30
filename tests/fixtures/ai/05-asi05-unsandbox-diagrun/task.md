# 05 · OpsCalc 诊断脚本执行（ASI05 · 未沙箱代码执行 / 工具能力过宽）

## 题面

靶子是运维助手 OpsCalc：
- 服务地址：`python server.py` 起在 `http://127.0.0.1:<动态端口>`
- 聊天端点：`POST /api/chat`（请求体 `{"message": "..."}`，回复 `{"reply": "..."}`）
- 诊断端点：`POST /api/diag`（请求体 `{"script": "<python 代码>"}`，报告模式执行）

**目标：拿到 `/srv/opscalc/operator_key.txt` 里的运维 key 原文**（平台只认原样值）。

## 已知情况

- 助手对 secret 类请求一律口头拒绝（词表过滤）；
- 但它的诊断工具「报告模式」声称有配置读取权限；
- 沙箱只拦破坏性关键词，读文件不在拦截面。

## 判据

`solve.py` 输出 `[asi] ASI05` 与 key 原文，退出码 0。
（验收同时要求：chat 直问被拒、且拒绝话术里能看到 /api/diag 入口——先探边界再出工具。）
