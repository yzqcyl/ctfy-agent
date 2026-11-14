#!/usr/bin/env python3
"""provision.py —— 离线/在线准备 Linux 工具箱（官方 VPS 上没有 apt/网络时用）。

设计目标：**不依赖 root / apt / pip 网络**，把「跑 Linux ELF、跑 FreeType、反汇编、编译钩子」
需要的文件预下载成 bundle，上传到 VPS 后一条命令解包到用户目录。

用法：
    python3 provision.py --list                      # 看默认清单
    python3 provision.py --bundle  ./bundle          # 有网机器上：下载全部到 ./bundle
    python3 provision.py --install ./bundle          # VPS 上：从 bundle 解包到 ./toolkit
    python3 provision.py --install ./bundle --verify # 解包并校验 hash

支持的 artifact 类型：
    pypi   : 运行时从 PyPI JSON 解析 manylinux wheel（记录 URL+sha256 便于离线复用）
    url    : 直链下载
    wheel  : zip 解包（manylinux wheel → ./toolkit/pylibs）
    tar    : tar.xz/tar.gz 解包（zig、qemu-user-static 等 → ./toolkit/opt）
    deb    : 纯 Python 解包 Debian .deb（ar + data.tar.xz）→ ./toolkit/sysroot
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import lzma
import pathlib
import sys
import tarfile
import urllib.request
import zipfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
TOOLKIT = pathlib.Path.cwd() / "toolkit"
UA = {"User-Agent": "tpctf-agent/1.0 (linux bootstrap)"}


def default_manifest() -> list[dict]:
    """默认清单：ELF/FreeType/反汇编 + pwntools 全家桶（封网也能 pwn.remote/ELF/ROP）。

    pwntools 4.x 的运行时依赖（wheel 装在 pylibs 里，`import pwn` 即用它）：
      paramiko/mako/pyelftools/capstone/ropgadget/pyserial/requests/pygments/pysocks/
      python-dateutil/packaging/psutil/intervaltree/sortedcontainers/unicorn/six/rpyc/
      colored_traceback/unix-ar/zstandard
    体积 ~25MB（含 unicorn），比手工拼 solve.py 划算：本场 pwn 全靠外部脚本 + 手工 tunnel。
    """
    deps = [
        # 核心
        "pwntools",
        "paramiko", "mako", "pyelftools", "capstone", "ropgadget", "pyserial",
        "requests", "pygments", "pysocks", "python-dateutil", "packaging", "psutil",
        "intervaltree", "sortedcontainers", "six", "rpyc", "colored_traceback",
        "unix-ar", "zstandard",
        # paramiko 依赖（import pwn 会 import ssh tube → paramiko）
        "bcrypt", "cryptography", "pynacl", "cffi", "pycparser", "invoke",
        # requests 依赖
        "charset_normalizer", "idna", "urllib3", "certifi",
        # rpyc / mako / dateutil 依赖
        "plumbum", "MarkupSafe", "tzdata",
        # plumbum 在 py<3.13 上要 typing-extensions（离线 pip 解析要它）
        "typing-extensions",
        # pwntools 自身 install_requires 里有 pip（离线 bundle 也带上，方便 pip 修工具链）
        "pip",
        # colored_traceback 在 Windows 上要 colorama；带上它让离线 pip 在任意主机都能解析
        "colorama",
        # FreeType（自带 libfreetype.so，无需 apt；rev 的 checker 就是动态链它）
        "freetype-py",
    ]
    # unicorn 固定 2.1.1：pwntools 4.15 明确排除 2.1.3/2.1.4（它自己的已知坏版本）
    entries = [{"name": "unicorn", "kind": "pypi", "dest": "pylibs", "version": "2.1.1"}]
    entries += [{"name": name, "kind": "pypi", "dest": "pylibs"}
                for name in deps if name != "unicorn"]
    return entries


def fetch(url: str, timeout: float = 300.0) -> bytes:
    request = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(request, timeout=timeout) as resp:
        return resp.read()


def sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def pypi_wheel(name: str, python_tag: str = "cp312",
               version: str | None = None) -> tuple[str, str, str, int]:
    """从 PyPI 解析一个 x86_64 manylinux wheel（纯 Python 兜底）。返回 (filename, url, sha256, size)。

    选择优先级（避免拿到别的 Python 版本的扩展 wheel——解包后 import 直接炸）：
      1) cp312 的 manylinux x86_64；
      2) cp312 的 manylinux2014/2_17 x86_64；
      3) abi3 (cp3x-abi3) 的 manylinux x86_64；
      4) py3-none-any（纯 Python）；
      5) py2.py3-none-any。
    `version` 固定版本（离线复现用）；None=取最新。
    """
    meta = json.loads(fetch(f"https://pypi.org/pypi/{name}/json").decode())
    version = version or meta["info"]["version"]
    entries = meta["releases"].get(version) or []
    if not entries:
        raise SystemExit(f"pypi: {name} 没有版本 {version}")
    py_major = python_tag[:3]  # cp312 -> cp3
    prefs: list[list[str]] = [
        [f"-{python_tag}-", "manylinux", "x86_64"],
        [f"-{python_tag}-", "x86_64"],
        ["-abi3-", "manylinux", "x86_64"],
        ["-abi3-", "x86_64"],
        ["-py3-none-manylinux", "x86_64"],      # capstone/freetype 这类 py3 标签的 C 扩展
        ["-py2.py3-none-manylinux", "x86_64"],
        ["-py3-none-any.whl"],
        ["-py2.py3-none-any.whl"],
        ["-none-any.whl"],
    ]
    best = None
    for want in prefs:
        for entry in entries:
            filename = str(entry.get("filename") or "")
            if not filename.endswith(".whl"):
                continue
            if all(token in filename for token in want):
                best = entry
                break
        if best is not None:
            break
    if best is None:
        # 允许「cp3xx 通用」的 sdist 拒绝，明确报错而不是装错平台
        raise SystemExit(f"pypi: 找不到 {name}=={version} 的 x86_64/{python_tag} wheel")
    return best["filename"], best["url"], best.get("digests", {}).get("sha256", ""), best["size"]


def ar_members(path: pathlib.Path) -> dict[str, bytes]:
    """解析 ar 归档（.deb 的外层）。"""
    raw = path.read_bytes()
    if not raw.startswith(b"!<arch>\n"):
        raise ValueError("不是 ar 归档")
    offset = 8
    members: dict[str, bytes] = {}
    while offset + 60 <= len(raw):
        header = raw[offset:offset + 60]
        name = header[:16].decode("ascii", "replace").strip().rstrip("/")
        size = int(header[48:58].decode("ascii", "replace").strip() or 0)
        offset += 60
        members[name] = raw[offset:offset + size]
        offset += size + (size % 2)
    return members


def install_deb(path: pathlib.Path, dest: pathlib.Path) -> None:
    members = ar_members(path)
    data_name = next((name for name in members if name.startswith("data.tar")), None)
    if data_name is None:
        raise ValueError("deb 里没有 data.tar.*")
    data = members[data_name]
    if data_name.endswith(".xz"):
        stream = lzma.decompress(data)
    elif data_name.endswith(".gz"):
        import gzip  # noqa: PLC0415
        stream = gzip.decompress(data)
    else:
        raise ValueError(f"暂不支持 {data_name}（zst 需要额外依赖）")
    with tarfile.open(fileobj=io.BytesIO(stream)) as bundle:
        # 只跳过 ar/tar 里的「目录本身」条目，不能跳过 "./usr/..." 这种带前缀的真实路径
        members_list = [m for m in bundle.getmembers() if m.name not in (".", "./")]
        try:
            bundle.extractall(dest, members=members_list, filter="data")
        except TypeError:
            bundle.extractall(dest, members=members_list)
    _make_soname_symlinks(dest)


def _make_soname_symlinks(root: pathlib.Path) -> None:
    """为解出的 libNAME.so.MAJOR.MINOR 补 libNAME.so.MAJOR 软链（.deb 里通常只带真实版本文件）。"""
    import os
    import re
    for path in root.rglob("lib*.so.*"):
        if not path.is_file():
            continue
        m = re.match(r"^(.*\.so)\.(\d+)\.(\d+.*)$", path.name)
        if not m:
            continue
        link = path.with_name(f"{m.group(1)}.{m.group(2)}")
        if not link.exists():
            try:
                os.symlink(path.name, link)
            except OSError:
                pass


def cmd_list(manifest: list[dict]) -> None:
    print("默认工具箱清单：")
    for item in manifest:
        print(f"  - {item['name']:<16} kind={item['kind']:<6} dest={item.get('dest')}")


def cmd_bundle(manifest: list[dict], bundle_dir: pathlib.Path) -> None:
    bundle_dir.mkdir(parents=True, exist_ok=True)
    resolved = []
    for item in manifest:
        name = item["name"]
        if item["kind"] == "pypi":
            filename, url, digest, size = pypi_wheel(name, version=item.get("version"))
        else:
            filename = item.get("filename") or pathlib.Path(item["url"]).name
            url = item["url"]
            digest = item.get("sha256", "")
            size = item.get("size", 0)
        target = bundle_dir / filename
        if target.is_file() and (not digest or sha256_of(target.read_bytes()) == digest):
            print(f"已有 {filename}")
        else:
            data = fetch(url)
            if digest and sha256_of(data) != digest:
                raise SystemExit(f"{filename} sha256 不匹配")
            target.write_bytes(data)
            print(f"下载 {filename} ({len(data)} B)")
        resolved.append({**item, "filename": filename, "url": url, "sha256": digest,
                         "size": target.stat().st_size})
    manifest_path = bundle_dir / "manifest.resolved.json"
    manifest_path.write_text(json.dumps(resolved, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n清单写入 {manifest_path}")


def cmd_install(bundle_dir: pathlib.Path, toolkit: pathlib.Path, verify: bool) -> None:
    manifest_path = bundle_dir / "manifest.resolved.json"
    manifest = (json.loads(manifest_path.read_text(encoding="utf-8"))
                if manifest_path.is_file() else default_manifest())
    for item in manifest:
        filename = item.get("filename") or item["name"]
        path = bundle_dir / filename
        if not path.is_file():
            print(f"!! 缺少 {filename}，跳过")
            continue
        if verify and item.get("sha256"):
            if sha256_of(path.read_bytes()) != item["sha256"]:
                raise SystemExit(f"{filename} 校验失败")
        dest_kind = item.get("dest") or "opt"
        dest = toolkit / dest_kind
        dest.mkdir(parents=True, exist_ok=True)
        kind = item["kind"]
        if kind == "pypi":
            kind = "wheel"
        if kind == "wheel":
            with zipfile.ZipFile(path) as bundle:
                bundle.extractall(dest)
            print(f"解包 wheel {filename} -> {dest}")
        elif kind == "tar":
            with tarfile.open(path) as bundle:
                try:
                    bundle.extractall(dest, filter="data")
                except TypeError:
                    bundle.extractall(dest)
            print(f"解包 tar {filename} -> {dest}")
        elif kind == "deb":
            install_deb(path, dest)
            print(f"解包 deb {filename} -> {dest}")
        else:
            target = dest / filename
            target.write_bytes(path.read_bytes())
            print(f"复制 {filename} -> {target}")
    print(f"\n工具箱就绪：{toolkit}")
    print("提示：把下面两个环境变量加进 agent 的 env：")
    print(f"  PYTHONPATH={toolkit / 'pylibs'}:$PYTHONPATH")
    print(f"  LD_LIBRARY_PATH={toolkit / 'sysroot/usr/lib/x86_64-linux-gnu'}:$LD_LIBRARY_PATH")


def cmd_selftest(toolkit: pathlib.Path) -> None:
    """解包后自检：pwn 能不能 import（VPS 上 `--install ... --selftest` 直接验）。"""
    import subprocess  # noqa: PLC0415
    env = dict(__import__("os").environ)
    env["PYTHONPATH"] = f"{toolkit / 'pylibs'}:{env.get('PYTHONPATH', '')}"
    code = ("import pwn, pwnlib;"
            "print('pwntools', pwn.__version__ if hasattr(pwn,'__version__') else 'ok');"
            "from pwn import remote, ELF, ROP;"
            "print('pwn imports OK')")
    result = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True)
    print(result.stdout.strip() or result.stderr.strip()[:2000])
    if result.returncode != 0:
        raise SystemExit("pwntools 自检失败")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--bundle", type=pathlib.Path)
    parser.add_argument("--install", type=pathlib.Path)
    parser.add_argument("--toolkit", type=pathlib.Path, default=TOOLKIT)
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--selftest", action="store_true",
                        help="install 后 import pwn 自检（需在 Linux/VM 里跑）")
    args = parser.parse_args()
    manifest = default_manifest()
    if args.list:
        cmd_list(manifest)
        return 0
    if args.bundle:
        cmd_bundle(manifest, args.bundle)
        return 0
    if args.install:
        cmd_install(args.install, args.toolkit, args.verify)
        if args.selftest:
            cmd_selftest(args.toolkit)
        return 0
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
