# TPCTF 2026 智能体赛道 agent 镜像（CTFY-0001 v2 用户镜像契约）
#
# 契约：平台以 `docker create -w /ctfy/work -e CTFY_WORK_DIR=/ctfy/work ... <image>` 启动，
# 后面不跟任何 command/args → entrypoint 必须由镜像自带，且 cwd 被强制成 /ctfy/work，
# 所以不能用相对路径脚本，只能 `python -m ctfy_agent agent` + PYTHONPATH。

FROM python:3.12-slim

# grep 不在 coreutils 里、是独立 Debian 包；curl + ca-certificates 供 https 探测；
# bash 供 web 角色的 shell 工具（模型会用它跑探测，缺了 shell 16/16 全灭）；
# passwd 提供 useradd（下面要建非 root 用户；debian slim 不保证自带，显式装上最稳）。
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        curl grep coreutils ca-certificates bash passwd \
    && rm -rf /var/lib/apt/lists/*

# ---------- P2：web 工具链（构建期安装；运行期无网络，所以必须烤进镜像） ----------
# ffuf：目录/参数发现，web 角色的 fuzz 工具调用它（模型不直接跑命令行）。
# 版本 + 精确字节数 + sha256 三钉死：字节数取自该 release 的 asset 元数据，
# sha256 取自官方 ffuf_2.1.0_checksums.txt（同 release）。传输截断、内容被换、
# 或 asset 被替换都会在构建期直接失败。
ARG FFUF_VERSION=2.1.0
ARG FFUF_BYTES=3478614
ARG FFUF_SHA256=fc2c82736c14dcbea4daf3d3cf3878c1c4773008ba45c2bc0fceba7d17b40bb5
# CI 只构建 linux/amd64；换架构必须显式改这里，而不是静默装一个跑不起来的二进制
RUN test "$(dpkg --print-architecture)" = "amd64"
RUN set -eux; \
    curl -fsSL -o /tmp/ffuf.tgz \
      "https://github.com/ffuf/ffuf/releases/download/v${FFUF_VERSION}/ffuf_${FFUF_VERSION}_linux_amd64.tar.gz"; \
    test "$(stat -c%s /tmp/ffuf.tgz)" = "${FFUF_BYTES}"; \
    if [ -n "${FFUF_SHA256}" ]; then echo "${FFUF_SHA256}  /tmp/ffuf.tgz" | sha256sum -c -; fi; \
    echo "ffuf tarball sha256: $(sha256sum /tmp/ffuf.tgz | cut -d' ' -f1)"; \
    tar -xzf /tmp/ffuf.tgz -C /usr/local/bin ffuf; \
    chmod 755 /usr/local/bin/ffuf; \
    rm -f /tmp/ffuf.tgz; \
    ffuf -V

# SecLists 字典：只取三张小表（约 441KB）。commit + 精确字节数双钉死；
# /opt 下 root 只读、644，运行用户 uid 10001 可读不可改（模型污染不了字典）。
ARG SECLISTS_SHA=758fecf4925900dd1bd0a3cba39551d6b4f2da8c
RUN set -eux; \
    mkdir -p /opt/wordlists; \
    base="https://raw.githubusercontent.com/danielmiessler/SecLists/${SECLISTS_SHA}/Discovery/Web-Content"; \
    fetch() { curl -fsSL -o "/opt/wordlists/$1" "$base/$2"; test "$(stat -c%s "/opt/wordlists/$1")" = "$3"; }; \
    fetch common.txt common.txt 38536; \
    fetch params.txt burp-parameter-names.txt 54157; \
    fetch small.txt raft-small-words.txt 348619; \
    chmod 644 /opt/wordlists/*.txt; \
    wc -l /opt/wordlists/*.txt

# sqlmap：盲注（尤其时间盲注）手工极痛，web 角色的 sqli 工具调用它。
# 源码树装到 /opt/sqlmap、root 只读，不进 site-packages，因此不违反「src/ 只允许 stdlib」的自扫。
#
# ⚠️ 为什么用 GitHub 归档 + raw 单文件两次下载：sqlmap 仓库把 sqlmap.py 写进了
# .gitattributes 的 export-ignore，所以 **GitHub 归档和 PyPI sdist 里都没有顶层 sqlmap.py**
# （实测 CI run 40836261029：PyPI 包解出 18M 却报 `No such file '/opt/sqlmap/sqlmap.py'`）。
# 归档给 lib/data/plugins/tamper 等运行期依赖，sqlmap.py 单独从 raw 取。
# 两个下载各自钉死字节数 + sha256；任一不符或跑不起来就让构建失败。
ARG SQLMAP_VERSION=1.10.10
ARG SQLMAP_TAR_BYTES=8373244
ARG SQLMAP_TAR_SHA256=7dd690c62fb1ae58bd8555b91012896863c47fc245dc6d39ba300362a3368d17
ARG SQLMAP_MAIN_BYTES=30040
ARG SQLMAP_MAIN_SHA256=311eb1962efd1ceb1b26a6086b639f8e85f287888717c5eb7dbd28af63cdb56b
RUN set -eux; \
    curl -fsSL -o /tmp/sqlmap.tar.gz \
      "https://github.com/sqlmapproject/sqlmap/archive/refs/tags/${SQLMAP_VERSION}.tar.gz"; \
    test "$(stat -c%s /tmp/sqlmap.tar.gz)" = "${SQLMAP_TAR_BYTES}"; \
    echo "${SQLMAP_TAR_SHA256}  /tmp/sqlmap.tar.gz" | sha256sum -c -; \
    mkdir -p /opt/sqlmap; \
    tar -xzf /tmp/sqlmap.tar.gz -C /opt/sqlmap --strip-components=1; \
    curl -fsSL -o /opt/sqlmap/sqlmap.py \
      "https://raw.githubusercontent.com/sqlmapproject/sqlmap/${SQLMAP_VERSION}/sqlmap.py"; \
    test "$(stat -c%s /opt/sqlmap/sqlmap.py)" = "${SQLMAP_MAIN_BYTES}"; \
    echo "${SQLMAP_MAIN_SHA256}  /opt/sqlmap/sqlmap.py" | sha256sum -c -; \
    rm -rf /opt/sqlmap/doc /opt/sqlmap/extra /opt/sqlmap/tests /opt/sqlmap/.github \
           /opt/sqlmap/.gitignore /opt/sqlmap/.gitattributes /tmp/sqlmap.tar.gz; \
    chmod -R a+rX /opt/sqlmap; \
    du -sh /opt/sqlmap; \
    python /opt/sqlmap/sqlmap.py --version

# 构建期自检：用真实 ffuf 打本机 http.server（验证 fuzz 的 -w/-u/-of json/-ac/-maxtime
# 形状与 JSON 可解析），并用真 sqlmap 打同站点验证「injectable 判定」能产出；末尾的
# 跳转探针只打印 decoy_hits（观察 sqlmap 对 302 的姿态，不 fail）。
RUN set -eux; \
    cd /tmp; \
    python -m http.server 18080 --bind 127.0.0.1 >/dev/null 2>&1 & \
    srv=$!; sleep 1; \
    printf 'admin\nlogin\nindex.html\n' > /tmp/w.txt; \
    ffuf -s -noninteractive -ac -of json -o /tmp/o.json \
         -w /tmp/w.txt -u http://127.0.0.1:18080/FUZZ \
         -t 10 -timeout 5 -maxtime 20 -mc 200,301,302,401,403 || true; \
    sm=$(timeout 90 python /opt/sqlmap/sqlmap.py -u "http://127.0.0.1:18080/?id=1" \
           --batch --disable-coloring --level=1 --risk=1 --technique=B \
           --timeout=3 --retries=0 --output-dir=/tmp/sqlmap-out 2>&1 || true); \
    kill "$srv" 2>/dev/null || true; \
    echo "$sm" | tail -4; \
    echo "$sm" | grep -qi "injectable" || { echo "sqlmap 自检失败：没产出 injectable 判定（Python 3.12 兼容或联网问题）" >&2; exit 1; }; \
    cat > /tmp/redirprobe.py <<'PY'
import http.server
import subprocess
import sys
import threading

HITS = []


class Redir(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        self.send_response(302)
        self.send_header("Location", "http://127.0.0.1:18099/decoy")
        self.send_header("Content-Length", "0")
        self.end_headers()


class Decoy(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        HITS.append(self.path)
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")


def serve(port, handler):
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()


serve(18098, Redir)
serve(18099, Decoy)
proc = subprocess.run(
    [sys.executable, "/opt/sqlmap/sqlmap.py", "-u", "http://127.0.0.1:18098/?id=1",
     "--batch", "--disable-coloring", "--flush-session", "--level=1", "--risk=1",
     "--technique=B", "--timeout=3", "--retries=0", "--threads=1",
     "--max-redirects=3", "--output-dir=/tmp/sqlmap-probe"],
    capture_output=True, text=True,
)
tail = "\n".join((proc.stdout or "").splitlines()[-6:])
print("REDIRECT-PROBE decoy_hits=%d  (0 = sqlmap 未跟随 302；>0 = 跟随了，A4 暴露面成立)" % len(HITS))
print("REDIRECT-PROBE tail:")
print(tail)
PY

# 构建期自检第二步（必须独立 RUN）：上面 `cat > … <<'PY'` 的 heredoc 在 `PY` 处就结束了
# 所在 RUN 指令——若把 `python /tmp/redirprobe.py` 继续接在同一段里，Dockerfile 解析器会把
# 它当成新指令（实测 CI 报 "unknown instruction: python"，run 40835923318）。
RUN python /tmp/redirprobe.py; \
    test -s /tmp/o.json; \
    python -c "import json;d=json.load(open('/tmp/o.json'));assert isinstance(d.get('results'),list),'ffuf JSON 结构异常';print('ffuf selftest ok, results=',len(d['results']))"; \
    rm -rf /tmp/o.json /tmp/w.txt /tmp/redirprobe.py /tmp/sqlmap-out /tmp/sqlmap-probe /root/.sqlmap

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

# 平台协议检查硬性要求：用户镜像必须以 uid != 0 的非 root 用户运行
# （root 直接被判「协议检查：发现问题 / 试运行：失败」）。
# 运行期不需要 root：平台把 /ctfy/work 以 0o777 的 staging 目录 `docker cp` 进容器
# （providers/docker_provider/harness.py），运行期状态全部写在 /ctfy/work/runs
# （agent_main.py: config.runs_dir = work_dir/"runs"），/ctfy 只读即可。
# 顺序：USER 必须放在 WORKDIR/COPY 之后——WORKDIR 建目录、COPY 铺文件都要 root 身份。
RUN useradd --create-home --uid 10001 --shell /bin/bash app
ENV HOME=/home/app
USER app

# 绝对入口 + PYTHONPATH（cwd=/ctfy/work 时模块仍可导入）；task.json 路径默认取 $CTFY_WORK_DIR
ENTRYPOINT ["python", "-m", "ctfy_agent", "agent"]
