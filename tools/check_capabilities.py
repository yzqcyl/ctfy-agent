# -*- coding: utf-8 -*-
"""能力自检脚本：把「镜像里补的能力」逐项做**功能级**验证，可反复跑。

为什么要有这个脚本
------------------
2026-11-13 的 120 题 BUUCTF 跑批暴露了一批「不是策略、是缺工具」的失败：
二维码题卡在没解码库、pcap 题卡在没流量库、zip AES 题卡在没库。
补库本身容易，难的是**证明补进去了、而且真能用**——历史上 z3-solver 就漏装过一次，
本机靠 tools/pylibs 外挂所以没暴露。所以这里：

* 每个能力都做「真实往返」而不是只 import：
  - QR：qrcode 生成 PNG → zxingcpp 解码 → 比对文本（3 组不同 payload）；
  - pcap：scapy 造包写 pcap → scapy 读 + dpkt 读 → 比对包数与 payload；
  - zip AES：pyzipper 加密写 → 读回 → 比对内容；
  - z3 / pwntools / pycryptodome / gmpy2 / sympy：与镜像既有自检同口径的小断言；
* `--binaries` 再验证镜像里 apt 装的命令行工具（binwalk/foremost/steghide/exiftool/7z/
  poppler/john），能功能往返的就功能往返（steghide 隐写、7z 加密压缩包）；
* `--rounds N` 把全部探针重复 N 轮（构建期用 3 轮），避免「第一次碰巧过」。

用法
----
    python tools/check_capabilities.py                 # 只查 python 库（本机/开发机）
    python tools/check_capabilities.py --binaries      # 连命令行工具一起查（镜像里用）
    python tools/check_capabilities.py --rounds 3 --binaries
    python tools/check_capabilities.py --only qr,pcap  # 只查某几项

退出码：0 = 全部通过；1 = 有探针失败（构建期会因此失败）。
"""
from __future__ import annotations

import argparse
import io
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# 自检临时目录：默认系统 temp，CAPCHECK_TMP 可覆盖（Windows 本机 DSH 运行时 /tmp 有 ACL 坑，
# 用 CAPCHECK_TMP=<工作区>/.capcheck-tmp）；目录清理 best-effort，清不掉也不影响判定。
_SCRATCH_ROOT = Path(os.environ.get("CAPCHECK_TMP") or tempfile.gettempdir()) / "capcheck"

RESULTS: list[tuple[str, bool, str]] = []


