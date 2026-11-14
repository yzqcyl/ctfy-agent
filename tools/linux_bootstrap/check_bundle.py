#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_bundle.py —— 离线工具箱 bundle 的**静态**验收（无需 Linux/VM）。

P0-6 验收里「在 Linux 上 `import pwn` + 远程/SSL 交互」需要 Linux 运行时（VPS/容器）；
本脚本负责在没有 Linux 机器时能证明的部分：
  1) 清单里的每个 wheel 平台/CPython 标签与目标（linux x86_64 / cp312）兼容；
  2) 依赖闭包完整：解析每个 wheel 的 METADATA `Requires-Dist`（按 linux+cp312 评估
     environment marker），确认 bundle 里有满足版本约束的发行包；
  3) 关键扩展模块确实带 .so（capstone/unicorn/cryptography 等不是空壳 wheel）；
  4) pwntools 关键文件在（pwn/__init__.py、pwnlib/tubes/ssh.py、pwnlib/rop/rop.py）；
  5) 可选：`--pip-download` 调离线 pip download 做端到端解析（Windows 上也能跑）。

用法：
    python tools/linux_bootstrap/check_bundle.py                     # 静态检查
    python tools/linux_bootstrap/check_bundle.py --pip-download      # 额外跑离线 pip 解析
