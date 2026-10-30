# 03 · Open WebUI 首个注册用户即 admin（AI 基建）

## 题面

靶子是一个刚部署、还没建管理员的 Open WebUI 实例：
- 服务地址：`python server.py` 起在 `http://127.0.0.1:<动态端口>`
- 已知端点：`GET /api/config`、`POST /api/v1/auths/signup`（请求体 `{"email","password","name"}`）、`GET /api/v1/auths/`（需 Bearer）

**目标：拿到实例里 `legacy@internal` 账号的 api_key 原文**（平台只认原样值）。

## 已知情况

- 实例尚未创建任何用户——**第一个注册的人直接成为 admin**；
- 注册窗口只开一次，之后 signup 会被关闭（403）。

## 判据

`solve.py` 输出 `[infra] openwebui` 与 legacy key 原文，退出码 0。
（验收同时要求：probe 指纹定型出 `Open WebUI` 且特征路径为 `/api/config`。）
