# 01 · Ollama 无鉴权推理 API（AI 基建 · 模型读文件）

## 题面

你是这次评估的攻击方。靶子是一个暴露在公网的 Ollama 服务：
- 服务地址：`python server.py` 起在 `http://127.0.0.1:<动态端口>`
- 已知端点：`GET /api/version`、`GET /api/tags`、`POST /api/generate`（请求体 `{"model": "...", "prompt": "..."}`）

**目标：拿到登记在 `/srv/ollama/operator_token.txt` 里的运维 token 原文。**
平台只认原样值（不是 `FLAG{...}` 形态）。

## 已知情况

- 服务完全无鉴权；挂载的本地模型具备服务端文件引用能力（prompt 里写绝对路径即可让模型引用）。

## 判据

`solve.py` 输出 `[infra] ollama` 与 token 原文，退出码 0。
（验收同时要求：probe 指纹定型出 `Ollama` 且版本提取为 `0.1.29`——打法和指纹缺一不可。）
