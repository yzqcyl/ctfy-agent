"""pack_upload.py —— 把「上传到官方 VPS / 拷进测试 VM」的内容打成一个 tar.gz。

包含：src/、runs/_tpctf 的脚本、tools/linux_bootstrap（含离线 bundle）、
     .tools/wireproxy 的 Linux 二进制、config/agent.json、题目附件与元数据、文档。
排除：.env（密钥）、构建产物/大目录（wheels、brotli、pwnrev、sandbox、transcripts、
     jsonl 会话数据、__pycache__、.git 等）。

用法：
    python tools/linux_bootstrap/pack_upload.py            # 输出 dist/tpctf-agent-upload.tar.gz
    python tools/linux_bootstrap/pack_upload.py --serve    # 打完顺手起 8000 端口 HTTP 服务
"""
from __future__ import annotations

import argparse
import hashlib
import pathlib
import sys
import tarfile

ROOT = pathlib.Path(__file__).resolve().parents[2]

INCLUDE_DIRS = [
    "src",
    "tools/linux_bootstrap",
    # 工具链/能力沉淀：VPS 上要能复编 ft_hook、跑 pwn 本地化、用出题模板
    "tools/re_toolchain",
    "tools/authoring",
    "tools/pwn_local",
    "tools/exploit_templates",
    "tools/wordlists",
    "tools/shims",
    "docs",
    "tests",
    "roles",
    "runs/tpctf2026/attachments",
    "runs/tpctf2026/docs",
    "runs/_tpctf/l2_assets",          # L2 团队资产（leak_helpers.py 等，不含 flag）
]
INCLUDE_FILES = [
    "config/agent.json",
    "config/platforms.json",
    "runs/tpctf2026/challenges.json",
    "runs/tpctf2026/challenges_raw.json",
    ".tools/wireproxy/wireproxy",
    "runs/tpctf2026/web_cve_stage_复现文档.md",
    "runs/tpctf2026/TPCTF2026_最终复盘报告.md",
    "runs/tpctf2026/初赛VPS与网关适配.md",
    "runs/tpctf2026/修复与自治方案.md",
    "runs/tpctf2026/supervisor实现方案.md",
]
INCLUDE_GLOBS = [
    ("runs/_tpctf", "*.py"),          # 只带脚本，不带 wheels/brotli/pwnrev 等大目录
    ("runs/_tpctf", "*.sh"),          # L2 隔离彩排的 root wrapper（生成随机 flag，无明文）
    ("tools", "*.py"),                # tools 顶层工具脚本（bench_stats/check_*/secret_scan…）
]
EXCLUDE_PARTS = {
    ".git", "__pycache__", ".pytest_cache", "node_modules",
    "sandbox", "transcripts", "checkpoints", "traffic", "handoff",
    "wheels", "brotli", "pwnrev", "revvm", "pylibs", "pylibs2", "tmp",
    "_toolkit_test", "linux_bundle", ".policy_test_reruns", ".mock_out", ".m3_out",
}
EXCLUDE_SUFFIX = {".jsonl", ".pyc", ".log", ".exe", ".tar.gz", ".zip"}

# Windows 上没有可靠的 Unix 可执行位，tarfile 会把文件记成 0666；解包到 Linux 后
# wireproxy_nolandlock / *.sh 之类必须可执行。这里显式写 0755。
EXECUTABLE = {
    "tools/linux_bootstrap/vps_bootstrap.sh",
    "tools/re_toolchain/build_ft_hook.sh",
    "tools/re_toolchain/run_hook_checker.sh",
    ".tools/wireproxy/wireproxy",
}
EXECUTABLE_PREFIX = ("tools/linux_bootstrap/prebuilt/",)


def tarinfo_for(bundle: tarfile.TarFile, path: pathlib.Path) -> tarfile.TarInfo:
    rel = path.relative_to(ROOT).as_posix()      # Windows 下必须转 /，否则前缀判断失效
    info = bundle.gettarinfo(str(path), arcname=rel)
    if rel in EXECUTABLE or rel.endswith(".sh") or rel.startswith(EXECUTABLE_PREFIX):
        info.mode = 0o755
    return info


def skip(path: pathlib.Path) -> bool:
    rel = path.relative_to(ROOT)
    if any(part in EXCLUDE_PARTS for part in rel.parts):
        return True
    if path.name == ".env" or path.name.startswith(".env."):
        return True
    if path.suffix in EXCLUDE_SUFFIX:
        # tests/fixtures 下的压缩包是测试输入数据，不是构建产物——必须带上，
        # 否则 VPS 上跑自检会因 zip/tar.gz 缺失而误报。
        in_fixtures = len(rel.parts) >= 2 and rel.parts[0] == "tests" and rel.parts[1] == "fixtures"
        if not in_fixtures:
            return True
    return False


def collect() -> list[pathlib.Path]:
    files: list[pathlib.Path] = []
    for rel in INCLUDE_FILES:
        path = ROOT / rel
        if path.is_file() and not skip(path):
            files.append(path)
    for rel in INCLUDE_DIRS:
        base = ROOT / rel
        if base.is_dir():
            for path in base.rglob("*"):
                if path.is_file() and not skip(path):
                    files.append(path)
    for rel, pattern in INCLUDE_GLOBS:
        base = ROOT / rel
        if base.is_dir():
            for path in base.glob(pattern):
                if path.is_file() and not skip(path):
                    files.append(path)
    # 去重 + 排序
    return sorted(set(files))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="dist/tpctf-agent-upload.tar.gz")
    parser.add_argument("--serve", action="store_true")
    args = parser.parse_args()

    files = collect()
    out = ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    with tarfile.open(out, "w:gz") as bundle:
        for path in files:
            info = tarinfo_for(bundle, path)
            with path.open("rb") as fh:
                bundle.addfile(info, fh)
            total += path.stat().st_size
    size = out.stat().st_size
    digest = hashlib.sha256(out.read_bytes()).hexdigest()
    print(f"打包 {len(files)} 个文件，原始 {total / 1e6:.1f} MB，压缩后 {size / 1e6:.1f} MB")
    print(f"输出: {out}")
    print(f"sha256: {digest}")

    if args.serve:
        import http.server
        import socketserver
        port = 8000
        handler = lambda *a, **kw: http.server.SimpleHTTPRequestHandler(  # noqa: E731
            *a, directory=str(out.parent), **kw)
        with socketserver.TCPServer(("0.0.0.0", port), handler) as httpd:
            print(f"HTTP 服务: http://<本机IP>:{port}/{out.name}  (Ctrl+C 结束)")
            httpd.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
