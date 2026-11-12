# TPCTF 2026 智能体赛道 agent 镜像（CTFY-0001 v2 用户镜像契约）
#
# 契约：平台以 `docker create -w /ctfy/work -e CTFY_WORK_DIR=/ctfy/work ... <image>` 启动，
# 后面不跟任何 command/args → entrypoint 必须由镜像自带，且 cwd 被强制成 /ctfy/work，
# 所以不能用相对路径脚本，只能 `python -m ctfy_agent agent` + PYTHONPATH。

FROM python:3.12-slim

# grep 不在 coreutils 里、是独立 Debian 包；curl + ca-certificates 供 https 探测；
# bash 供 web 角色的 shell 工具（模型会用它跑探测，缺了 shell 16/16 全灭）；
# passwd 提供 useradd（下面要建非 root 用户；debian slim 不保证自带，显式装上最稳）。
#
# gcc + libc6-dev + binutils（v7）：pwntools 的 asm() 是「cpp 预处理 → as 汇编」两步，
# 少任何一个都抛 PwnlibException——实测 v7 构建 run 31 缺 as、run 32 缺 cpp 连续挂两次。
# 顺带 gcc/libc6-dev 让模型能在容器里本地编译 C 漏洞利用（pwn 常见打法：写 poc.c 编译跑通
# 再打远程）。disasm()/ROP 还要 binutils 的 objdump/readelf。
#
# gdb + patchelf（v7.3，2026-11-12）：PWN 短板里最硬的一条是「崩了看不见」——模型只能猜偏移。
#   * gdb：给 pwn/rev 用 `gdb -batch -q -ex run -ex 'info registers' -ex bt ./poc` 这种批处理
#     方式看崩溃现场（无需 pwndbg，交互式用不上，批处理就够拿寄存器/栈/回溯）；
#   * patchelf：ret2libc 题把题目自带 libc 的 interpreter/rpath 改成本机 loader，才能本地复现。
#   体积约 +25MB（gdb 带 libpython/pretty-printer 依赖），总计约 230~240MB，仍低于 400MB 门禁。
#
# 体积：合计约 +110MB（gcc/libc6-dev/binutils 约 85 + gdb/patchelf 约 25），
# 基线 126.5 + 扩展库 ~85 + 工具链 ~110 仍在 400MB 门禁内（tools/container_check.py）。
# 注意 libc6-dev 必须显式列出：gcc 只 Recommend 它，本行是 --no-install-recommends，不写就没有 stdio.h。
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        curl grep coreutils ca-certificates bash passwd binutils gcc libc6-dev \
        gdb patchelf \
    && rm -rf /var/lib/apt/lists/* \
    && gdb --version | head -1 \
    && patchelf --version

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
    rm -rf /opt/sqlmap/doc /opt/sqlmap/tests /opt/sqlmap/.github \
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

# 关思考模式：百炼（DashScope 兼容端点）默认开启思考，重推理步骤会把 max_tokens 烧在
# reasoning_content 上 → content 空 / finish_reason=length，白丢一步（实测坑）。
# 本地所有实测都在 LLM_ENABLE_THINKING=0 下完成，镜像必须同口径。
# 注意：11-21 正式赛换官方智谱后若该字段不被接受，重建时去掉这行即可。
ENV LLM_ENABLE_THINKING=0

# ---------- rev/pwn 工具箱：capstone 反汇编引擎（v5 新增） ----------
# 为什么现在才装：docs/archive/rev-toolbox-plan.md 的 R2 项写着「capstone 进镜像，随 v4 重建一起做」，
# v4 建了但这一步漏做，于是 elfpeek 的 disasm / gadgets 在镜像里一直走 stdlib 字节退化路径；
# 更糟的是 rev/pwn 角色提示词和代码注释都写着「v4 镜像含 capstone」——提示词在骗模型，
# 每个 rev 题会白烧 1–2 步去调一个注定退化的视图。v5 补上（HANDOFF 缺口 #12）。
#
# 为什么用 pip 装而不是拷 .so：wheel 自带 libcapstone，pip 解包最省事；但必须
# 版本 + sha256 双钉死（与 ffuf/sqlmap 同口径），且**必须放在下面「删 pip」那段之前**。
# --require-hashes 会强制校验下载到的产物：一旦 PyPI 换了包体，构建当场失败而不是静默装进坏的。
ARG CAPSTONE_VERSION=5.0.3
ARG CAPSTONE_WHEEL_SHA256=a6b7f3ec2346a60475d9d043b437ace5e6688fc9c4d45387476104bd2c231040
RUN set -eux; \
    printf 'capstone==%s --hash=sha256:%s\n' "${CAPSTONE_VERSION}" "${CAPSTONE_WHEEL_SHA256}" > /tmp/capstone-req.txt; \
    python -m pip install --no-cache-dir --require-hashes -r /tmp/capstone-req.txt; \
    rm -f /tmp/capstone-req.txt; \
    python -c "import capstone; assert capstone.__version__=='5.0.3', capstone.__version__; cs=capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64); ins=list(cs.disasm(bytes([0x55,0x48,0x89,0xe5]), 0x1000)); assert len(ins)==2 and ins[0].mnemonic=='push', ins; print('capstone selftest ok:', capstone.__version__, len(ins), 'insns')"

# ---------- crypto/pwn 扩展库：pwntools + pycryptodome + gmpy2 + sympy + z3（v7 新增） ----------
# 为什么现在才装：上一轮（HANDOFF-2026-11-10-r2 §8）实测 runs/plat 26 题 CRYPTO 0/5、PWN 0/4，
# 收尾结论反复是「镜像里没有第三方库，只能纯 stdlib」；unipickle 19 次手工拼 opcode 全被拒。
# 模型是通过 shell() 子进程写脚本、脚本里 `import pwn/Crypto/...`，代码本体零 import——
# 所以只要把库装进 site-packages（子进程继承环境），crypto/pwn 角色就能直接用。
#
# 体积口径：基线 126.5MB，门禁 400MB（tools/container_check.py 默认），可用约 270MB。
# 本段合计约 +55~65MB（pwntools 12.3 + unicorn 15.7 + zstandard 5.3 + cryptography 4.5 +
# pycryptodome 3.0 + gmpy2 1.8 + sympy 6.0 + 纯 Python 小依赖 ~8），仍远低于门禁。
# z3（2026-11-12 补）：z3solve 工具早已上线并被 rev/crypto/pwn/misc 四个角色派发，但此前
# 镜像漏装 z3-solver → 官方镜像里 `import z3` 必失败、z3solve 恒报缺库（本机靠 tools/pylibs
# 外挂所以没暴露）。安装后 z3 包约 +21MB（解包体积），总计仍 ~220MB，低于 400MB 门禁。
#
# 为什么是「C 扩展锁 sha256 + 纯 Python 锁版本」而非全量 --require-hashes：
# pwntools 有 24 个传递依赖，--require-hashes 要求每个都有 hash，手工维护极易漏、
# 且任意一个依赖发新版就会让构建炸掉；这里对「带二进制 wheel、被换包体危害最大」的三个
# C 扩展包（pycryptodome/gmpy2/z3-solver）单独 --require-hashes 锁 sha256，pwntools/sympy 及
# 纯 Python 依赖链交给 pip 解析器按顶层版本约束解决（与 capstone 的「版本+sha256 双钉死」同口径地保守）。
#
# ⚠️ 必须放在下面「删 pip」那段之前（装完才删 pip）；wheel 都选 manylinux x86_64（CI 只建 amd64）。
ARG PYCRYPTODOME_VERSION=4.4.0
ARG PYCRYPTODOME_SHA256=32c1a3d98c93d92c2f2a45d6cc9c79fb8d7552aca1bd9a764209a3309bf6a7b1
ARG GMPY2_VERSION=2.3.2
ARG GMPY2_SHA256=c3021ec352e1b26baf4752f99d88adc9e930f115a053162c127d1c1b2f5783c2
ARG PWNYTOOLS_VERSION=4.15.0
ARG SYMPY_VERSION=1.14.0
ARG Z3_VERSION=5.4.1.0
ARG Z3_SHA256=e680ce083683abca5d07c3e55c2f620ff9a29cb91c548eeeea27ae29e8060522
RUN set -eux; \
    printf 'pycryptodome==%s --hash=sha256:%s\n' "${PYCRYPTODOME_VERSION}" "${PYCRYPTODOME_SHA256}" > /tmp/ext-req.txt; \
    printf 'gmpy2==%s --hash=sha256:%s\n' "${GMPY2_VERSION}" "${GMPY2_SHA256}" >> /tmp/ext-req.txt; \
    printf 'z3-solver==%s --hash=sha256:%s\n' "${Z3_VERSION}" "${Z3_SHA256}" >> /tmp/ext-req.txt; \
    python -m pip install --no-cache-dir --require-hashes -r /tmp/ext-req.txt; \
    python -m pip install --no-cache-dir "pwntools==${PWNYTOOLS_VERSION}" "sympy==${SYMPY_VERSION}"; \
    rm -f /tmp/ext-req.txt; \
    for b in as cpp gcc objdump readelf; do \
        command -v "$b" >/dev/null || { echo "缺工具链 $b（pwntools asm = cpp 预处理 + as 汇编，ROP/disasm 要 objdump/readelf）" >&2; exit 1; }; \
    done; \
    python -c "import z3; x=z3.Int('x'); s=z3.Solver(); s.add(x>3, x<5); assert s.check()==z3.sat and s.model()[x].as_long()==4, 'z3 求解异常'; print('z3 selftest ok', z3.get_version_string())"; \
    python -c "import Crypto, gmpy2, sympy, pwnlib; from pwn import remote, process, cyclic, p64, p32, context, ELF, asm, cyclic_find; assert len(cyclic(128))==128, 'cyclic 异常'; assert cyclic_find(p32(0x61616162))==4, 'cyclic_find 异常'; assert p64(0x41424344)==b'DCBA\x00\x00\x00\x00', 'p64 异常'; assert asm('nop')==b'\x90', 'asm 异常'; print('ext-libs selftest ok: Crypto', Crypto.__version__, '| gmpy2', gmpy2.version(), '| sympy', sympy.__version__, '| pwntools', pwnlib.__version__)"

# 瘦身（主办方：镜像太大传不上去）。运行时只用 stdlib，下列都是死重量（约 20MB）：
#   pip/setuptools/wheel/ensurepip（不安装依赖）、test 模块、idlelib/lib2to3/pydoc_data/turtledemo。
# 另：.dockerignore 已排除 tests/docs/runs/__pycache__，ENV 里禁了运行时写 .pyc。
# 注意顺序：上面 capstone 用的是 pip，必须在这段之前装完；这段删的是 pip 本身，不影响已装的包。
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

# ---------- [v7] 镜像不带 GUI / 多平台档案适配器：构建期硬校验 ----------
# 正赛容器只跑 `python -m ctfy_agent agent`（CTFY 单平台）。src/ctfy_agent/gui/ 里是
#   * 可视化界面：app.py / worker.py / settings.py / limits.py / onboard.py
#   * 连接其他平台的档案与适配器：platform_adapter.py / platform_worker.py / profiles.py / toolmap.py
# 二者对比赛毫无用处，且会把「切平台的开关」带进一个只该连 CTFY 的镜像里。.dockerignore 已把
# 该目录排除出构建上下文；这里再断言一次（任何人把它加回来，构建当场失败而不是静默混入）。
# 同时证明剔除后入口链路完好：attachments 对 gui 只有 main() 里一处懒加载（且捕获 ImportError
# 按单平台继续跑），agent 主链路（agent_main→crew→solver→platform→tools_*）零 gui 依赖。
RUN python -c "import importlib.util as u; assert u.find_spec('ctfy_agent.gui') is None, 'src/ctfy_agent/gui 不该进镜像（GUI + 多平台档案适配器）'; import ctfy_agent, ctfy_agent.agent_main, ctfy_agent.attachments, ctfy_agent.crew, ctfy_agent.solver, ctfy_agent.instance_guard, ctfy_agent.tools_local, ctfy_agent.tools_misc_ext, ctfy_agent.tools_webscan, ctfy_agent.tools_websec, ctfy_agent.tools_websql, ctfy_agent.tools_domain_ext; print('v7 image selftest ok: no gui, entrypoint imports clean')"

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
