# TPCTF 2026 智能体赛道 agent 镜像（CTFY-0001 v2 用户镜像契约）
#
# 契约：平台以 `docker create -w /ctfy/work -e CTFY_WORK_DIR=/ctfy/work ... <image>` 启动，
# 后面不跟任何 command/args → entrypoint 必须由镜像自带，且 cwd 被强制成 /ctfy/work，
# 所以不能用相对路径脚本，只能 `python -m ctfy_agent agent` + PYTHONPATH。

FROM python:3.12-slim

# grep 不在 coreutils 里、是独立 Debian 包；curl + ca-certificates 供 https 探测；
# bash 供 web 角色的 shell 工具（模型会用它跑探测，缺了 shell 16/16 全灭）。
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        curl grep coreutils ca-certificates bash \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    PYTHONUTF8=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/ctfy/src

# 瘦身（主办方：镜像太大传不上去）。运行时只用 stdlib，下列都是死重量（约 20MB）：
#   pip/setuptools/wheel/ensurepip（不安装依赖）、test 模块、idlelib/lib2to3/pydoc_data/turtledemo。
# 另：.dockerignore 已排除 tests/docs/runs/__pycache__，ENV 里禁了运行时写 .pyc。
RUN rm -rf /usr/local/lib/python3.12/ensurepip \
           /usr/local/lib/python3.12/test \
           /usr/local/lib/python3.12/idlelib \
           /usr/local/lib/python3.12/lib2to3 \
           /usr/local/lib/python3.12/pydoc_data \
           /usr/local/lib/python3.12/turtledemo \
           /usr/local/lib/python3.12/tkinter \
           /usr/local/lib/python3.12/site-packages/pip* \
           /usr/local/lib/python3.12/site-packages/setuptools* \
           /usr/local/lib/python3.12/site-packages/wheel* \
           /usr/local/bin/pip*


WORKDIR /ctfy

# stdlib-only：只拷源码 + 角色 profile（web allow_shell / 各角色 system 提示词等；无凭据）
COPY src/ /ctfy/src/
COPY config/agent.json /ctfy/config/agent.json

# 绝对入口 + PYTHONPATH（cwd=/ctfy/work 时模块仍可导入）；task.json 路径默认取 $CTFY_WORK_DIR
ENTRYPOINT ["python", "-m", "ctfy_agent", "agent"]