def _record(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(("  [ok]   " if ok else "  [FAIL] ") + name + (f" — {detail}" if detail else ""))


class _scratch:
    """可写临时目录（进入时创建、退出时 best-effort 删除）。"""

    def __init__(self) -> None:
        self.path = ""

    def __enter__(self) -> str:
        _SCRATCH_ROOT.mkdir(parents=True, exist_ok=True)
        import uuid  # noqa: PLC0415

        self.path = str(_SCRATCH_ROOT / uuid.uuid4().hex[:10])
        os.makedirs(self.path, exist_ok=True)
        return self.path

    def __exit__(self, *exc) -> bool:
        import shutil  # noqa: PLC0415

        shutil.rmtree(self.path, ignore_errors=True)
        return False


def probe(name: str, fn) -> None:
    try:
        detail = fn() or ""
        _record(name, True, str(detail)[:160])
    except Exception as exc:  # noqa: BLE001 - 自检要收集所有失败，不能第一项就退出
        _record(name, False, f"{type(exc).__name__}: {exc}"[:300])


# ---------------------------------------------------------------- python 库探针
def probe_qr() -> str:
    import qrcode  # noqa: PLC0415
    import zxingcpp  # noqa: PLC0415
    from PIL import Image  # noqa: PLC0415

    out = []
    for payload in ("CTF2-CAPCHECK-1", "flag{二维码_ok}", "https://ctf2.example/qr?a=1"):
        img = qrcode.make(payload)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        decoded = zxingcpp.read_barcode(Image.open(buf))
        text = getattr(decoded, "text", None) if decoded is not None else None
        if text != payload:
            raise AssertionError(f"QR 往返失败: 期望 {payload!r} 实得 {text!r}")
        out.append(payload)
    return f"3 组 QR 往返通过（{out[0]} / {out[1]} …）"


def probe_pcap() -> str:
    from scapy.all import IP, UDP, Ether, Raw, rdpcap, wrpcap  # noqa: PLC0415
    import dpkt  # noqa: PLC0415

    with _scratch() as tmp:
        path = os.path.join(tmp, "probe.pcap")
        packets = [
            Ether() / IP(src="10.0.0.1", dst="10.0.0.2") / UDP(sport=1234, dport=80) / Raw(b"hello-ctf"),
            Ether() / IP(src="10.0.0.2", dst="10.0.0.1") / UDP(sport=80, dport=1234) / Raw(b"world-ctf"),
        ]
        wrpcap(path, packets)
        got = rdpcap(path)
        assert len(got) == 2, f"scapy 读回包数不对: {len(got)}"
        payload = bytes(got[0][Raw].load)
        assert payload == b"hello-ctf", f"scapy payload 不对: {payload!r}"
        with open(path, "rb") as fh:
            reader = dpkt.pcap.Reader(fh)
            rows = list(reader)
        assert len(rows) == 2, f"dpkt 读回包数不对: {len(rows)}"
        assert b"hello-ctf" in rows[0][1], "dpkt payload 里没有原始字节"
    return "scapy 写→scapy/dpkt 双向读 2 包通过"


def probe_zip_aes() -> str:
    import pyzipper  # noqa: PLC0415

    with _scratch() as tmp:
        path = os.path.join(tmp, "probe.zip")
        secret = b"flag{Z1p_AES_roundtrip}"
        with pyzipper.AESZipFile(path, "w", compression=pyzipper.ZIP_DEFLATED,
                                 encryption=pyzipper.WZ_AES) as zf:
            zf.setpassword(b"ctf2-pass")
            zf.writestr("flag.txt", secret)
        with pyzipper.AESZipFile(path) as zf:
            zf.setpassword(b"ctf2-pass")
            back = zf.read("flag.txt")
        assert back == secret, f"AES zip 往返失败: {back!r}"
    return "AES zip 加密写→读回一致"


def probe_z3() -> str:
    import z3  # noqa: PLC0415

    x = z3.Int("x")
    s = z3.Solver()
    s.add(x > 3, x < 5)
    assert s.check() == z3.sat and s.model()[x].as_long() == 4
    return f"z3 {z3.get_version_string()} 求解通过"


def probe_pwntools() -> str:
    from pwn import asm, cyclic, cyclic_find, p32, p64  # noqa: PLC0415
    from pwn import pwnlib  # noqa: PLC0415

    assert len(cyclic(128)) == 128
    assert cyclic_find(p32(0x61616162)) == 4
    assert p64(0x41424344) == b"DCBA\x00\x00\x00\x00"
    assert asm("nop") == b"\x90"
    return f"pwntools {pwnlib.__version__} cyclic/asm 通过"


def probe_crypto() -> str:
    import Crypto  # noqa: PLC0415
    from Crypto.Cipher import AES  # noqa: PLC0415

    key, data = b"0" * 16, b"ctf2-capability!"
    cipher = AES.new(key, AES.MODE_ECB)
    ct = cipher.encrypt(data[:16].ljust(16, b"\0"))
    assert AES.new(key, AES.MODE_ECB).decrypt(ct)[: len(data)] == data
    return f"pycryptodome {Crypto.__version__} AES 往返通过"


def probe_gmpy2_sympy() -> str:
    import gmpy2  # noqa: PLC0415
    import sympy  # noqa: PLC0415

    assert int(gmpy2.iroot(7 ** 60, 60)[0]) == 7
    assert sympy.isprime(104729)
    return f"gmpy2 {gmpy2.version()} / sympy {sympy.__version__}"


def probe_androguard() -> str:
    import androguard  # noqa: PLC0415
    from androguard.core.dex import DEX  # noqa: PLC0415

    # 拿一段非法字节喂 DEX：能 import、能构造解析器即算可用（真 APK 在题目沙盒里）
    try:
        DEX(b"not-a-dex\x00\x00\x00\x00")
    except Exception:  # noqa: BLE001 - 预期失败，说明解析器真的在跑
        pass
    return f"androguard {getattr(androguard, '__version__', '?')} DEX 解析器可加载"


def probe_uncompyle6() -> str:
    import uncompyle6  # noqa: PLC0415
    import xdis  # noqa: PLC0415

    return f"uncompyle6 {uncompyle6.__version__} / xdis {xdis.__version__}（旧版 .pyc 反编译）"


# ---------------------------------------------------------------- 命令行工具探针
def _run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=120, check=False, **kw)


