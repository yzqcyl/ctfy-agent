# 02 · ComfyUI 工作流参数路径穿越（AI 基建）

## 题面

靶子是一个暴露的 ComfyUI 服务：
- 服务地址：`python server.py` 起在 `http://127.0.0.1:<动态端口>`
- 已知端点：`GET /`（前端页）、`GET /system_stats`、`POST /prompt`（提交工作流，请求体 `{"prompt": {<node_id>: {class_type, inputs}}}`）

**目标：`/srv/comfyui/secret_note.txt` 里的 token 原文**（平台只认原样值）。

## 已知情况

- 模型加载参数（`ckpt_name` / `lora_name`）直接拼进服务端路径，未做目录约束；
- 正常参数（如 `sd3.5.safetensors`）都在模型目录内，`../` 序列会越出。

## 判据

`solve.py` 输出 `[infra] comfyui` 与 token 原文，退出码 0。
（验收同时要求：probe 指纹定型出 `ComfyUI` 且版本提取为 `0.6.1`。）
