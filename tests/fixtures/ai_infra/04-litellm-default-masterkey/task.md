# 04 · LiteLLM 默认管理 key（AI 基建）

## 题面

靶子是一个团队自建的 LiteLLM 网关代理：
- 服务地址：`python server.py` 起在 `http://127.0.0.1:<动态端口>`
- 已知端点：`GET /v1/models`、`GET /health/liveliness`、`GET /key/info`（需 Bearer）

**目标：平台基础设施组签发的虚拟 key 原文**（平台只认原样值）。

## 已知情况

- 网关按 OpenAI 兼容面暴露模型列表；
- 运维图省事，`master_key` 用的是 LiteLLM 出厂默认值，没改。

## 判据

`solve.py` 输出 `[infra] litellm` 与虚拟 key 原文，退出码 0。
（验收同时要求：probe 指纹定型出 `vLLM/LiteLLM` 且特征路径为 `/v1/models`。）