"""
from __future__ import annotations

import argparse
import email.parser
import json
import pathlib
import re
import subprocess
import sys
import tempfile
import zipfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
BUNDLE = ROOT / "tools" / "linux_bootstrap" / "bundle"
TARGET = {"python": "cp312", "platform": "manylinux2014_x86_64", "abi": "cp312"}

# 目标环境（linux/cp312）下的 environment marker 变量
ENV = {
    "implementation_name": "cpython",
    "os_name": "posix",
    "platform_machine": "x86_64",
    "platform_system": "Linux",
    "python_full_version": "3.12.14",
    "python_version": "3.12",
    "sys_platform": "linux",
    "extra": "",
}

KEY_FILES = {
    "pwntools": ["pwn/__init__.py", "pwnlib/tubes/ssh.py", "pwnlib/rop/rop.py",
                 "pwnlib/tubes/remote.py"],
    "capstone": [".so"],
    "unicorn": [".so"],
    "cryptography": [".so"],
    "bcrypt": [".so"],
    "pynacl": [".so"],
    "cffi": [".so"],
    "zstandard": [".so"],
    "psutil": [".so"],
    "freetype-py": ["freetype/libfreetype.so"],
}


def normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower().strip()


def parse_marker_requirements(spec: str) -> tuple[str, list[tuple[str, str]], str]:
    """'mako>=1.0.0; python_version >= "3"' -> (name, [(op, version), ...], marker)。

    版本约束可能是逗号分隔的多个 specifier（`unicorn!=2.1.3,!=2.1.4,>=2.0.1`），
    必须全部校验；只取第一段会让明确被排除的版本混过静态检查（P0-6 四审 P2-10）。
    """
    raw = spec.strip()
    marker = ""
    if ";" in raw:
        raw, marker = raw.split(";", 1)
        raw, marker = raw.strip(), marker.strip()
    raw = raw.split("[", 1)[0].strip()
    match = re.match(r"^([A-Za-z0-9_.\-]+)\s*(.*)$", raw)
    if not match:
        return ("", [], marker)
    name, rest = match.group(1), match.group(2).strip()
    specs: list[tuple[str, str]] = []
    for piece in rest.split(","):
        piece = piece.strip()
        if not piece:
            continue
        for candidate in (">=", "==", "<=", "~=", "!=", ">", "<"):
            if piece.startswith(candidate):
                specs.append((candidate, piece[len(candidate):].strip()))
                break
    return (normalize(name), specs, marker)


_MARKER_TOKEN = re.compile(r"\s*(\()\s*|\)\s*|(\b(?:and|or|in|not)\b)\s*"
                          r"|([A-Za-z_][A-Za-z0-9_.]*)\s*(==|!=|<=|>=|<|>)\s*"
                          r"('[^']*'|\"[^\"]*\")\s*")


def _eval_marker(marker: str, env: dict) -> bool | None:
    """评估 environment marker 的 and/or/in/比较；不认识的变量返回 None。"""
    tokens: list[tuple[str, str, str]] = []
    pos = 0
    while pos < len(marker):
        match = _MARKER_TOKEN.match(marker, pos)
        if not match:
            return None
        if match.group(1):
            tokens.append(("lparen", "", ""))
        elif match.group(2):
            tokens.append((match.group(2).lower(), "", ""))
        elif match.group(3):
            value = match.group(5)
            tokens.append(("cmp", match.group(3), (match.group(4), value.strip("'\""))))
        pos = match.end()

    def value_of(name: str):
        if name in ("extra",):
            return env.get("extra", "")
        return env.get(name)

    def compare(left: str, op: str, right: str) -> bool | None:
        a = value_of(left)
        if a is None:
            return None
        if right in env:
            b = env[right]
        elif right in ("True", "False"):
            return None
        else:
            b = right

        def vt(text: str) -> tuple:
            parts = []
            for piece in re.split(r"[.\-+]", str(text)):
                m = re.match(r"^(\d+)", piece)
                parts.append(int(m.group(1)) if m else 0)
            return tuple(parts)

        def num(text: str) -> bool:
            return re.match(r"^\d", str(text)) is not None

        if op in ("in", "not in"):
            found = str(a) in str(b)
            return found if op == "in" else not found
        if num(a) and num(b):
            x, y = vt(a), vt(b)
            length = max(len(x), len(y))
            x += (0,) * (length - len(x))
            y += (0,) * (length - len(y))
            return {"==": x == y, "!=": x != y, ">=": x >= y,
                    "<=": x <= y, ">": x > y, "<": x < y}[op]
        sa, sb = str(a).lower(), str(b).lower()
        return {"==": sa == sb, "!=": sa != sb, ">=": sa >= sb,
                "<=": sa <= sb, ">": sa > sb, "<": sa < sb}[op]

    # 递归下降：expr := term (or term)*; term := factor (and factor)*
    index = 0

    def parse_expr() -> bool | None:
        nonlocal index
        value = parse_term()
        while index < len(tokens) and tokens[index][0] == "or":
            index += 1
            right = parse_term()
            if value is True or right is True:
                value = True
            elif value is None or right is None:
                value = None if (value is not False or right is not False) else False
            else:
                value = bool(value) or bool(right)
        return value

    def parse_term() -> bool | None:
        nonlocal index
        value = parse_atom()
        while index < len(tokens) and tokens[index][0] == "and":
            index += 1
            right = parse_atom()
            if value is False or right is False:
                value = False
            elif value is None or right is None:
                value = None if (value is not True or right is not True) else True
            else:
                value = bool(value) and bool(right)
        return value

    def parse_atom() -> bool | None:
        nonlocal index
        if index >= len(tokens):
            return None
        kind, name, payload = tokens[index]
        index += 1
        if kind == "lparen":
            value = parse_expr()
            if index < len(tokens) and tokens[index][0] == "rparen":
                index += 1
            return value
        if kind == "cmp":
            return compare(name, payload[0], payload[1])
        return None

    try:
        return parse_expr()
    except Exception:  # noqa: BLE001
        return None


def marker_allows(marker: str) -> bool:
    """返回 False 表示「目标环境（linux/cp312）肯定不需要」。"""
    if not marker:
        return True
    result = _eval_marker(marker, ENV)
    # 不认识 marker：保守当作需要（宁可多报缺依赖，也不要漏）
    return True if result is None else bool(result)


def version_tuple(text: str) -> tuple:
    parts = []
    for piece in re.split(r"[.\-+]", text):
        match = re.match(r"^(\d+)", piece)
        parts.append(int(match.group(1)) if match else 0)
    return tuple(parts)


def satisfies(version: str, op: str, want: str) -> bool:
    if not op or not want:
        return True
    if op not in (">=", "==", "<=", "~=", "!=", ">", "<"):
        return False
    have, need = version_tuple(version), version_tuple(want)
    length = max(len(have), len(need))
    have += (0,) * (length - len(have))
    need += (0,) * (length - len(need))
    if op == "==":
        return have == need
    if op == "!=":
        return have != need
    if op == ">=":
        return have >= need
    if op == "<=":
        return have <= need
    if op == ">":
        return have > need
    if op == "<":
        return have < need
    if op == "~=":
        return have[: len(need) - 1] == need[: len(need) - 1] and have >= need
    return True


def wheel_info(path: pathlib.Path) -> dict:
    """读 wheel 的 METADATA / 关键文件 / .so 列表。"""
    info = {"name": "", "version": "", "requires": [], "files": [], "so": [],
            "tags": path.name}
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        info["files"] = names
        info["so"] = [n for n in names if n.endswith(".so")]
        meta_name = next((n for n in names if n.endswith(".dist-info/METADATA")), "")
        if meta_name:
            raw = archive.read(meta_name).decode("utf-8", "replace")
            meta = email.parser.Parser().parsestr(raw)
            info["name"] = normalize(meta.get("Name", ""))
            info["version"] = (meta.get("Version", "") or "").strip()
            info["requires"] = meta.get_all("Requires-Dist") or []
    return info


def tag_ok(filename: str) -> tuple[bool, str]:
    lowered = filename.lower()
    pure = lowered.endswith("-none-any.whl")
    if pure:
        return True, "pure-python"
    if "manylinux" not in lowered or "x86_64" not in lowered:
        return False, "不是 manylinux x86_64 wheel"
    # 扩展 wheel：必须带 cp312 / abi3 / py3 标签之一，且不能是别的 cpX 专属
    if "-cp312-" in lowered or "-cp312-abi3-" in lowered:
        return True, "cp312"
    if "-abi3-" in lowered:
        return True, "abi3"
    if re.search(r"-py3-none-manylinux", lowered) or "-py2.py3-none-manylinux" in lowered:
        return True, "py3 通用扩展"
    match = re.search(r"-cp(\d{2,3})-", lowered)
    if match and match.group(1) != "312":
        return False, f"CPython {match.group(1)} 专属（目标 cp312）"
    return True, "other"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", default=str(BUNDLE))
    parser.add_argument("--pip-download", action="store_true")
    args = parser.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    bundle = pathlib.Path(args.bundle)
    manifest_path = bundle / "manifest.resolved.json"
    if not manifest_path.is_file():
        print(f"FAIL 缺少清单 {manifest_path}")
        return 1
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    packages: dict[str, dict] = {}
    fails: list[str] = []
    for item in manifest:
        filename = item.get("filename") or ""
        path = bundle / filename
        if not path.is_file():
            fails.append(f"清单里的 {filename} 不存在")
            continue
        ok, why = tag_ok(filename)
        if not ok:
            fails.append(f"{filename}: {why}")
        info = wheel_info(path)
        if not info["name"]:
            fails.append(f"{filename}: 读不到 METADATA Name")
            continue
        packages[info["name"]] = info
        print(f"[ok] {info['name']}=={info['version']} ({why})")
    print(f"\n清单 {len(manifest)} 项，解析出 {len(packages)} 个发行包")
    # 依赖闭包
    missing: list[str] = []
    for name, info in sorted(packages.items()):
        for spec in info["requires"]:
            dep, specs, marker = parse_marker_requirements(spec)
            if not dep or not marker_allows(marker):
                continue
            found = packages.get(dep)
            if found is None:
                missing.append(f"{name} 需要 {dep}，bundle 里没有")
                continue
            for op, want in specs:
                if not satisfies(found["version"], op, want):
                    missing.append(f"{name} 需要 {dep}{op}{want}，bundle 里是 {found['version']}")
                    break
    if missing:
        fails.extend(missing)
        for line in missing:
            print(f"[FAIL] {line}")
    else:
        print("[ok] 依赖闭包完整（linux/cp312 口径）")
    # 关键文件
    for name, wanted in KEY_FILES.items():
        info = packages.get(name)
        if info is None:
            fails.append(f"bundle 缺 {name}")
            continue
        for token in wanted:
            if token == ".so":
                # 有的 wheel 把共享库放成 libxxx.so.2（unicorn），不一定是 *.so
                hit = any(".so" in name for name in info["files"])
            else:
                hit = token in info["files"]
            if not hit:
                fails.append(f"{name} 缺 {token}")
        print(f"[ok] {name} 关键文件齐全")
    # 可选：离线 pip 解析（Windows 上也能跑，不执行 Linux 代码）
    if args.pip_download:
        # 不走 tempfile.TemporaryDirectory：Windows 沙箱对系统临时目录的 chmod 清理会
        # WinError 5，导致「明明解析成功却抛异常」。改在仓库 runs/_tmp 下建唯一目录，
        # 并把 pip 自己的 TEMP 也指过去（清理失败忽略即可）。
        import os
        import shutil

        base = ROOT / "runs" / "_tmp"
        try:
            base.mkdir(parents=True, exist_ok=True)
        except OSError:
            base = pathlib.Path(tempfile.gettempdir())
        tmp = base / f"pipcheck-{os.getpid()}"
        shutil.rmtree(tmp, ignore_errors=True)
        (tmp / "tmp").mkdir(parents=True, exist_ok=True)
        env = dict(os.environ)
        env["TEMP"] = str(tmp / "tmp")
        env["TMP"] = str(tmp / "tmp")
        command = [sys.executable, "-m", "pip", "download", "--no-index",
                   "--find-links", str(bundle), "--dest", str(tmp),
                   "--only-binary=:all:",
                   "--platform", TARGET["platform"], "--python-version", "3.12",
                   "--implementation", "cp", "--abi", TARGET["abi"], "--no-cache-dir",
                   "pwntools"]
        result = subprocess.run(command, capture_output=True, text=True, env=env)
        wheels = list(tmp.glob("*.whl"))
        if result.returncode != 0:
            fails.append(f"离线 pip 解析失败：{result.stderr.strip()[-300:]}")
        elif len(wheels) < 30:
            fails.append(f"离线 pip 只解析出 {len(wheels)} 个 wheel（预期 >30）")
        else:
            print(f"[ok] 离线 pip 解析出 {len(wheels)} 个 wheel（依赖闭包可满足）")
        try:
            shutil.rmtree(tmp)
        except OSError as exc:
            print(f"[warn] 临时目录清理失败（可手动删）：{tmp}：{exc}")
    print()
    if fails:
        for line in fails:
            print(f"[FAIL] {line}")
        print(f"RESULT=FAIL（{len(fails)} 项）")
        return 1
    print("RESULT=PASS（静态验收；Linux 上 `import pwn` 的运行时验证留待 VPS："
          "provision.py --install ./bundle --selftest）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())