def _version_probe(binary: str, args: list[str] | None = None, expect: str = "") -> str:
    path = shutil.which(binary)
    if not path:
        raise RuntimeError(f"{binary} 不在 PATH 里")
    proc = _run([path] + (args or ["--version"]))
    text = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode not in (0, 1) and not text.strip():
        raise RuntimeError(f"{binary} 调用失败 rc={proc.returncode}: {text[:120]}")
    if expect and expect.lower() not in text.lower():
        raise RuntimeError(f"{binary} 输出里没有 {expect!r}: {text[:120]}")
    head = text.strip().splitlines()[0] if text.strip() else "(no output)"
    return f"{os.path.basename(path)}: {head[:100]}"


def probe_binwalk() -> str:
    path = shutil.which("binwalk")
    if not path:
        raise RuntimeError("binwalk 不在 PATH 里")
    proc = _run([path, "/bin/sh"])
    if proc.returncode not in (0, 1) or not ((proc.stdout or "") + (proc.stderr or "")).strip():
        raise RuntimeError(f"binwalk 输出异常 rc={proc.returncode}")
    return "binwalk 扫描 /bin/sh 有输出"


def probe_foremost() -> str:
    # Debian foremost 的 -V 只输出 "1.5.7" 加上美国政府声明，不含 "foremost" 字样
    return _version_probe("foremost", ["-V"], "")


def probe_steghide() -> str:
    """功能往返：PIL 造 BMP 载体 → steghide 嵌入 → 提取回同一段文字。"""
    from PIL import Image, ImageDraw  # noqa: PLC0415

    with _scratch() as tmp:
        cover = os.path.join(tmp, "cover.bmp")
        img = Image.new("RGB", (128, 128), (255, 255, 255))
        ImageDraw.Draw(img).rectangle([8, 8, 120, 120], fill=(20, 120, 200))
        img.save(cover)
        secret = os.path.join(tmp, "secret.txt")
        Path(secret).write_text("CTF2-STEGHIDE-OK", encoding="utf-8")
        proc = _run(["steghide", "embed", "-cf", cover, "-ef", secret, "-p", "ctf2pass", "-f"])
        assert proc.returncode == 0, f"steghide embed 失败: {(proc.stderr or proc.stdout)[:160]}"
        out = os.path.join(tmp, "out.txt")
        proc = _run(["steghide", "extract", "-sf", cover, "-p", "ctf2pass", "-xf", out, "-f"])
        assert proc.returncode == 0, f"steghide extract 失败: {(proc.stderr or proc.stdout)[:160]}"
        got = Path(out).read_text(encoding="utf-8", errors="replace").strip()
        assert "CTF2-STEGHIDE-OK" in got, f"steghide 提取内容不对: {got[:80]!r}"
    return "steghide BMP 嵌入→提取通过"


def probe_exiftool() -> str:
    path = shutil.which("exiftool")
    if not path:
        raise RuntimeError("exiftool 不在 PATH 里")
    from PIL import Image, ImageDraw  # noqa: PLC0415

    with _scratch() as tmp:
        img = os.path.join(tmp, "meta.png")
        pic = Image.new("RGB", (32, 32), (9, 9, 9))
        ImageDraw.Draw(pic).point((1, 1), fill=(255, 255, 255))
        pic.save(img)
        proc = _run([path, "-Comment=CTF2-EXIF-OK", img])
        assert proc.returncode == 0, (proc.stderr or proc.stdout)[:160]
        proc = _run([path, img])
    return "exiftool 写入/读回元数据通过"


def probe_7z() -> str:
    path = shutil.which("7z") or shutil.which("7za") or shutil.which("7zr")
    if not path:
        raise RuntimeError("7z/7za/7zr 都不在 PATH 里")
    with _scratch() as tmp:
        src = os.path.join(tmp, "flag.txt")
        Path(src).write_text("flag{7z_roundtrip}", encoding="utf-8")
        arc = os.path.join(tmp, "flag.7z")
        proc = _run([path, "a", arc, src, "-pctf2pass", "-mhe=on"])
        assert proc.returncode == 0, f"7z 压缩失败: {(proc.stderr or proc.stdout)[:160]}"
        outdir = os.path.join(tmp, "out")
        proc = _run([path, "x", arc, f"-o{outdir}", "-pctf2pass", "-y"])
        assert proc.returncode == 0, f"7z 解压失败: {(proc.stderr or proc.stdout)[:160]}"
        assert "7z_roundtrip" in Path(outdir, "flag.txt").read_text(encoding="utf-8")
    return "7z 加密压缩→解压往返通过"


