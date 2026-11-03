# -*- coding: utf-8 -*-
"""平台姿态自检（P1-3）：把交付镜像放进「平台会给的那套约束」里跑一遍，看它还能不能干活。

⚠️ 名字叫自检不叫证明：这**不能**证明容器不可逃逸，也不能证明平台真的这么跑。
   它只回答一个具体问题——「以非 root、只读根、断网、只有 /ctfy/work 可写」时，
   我们的 ENTRYPOINT 还能不能正常写出 result.json。这是历史上最容易踩的一类坑
   （v2 就因 root 被平台协议检查直接拒收）。

六项探测（全部在容器内执行，宿主只看结果）：
  1. non_root            uid != 0
  2. source_readonly     /ctfy/src 不可写（代码目录不该被运行时改动）
  3. work_writable       /ctfy/work 可写（result.json 必须落在这里）
  4. egress_blocked      TCP 到 1.1.1.1:443 失败（internet_policy=deny）
  5. no_dotenv           镜像里没有 .env（凭据只能由平台注入）
  6. dry_run_exit_zero   在上述约束下跑 dry_run，退出码必须是 0 且写出 result.json

用法：
    python tools/isolation_probe.py --image ghcr.io/yzqcyl/ctfy-agent-pub:v4
    python tools/isolation_probe.py --json          # 机读输出
    python tools/isolation_probe.py                 # 没装 docker → 打印 SKIP，退出码 0

退出码：0=全过（或跳过），1=有探测失败。加 --strict 时「跳过」也算失败（CI 用）。
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_IMAGE = "ctfy-agent:check"

# 容器内执行：只用 stdlib，结果一行 JSON 打印出来
PROBE_SCRIPT = r"""
import json, os, socket
def cannot_write(path):
    try:
        with open(path, "wb") as handle:
            handle.write(b"probe")
        return False
    except OSError:
        return True
sock = socket.socket()
sock.settimeout(2)
try:
    egress_blocked = sock.connect_ex(("1.1.1.1", 443)) != 0
except OSError:
    egress_blocked = True
finally:
    sock.close()
print(json.dumps({
    "non_root": os.getuid() != 0,
    "source_readonly": cannot_write("/ctfy/src/probe.tmp"),
    "work_writable": not cannot_write("/ctfy/work/probe.tmp"),
    "egress_blocked": egress_blocked,
    "no_dotenv": not os.path.exists("/ctfy/.env"),
    "uid": os.getuid(),
}))
"""


def docker() -> str | None:
    return shutil.which("docker")


def daemon_ready(binary: str) -> tuple[bool, str]:
    """docker CLI 装了不等于引擎在跑（Windows 上 Docker Desktop 没启动就是这个状态）。"""
    try:
        proc = run([binary, "info", "--format", "{{.ServerVersion}}"], timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"docker info 调用失败：{exc}"
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()
        return False, f"Docker 引擎未就绪：{tail[-1] if tail else '未知原因'}"
    return True, proc.stdout.strip()


def run(cmd: list[str], timeout: int = 180) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout)


def probe(image: str) -> dict:
    """跑六项探测，返回 {passed, checks, error}。任何环境问题都降级成 error，不抛。"""
    binary = docker()
    if not binary:
        return {"passed": False, "skipped": True, "checks": {},
                "error": "未找到 docker：跳过姿态自检（CI 里应装在能 build 的 runner 上跑）"}
    ready, info = daemon_ready(binary)
    if not ready:
        return {"passed": False, "skipped": True, "checks": {}, "error": info}
    work = Path(tempfile.mkdtemp(prefix="ctfy-posture-"))
    common = [
        binary, "run", "--rm", "--network", "none", "--read-only",
        "--tmpfs", "/tmp:rw,nosuid,nodev,size=64m,mode=1777",
        # 刻意不传 --user：要验的正是「镜像自己声明的 USER 是不是非 root」，
        # 外部强行指定 uid 会把这条唯一的真相盖掉。
        "--mount", f"type=bind,source={work.as_posix()},target=/ctfy/work",
    ]
    try:
        # 1–5：纯粹的环境事实
        # 必须显式覆盖 entrypoint：镜像自带 ENTRYPOINT=["python","-m","ctfy_agent","agent"]，
        # 直接追加 "python -I -c ..." 会被当成 agent 子命令的参数（实测 CI 报
        # `ctfy_agent: error: unrecognized arguments: python -I -c`）。
        base = run([*common, "--entrypoint", "python", image, "-I", "-c", PROBE_SCRIPT])
        if base.returncode != 0:
            return {"passed": False, "checks": {}, "error":
                    f"容器内探测脚本退出 {base.returncode}：{(base.stderr or base.stdout)[-800:]}"}
        try:
            checks = json.loads(base.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            return {"passed": False, "checks": {}, "error": f"探测输出无法解析：{base.stdout[-400:]}"}

        # 6：同一套约束下跑真实入口（dry_run 不联网、不烧 token）
        env = ["-e", "CTFY_EVAL_DRY_RUN=1", "-e", "CTFY_WORK_DIR=/ctfy/work",
               "-e", "LLM_FLAVOR=openai", "-e", "LLM_API_KEY=dummy", "-e", "LLM_MODEL=dummy"]
        dry = run([*common, *env, image])
        result_file = work / "result.json"
        payload = {}
        if result_file.is_file():
            try:
                payload = json.loads(result_file.read_text(encoding="utf-8"))
            except ValueError:
                payload = {}
        checks["dry_run_exit_zero"] = dry.returncode == 0
        checks["dry_run_wrote_result"] = bool(payload)
        return {
            "passed": all(checks.values()),
            "checks": checks,
            "stop_reason": payload.get("stop_reason"),
            "error": "" if dry.returncode == 0 else
                     f"dry_run 退出 {dry.returncode}：{(dry.stderr or '')[-800:]}",
        }
    except subprocess.TimeoutExpired:
        return {"passed": False, "checks": {}, "error": "探测超时（180s）"}
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="交付镜像的平台姿态自检")
    parser.add_argument("--image", default=DEFAULT_IMAGE, help=f"镜像标签（默认 {DEFAULT_IMAGE}）")
    parser.add_argument("--json", action="store_true", help="机读输出")
    parser.add_argument("--strict", action="store_true", help="跳过也算失败（CI 用）")
    args = parser.parse_args(argv)

    outcome = probe(args.image)
    if args.json:
        print(json.dumps(outcome, ensure_ascii=False, indent=2))
    else:
        if outcome.get("skipped"):
            print(f"[SKIP] {outcome['error']}")
        else:
            print(f"== 平台姿态自检：{args.image} ==")
            for name, value in (outcome.get("checks") or {}).items():
                print(f"  {'PASS' if value else 'FAIL'}  {name}")
            if outcome.get("stop_reason"):
                print(f"  dry_run stop_reason={outcome['stop_reason']}")
            if outcome.get("error"):
                print(f"  ! {outcome['error']}", file=sys.stderr)
            print("全部通过 ✓" if outcome["passed"] else "有探测失败 ✗")

    if outcome.get("skipped"):
        return 1 if args.strict else 0
    return 0 if outcome["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
