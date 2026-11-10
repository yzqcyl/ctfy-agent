#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""smoke_attachments.py — 题目附件自动下载（attachments.py）离线回归。

背景（2026-11-10 runs/plat 实测）：13 道未解题里 7 道卡在「沙盒目录为空」，而平台本来
就下发附件（get_practice_challenge 里有 files[]，且 ctf2_get_attachment_url 能给签名链）。
本次新增 attachments.py 在题目就绪时主动把附件拉进沙盒。

本测试全离线：假平台 + 本地 HTTP 服务，不连真实平台、不消耗任何额度。
覆盖：URL 模式落盘 / sha256 一致 / 名字消毒 / 体积上限 / 单个失败不放弃 /
      清单不泄漏签名 / 重复跑跳过 / 内容模式（CTFY 式）落盘 / 无附件时静默。

运行: PYTHONPATH=src python tests/smoke_attachments.py
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import shutil
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ctfy_agent import attachments  # noqa: E402

FAILS: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"[{'ok' if cond else 'FAIL'}] {name}" + (f"  {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


# ------------------------------------------------------------------ 本地 HTTP 服务
PAYLOAD = b"#!/usr/bin/env python\nprint('flag{pickle_rce}')\n"
BIG = b"A" * (attachments.MAX_PERSIST_BYTES + 1024)


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path.startswith("/ok"):
            body = PAYLOAD
        elif self.path.startswith("/big"):
            body = BIG
        else:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # 静音
        pass


def _serve():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


# ------------------------------------------------------------------ 假平台
class FakePlatform:
    """鸭子类型的平台：只实现 attachments.py 用到的那几个方法。"""

    def __init__(self, files, base_url, *, attachment_tool="ctf2_get_attachment_url",
                 content_tool="", content_map=None, bad_file_ids=()):
        self._files = files
        self._base = base_url
        self._attachment_tool = attachment_tool
        self._content_tool = content_tool
        self._content_map = content_map or {}
        self._bad = set(bad_file_ids)
        self.calls: list[tuple[str, dict]] = []

    # --- 平台接口面 ---
    def tool_names(self):
        names = ["ctf2_get_practice_challenge", "ctf2_submit_flag"]
        if self._attachment_tool:
            names.append(self._attachment_tool)
        if self._content_tool:
            names.append(self._content_tool)
        return names

    def resolve(self, kind):
        if kind == "get_challenge":
            return "ctf2_get_practice_challenge"
        raise KeyError(kind)

    def schema_of(self, name):
        if name == "ctf2_get_attachment_url":
            return {
                "type": "object",
                "additionalProperties": False,
                "required": ["practice_ground_id", "challenge_id", "file_id"],
                "properties": {
                    "practice_ground_id": {"type": "string"},
                    "challenge_id": {"type": "string"},
                    "file_id": {"type": "string"},
                },
            }
        return {}

    def prop_names(self, name):
        return set((self.schema_of(name).get("properties") or {}))

    def call_raw(self, name, arguments=None, *, retries=0):
        args = dict(arguments or {})
        self.calls.append((name, args))
        if name == "ctf2_get_practice_challenge":
            return {"structuredContent": {"challenge": {"id": "c1", "files": self._files}}}
        if name == self._attachment_tool:
            fid = args.get("file_id")
            if fid in self._bad:
                return {"structuredContent": {"attachment": {"file_id": fid}}}
            path = "/big" if fid == "big" else "/ok"
            url = f"{self._base}{path}?token=SECRET-TOKEN-{fid}"
            return {"structuredContent": {"attachment": {"file_id": fid, "file_name": fid,
                                                         "url": url}}}
        raise AssertionError(f"unexpected tool {name}")

    def call_text(self, name, arguments=None, *, retries=0):
        self.calls.append((name, dict(arguments or {})))
        payload = self._content_map.get(dict(arguments or {}).get("file_id"), "")
        if not payload:
            return "{}"
        envelope = {"filename": payload["name"], "size": len(payload["content"]),
                    "encoding": "base64", "content": payload["content"]}
        return "----- 工具返回 -----\n" + json.dumps(envelope)

    def _as_json(self, payload):
        # 模仿真实平台：拆信封 + 单键包装
        obj = payload
        if isinstance(obj, dict) and isinstance(obj.get("structuredContent"), dict):
            obj = obj["structuredContent"]
        if isinstance(obj, dict) and len(obj) == 1:
            (key, inner), = obj.items()
            if key in ("challenge", "attachment") and isinstance(inner, dict):
                return inner
        return obj


class FakeStore:
    def __init__(self, root: pathlib.Path):
        self.run_dir = root
        self.notes: dict[str, list[str]] = {}
        self.saved = 0

    def add_note(self, cid, text):
        self.notes.setdefault(cid, []).append(text)

    def save(self):
        self.saved += 1


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> int:
    srv, base = _serve()
    # 注意（本机实测）：① 受限环境 %TEMP% 不可写；② tempfile.mkdtemp 用 os.mkdir(...,0o700)
    # 建出来的目录在当前 Windows 组合下连创建者自己都读不了（PermissionError），
    # 所以临时根必须放工作区、且用普通 mkdir 建（HANDOFF-2026-11-10 §9）。
    tmp = ROOT / "runs" / f"_t_attach_{os.getpid()}"
    if tmp.exists():
        shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True, exist_ok=True)
    store = FakeStore(tmp)
    cid = "chall-1"

    # 1) URL 模式：正常落盘
    plat = FakePlatform(
        files=[{"id": "f1", "file": {"original_name": "unipickle.py", "size": len(PAYLOAD)}}],
        base_url=base,
    )
    recs = attachments.fetch_challenge_files(plat, store, cid, competition_id="g1")
    target = attachments.sandbox_root(store, cid) / "unipickle.py"
    check("附件落进沙盒", target.is_file())
    check("字节与平台一致", target.is_file() and target.read_bytes() == PAYLOAD)
    check("sha256 正确", recs and recs[0].get("sha256") == _sha(PAYLOAD))
    check("records 标记 ok", recs and recs[0].get("ok") is True)
    check("清单文件已写", (attachments.sandbox_root(store, cid) / attachments.MANIFEST_NAME).is_file())

    # 2) 签名不落盘/不泄漏
    manifest = (attachments.sandbox_root(store, cid) / attachments.MANIFEST_NAME).read_text(encoding="utf-8")
    check("清单不含签名 token", "SECRET-TOKEN" not in manifest)
    note = attachments.manifest_note(recs)
    check("笔记不含签名 token", "SECRET-TOKEN" not in note)
    check("笔记给出文件名", "unipickle.py" in note)

    # 3) 重复跑：已存在且大小一致 → 跳过下载（省带宽）
    plat2 = FakePlatform(
        files=[{"id": "f1", "file": {"original_name": "unipickle.py", "size": len(PAYLOAD)}}],
        base_url=base,
    )
    recs2 = attachments.fetch_challenge_files(plat2, store, cid)
    check("重复跑跳过下载", recs2 and recs2[0].get("skipped") is True)

    # 4) 文件名消毒：路径穿越被拍平，不会写到沙盒外
    plat3 = FakePlatform(files=[{"id": "f2", "name": "../../evil.sh", "size": len(PAYLOAD)}],
                         base_url=base)
    store3 = FakeStore(tmp / "r3")
    recs3 = attachments.fetch_challenge_files(plat3, store3, "c3")
    root3 = attachments.sandbox_root(store3, "c3")
    path3 = root3 / "evil.sh"
    check("穿越文件名被拍平到沙盒内", path3.is_file(), str(list(root3.iterdir()) if root3.is_dir() else "no dir"))
    check("没有写到沙盒外", not (tmp / "evil.sh").exists())

    # 5) 体积上限：超限放弃且不留 .part
    plat4 = FakePlatform(files=[{"id": "big", "name": "huge.bin", "size": len(BIG)}], base_url=base)
    store4 = FakeStore(tmp / "r4")
    recs4 = attachments.fetch_challenge_files(plat4, store4, "c4")
    check("超限附件标失败", recs4 and recs4[0].get("ok") is False, str(recs4))
    check("失败原因提到上限", recs4 and "上限" in str(recs4[0].get("error")), str(recs4))
    root4 = attachments.sandbox_root(store4, "c4")
    check("超限不留残片", not list(root4.glob("*.part")) if root4.is_dir() else True)

    # 6) 单个附件失败不放弃其它（一个没有 URL、一个正常）
    plat5 = FakePlatform(
        files=[{"id": "f_bad", "name": "broken.bin"}, {"id": "f_ok", "name": "good.bin"}],
        base_url=base, bad_file_ids={"f_bad"},
    )
    store5 = FakeStore(tmp / "r5")
    recs5 = attachments.fetch_challenge_files(plat5, store5, "c5")
    ok5 = [r for r in recs5 if r.get("ok")]
    bad5 = [r for r in recs5 if not r.get("ok")]
    check("部分失败仍下其它附件", len(ok5) == 1 and len(bad5) == 1, str(recs5))
    check("失败项有原因", bad5 and bad5[0].get("error"))

    # 7) 内容模式（CTFY 式：工具直接回 base64 内容）
    import base64

    content = b"ELF\x7f fake bytes"
    platc = FakePlatform(files=[{"id": "cf1", "name": "vaultsvc.bin", "size": len(content)}],
                         base_url=base, attachment_tool="", content_tool="download_challenge_attachment",
                         content_map={"cf1": {"name": "vaultsvc.bin",
                                              "content": base64.b64encode(content).decode()}})
    storec = FakeStore(tmp / "rc")
    recsc = attachments.fetch_challenge_files(platc, storec, "cc")
    cpath = attachments.sandbox_root(storec, "cc") / "vaultsvc.bin"
    check("内容模式落盘", cpath.is_file() and cpath.read_bytes() == content, str(recsc))
    check("内容模式 sha256 一致", recsc and recsc[0].get("sha256") == _sha(content))

    # 8) 无附件：静默返回空，不写笔记
    plat6 = FakePlatform(files=[], base_url=base)
    store6 = FakeStore(tmp / "r6")
    note6 = attachments.hydrate(plat6, store6, "c6")
    check("无附件时静默", note6 == "" and not store6.notes)

    # 9) hydrate 把清单写进 store 笔记（会进协调者 brief）
    plat7 = FakePlatform(files=[{"id": "f1", "name": "a.py", "size": len(PAYLOAD)}], base_url=base)
    store7 = FakeStore(tmp / "r7")
    note7 = attachments.hydrate(plat7, store7, "c7")
    check("hydrate 写 store 笔记", bool(store7.notes.get("c7")) and "附件已落盘" in store7.notes["c7"][0])
    check("hydrate 返回清单文本", "a.py" in note7)

    # 10) 平台完全取不到题目：不抛异常
    class BoomPlatform(FakePlatform):
        def call_raw(self, name, arguments=None, *, retries=0):
            raise RuntimeError("boom")

    store8 = FakeStore(tmp / "r8")
    recs8 = attachments.fetch_challenge_files(BoomPlatform([], base), store8, "c8")
    check("平台报错时不抛出", recs8 == [])

    # 11) 真实 DASCTF 回执形状：files[] 藏在 content[0].text 的 JSON 字符串里
    #     （2026-11-10 实测：只解 structuredContent 的旧实现会误报「题目没有附件」，
    #      把 unipickle.py 漏掉；这一条钉住 _dig_envelope）
    files_real = [{"id": "9b8f8c7b", "file": {"original_name": "unipickle.py",
                                              "size": len(PAYLOAD)}}]

    class TextEnvelopePlatform(FakePlatform):
        """只回 content 文本信封（没有 structuredContent），模拟真实工具回执。"""

        def call_raw(self, name, arguments=None, *, retries=0):
            if name == "ctf2_get_practice_challenge":
                self.calls.append((name, dict(arguments or {})))
                text = json.dumps({"challenge": {"id": "c1", "files": files_real}})
                return {"content": [{"type": "text", "text": text}],
                        "isError": False}
            return super().call_raw(name, arguments, retries=retries)

    plat9 = TextEnvelopePlatform(files=files_real, base_url=base)
    store9 = FakeStore(tmp / "r9")
    recs9 = attachments.fetch_challenge_files(plat9, store9, "c9")
    path9 = attachments.sandbox_root(store9, "c9") / "unipickle.py"
    check("content 文本信封也能挖出 files[]", path9.is_file(), str(recs9))
    check("content 文本信封下载字节一致", path9.is_file() and path9.read_bytes() == PAYLOAD)

    # 12) 详情工具选错时的回退：比赛版查练习场题拿不到 files[] → 自动换练习场版
    class FallbackPlatform(FakePlatform):
        """resolve 指向比赛版工具（查练习场题没 files），兄弟工具才有。"""

        def tool_names(self):
            return ["ctf2_get_competition_challenge", "ctf2_get_practice_challenge",
                    "ctf2_get_attachment_url", "ctf2_submit_flag"]

        def resolve(self, kind):
            if kind == "get_challenge":
                return "ctf2_get_competition_challenge"
            raise KeyError(kind)

        def call_raw(self, name, arguments=None, *, retries=0):
            if name == "ctf2_get_competition_challenge":
                self.calls.append((name, dict(arguments or {})))
                raise RuntimeError("缺少必填参数 stage_id")
            if name == "ctf2_get_practice_challenge":
                self.calls.append((name, dict(arguments or {})))
                return {"structuredContent": {"challenge": {"id": "c1", "files": files_real}}}
            return super().call_raw(name, arguments, retries=retries)

    plat10 = FallbackPlatform(files=[], base_url=base)  # 主工具回执里没有 files[]
    store10 = FakeStore(tmp / "r10")
    recs10 = attachments.fetch_challenge_files(plat10, store10, "c10", competition_id="g1")
    used = [n for n, _ in plat10.calls]
    path10 = attachments.sandbox_root(store10, "c10") / "unipickle.py"
    check("详情工具选错会回退到兄弟工具", path10.is_file(), f"calls={used} recs={recs10}")
    check("回退时先试映射工具再试练习场版",
          used.index("ctf2_get_practice_challenge") > used.index("ctf2_get_competition_challenge")
          if "ctf2_get_competition_challenge" in used else True, f"calls={used}")

    srv.shutdown()
    print()
    if FAILS:
        print(f"smoke_attachments 失败 {len(FAILS)} 项：" + "、".join(FAILS))
        return 1
    print("smoke_attachments 全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())