# 官方 VPS 形态的冒烟镜像：debian:bookworm-slim，无 root，模拟封网（无 pip/apt 网络）。
#
# 构建/运行（在能跑 Docker 的机器上）：
#   docker build -f tools/linux_bootstrap/Dockerfile -t tpctf-agent-smoke .
#   docker run --rm --network none tpctf-agent-smoke
#
# 注：--network none 模拟封网；只验证「环境自举 + 工具链离线解包 + 导入冒烟」，
# 不连平台（需要网关变量与令牌）。
FROM debian:bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-venv ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# 模拟官方 VPS：非 root 用户
RUN useradd --uid 10001 --create-home agent
WORKDIR /home/agent

COPY . /home/agent/repo
# 离线 bundle（赛前在有网机器上生成；镜像构建时若不存在则跳过）
COPY tools/linux_bootstrap/bundle /home/agent/bundle || true

RUN chown -R agent:agent /home/agent
USER agent

ENV PATH="/home/agent/repo/.tools/wireproxy:$PATH"

CMD ["sh", "/home/agent/repo/tools/linux_bootstrap/vps_bootstrap.sh", "/home/agent/bundle"]