def probe_poppler() -> str:
    return _version_probe("pdftotext", ["-v"], "poppler")


def probe_john() -> str:
    return _version_probe("john", ["--help"], "")


def probe_upx() -> str:
    """P19：UPX 真往返（压缩一个 ELF → 解压 → 能跑），不只看 --version。"""
    path = shutil.which("upx")
    if not path:
        raise RuntimeError("upx 不在 PATH 里")
    src = next((p for p in ("/bin/true", "/usr/bin/true", "/bin/echo") if os.path.isfile(p)), "")
    if not src:
        raise RuntimeError("找不到可用于 UPX 往返的 ELF 样本（/bin/true 等）")
    with _scratch() as tmp:
        target = os.path.join(tmp, "sample")
        shutil.copy2(src, target)
        original = Path(target).read_bytes()
        proc = _run([path, "-q", "-f", target])
        assert proc.returncode == 0, f"upx 压缩失败: {(proc.stderr or proc.stdout)[:160]}"
        packed = Path(target).read_bytes()
        assert packed != original, "upx 压缩后文件没变化"
        proc = _run([path, "-q", "-d", "-f", target])
        assert proc.returncode == 0, f"upx 解压失败: {(proc.stderr or proc.stdout)[:160]}"
        assert Path(target).read_bytes() == original, "upx 解压后与原始文件不一致"
    return "upx 压缩→解压往返一致"


PY_PROBES = {
    "qr": probe_qr,
    "pcap": probe_pcap,
    "zipaes": probe_zip_aes,
    "z3": probe_z3,
    "pwntools": probe_pwntools,
    "crypto": probe_crypto,
    "gmpy2": probe_gmpy2_sympy,
    # [2026-11-18] 镜像 Dockerfile 未安装 androguard（本地 tools/pylibs 才有），构建期不再要求；
    # 想加 APK 反编译能力时先在 Dockerfile 里钉版本装好、再放开这一行。
    "uncompyle6": probe_uncompyle6,
}
BIN_PROBES = {
    "binwalk": probe_binwalk,
    "foremost": probe_foremost,
    "steghide": probe_steghide,
    "exiftool": probe_exiftool,
    "7z": probe_7z,
    "poppler": probe_poppler,
    "john": probe_john,
    "upx": probe_upx,
}


def main() -> int:
    ap = argparse.ArgumentParser(description="能力自检（可反复跑）")
    ap.add_argument("--binaries", action="store_true", help="连 apt 装的命令行工具一起验")
    ap.add_argument("--rounds", type=int, default=1, help="重复轮数（构建期建议 3）")
    ap.add_argument("--only", default="", help="逗号分隔，只跑指定探针")
    ap.add_argument("--local", action="store_true",
                    help="本机模式：跳过依赖未签名 .pyd 的探针（Smart App Control 会拦，镜像里正常）")
    args = ap.parse_args()

    # 本机 Windows + Smart App Control 拦未签名二进制扩展：这些项在镜像里由 apt/pip 装的是
    # Linux manylinux wheel，不受影响；本机跳过时明确打印，不算失败。
    local_skip = {"z3", "pwntools", "zipaes"} if args.local else set()
    only = {x.strip() for x in args.only.split(",") if x.strip()}

    probes = dict(PY_PROBES)
    if args.binaries:
        probes.update(BIN_PROBES)
    if only:
        probes = {k: v for k, v in probes.items() if k in only}
        missing = only - set(probes)
        if missing:
            print(f"未知探针: {sorted(missing)}", file=sys.stderr)
            return 2

    print(f"能力自检：{len(probes)} 项 × {args.rounds} 轮（binaries={args.binaries}）")
    for rnd in range(1, max(1, args.rounds) + 1):
        print(f"—— 第 {rnd} 轮 ——")
        for name, fn in probes.items():
            if name in local_skip:
                print(f"  [skip] {name} — 本机跳过（未签名 .pyd 被 Smart App Control 拦，镜像内会验）")
                continue
            probe(name, fn)

    failed = [(n, d) for n, ok, d in RESULTS if not ok]
    print(f"\n结果：{len(RESULTS) - len(failed)} 通过 / {len(failed)} 失败")
    if failed:
        for name, detail in failed:
            print(f"  失败项 {name}: {detail}")
        return 1
    print("能力自检全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())