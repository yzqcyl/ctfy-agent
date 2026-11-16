"""离线附件回归：附件真的被复制进题目沙盒、题面能看到、marker 有记录。

为什么重要：附件是「离线单题」最常见的输入（压缩包/二进制/源码）。以前只能把路径写进题面
让模型自己去猜位置；现在要求开始前就把文件复制到 shell cwd 下的 attachments/，
这里逐条验证，包括中文名、二进制内容、同名去重、坏路径报错。
"""
from __future__ import annotations

import inspect
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ctfy_agent.config import load  # noqa: E402
from ctfy_agent import hunt  # noqa: E402
from ctfy_agent.state import Store  # noqa: E402

FAILS: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"[{'ok' if cond else 'FAIL'}] {name}" + (f"  {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


def main() -> int:
    tmp = ROOT / "runs" / "_t_attach"
    shutil.rmtree(tmp, ignore_errors=True)
    try:
        src_dir = tmp / "src"
        src_dir.mkdir(parents=True)
        txt = src_dir / "题面附件.txt"
        txt.write_text("hello 附件", encoding="utf-8")
        raw = src_dir / "bin.dat"
        raw.write_bytes(bytes(range(256)) * 4)
        # 同名文件（不同目录）→ 复制时应自动改名，不覆盖
        dup_dir = tmp / "dup"
        dup_dir.mkdir()
        dup = dup_dir / "题面附件.txt"
        dup.write_text("第二个同名文件", encoding="utf-8")

        config = load()
        config.runs_dir = str(tmp / "runs")
        config.token = ""
        config.mcp_url = "offline://"

        cid = hunt._seed_run(
            config, urls=["http://127.0.0.1:9/"], category="misc", difficulty="easy",
            name="附件 测试", brief="题面正文：看附件", run_id="att-01",
            attachments=[str(txt), str(raw), str(dup)],
        )

        run_dir = Path(config.run_dir("att-01"))
        sandbox = run_dir / "sandbox" / cid
        att_dir = sandbox / hunt.ATTACH_SUBDIR
        copied = sorted(p.name for p in att_dir.iterdir() if p.is_file())
        check("附件被复制进题目工作目录的 attachments/", att_dir.is_dir() and len(copied) == 3, str(copied))
        check("中文文件名保留", "题面附件.txt" in copied, str(copied))
        check("同名文件自动改名不覆盖",
              any(n.startswith("题面附件_1") for n in copied), str(copied))
        check("二进制内容逐字节一致",
              (att_dir / "bin.dat").read_bytes() == raw.read_bytes())
        check("文本内容一致", (att_dir / "题面附件.txt").read_text(encoding="utf-8") == "hello 附件")

        store = Store(config.run_dir("att-01")).load()
        ch = store.require(cid)
        desc = str(getattr(ch, "description", "") or "")
        check("题面提到 attachments/ 目录", "attachments/" in desc, desc[:200])
        check("题面列出每个附件名",
              all(n in desc for n in copied), desc[:300])
        check("题面保留原题面正文", "题面正文：看附件" in desc)
        check("题面告诉模型 cwd 就是题目工作目录", "工作目录" in desc)

        marker = json.loads((run_dir / ".offline").read_text(encoding="utf-8"))
        check("marker 记录附件名（历史/续跑可见）",
              sorted(marker.get("attachments") or []) == copied, str(marker))

        # 坏路径要立刻报错，不能悄悄跳过
        try:
            hunt._seed_run(config, urls=["http://127.0.0.1:9/"], category="misc",
                           difficulty="easy", name="bad", brief="", run_id="att-02",
                           attachments=[str(tmp / "不存在.zip")])
        except ValueError as exc:
            check("不存在的附件直接报错", "附件不存在" in str(exc), str(exc))
        else:
            check("不存在的附件直接报错", False, "没报错")

        # 超大附件拒绝（用 301MB 假大小不现实，直接改常量验证分支）
        old_max = hunt.ATTACH_MAX_BYTES
        try:
            hunt.ATTACH_MAX_BYTES = 4
            try:
                hunt._copy_attachments(config, "att-03", "HUNT-x", [str(raw)])
            except ValueError as exc:
                check("超过大小上限的附件被拒绝", "附件过大" in str(exc), str(exc))
            else:
                check("超过大小上限的附件被拒绝", False, "没报错")
        finally:
            hunt.ATTACH_MAX_BYTES = old_max

        # 接口层：run_hunt / CLI 都真的接受附件参数（防止 GUI 传了却被吞）
        params = inspect.signature(hunt.run_hunt).parameters
        check("run_hunt 接受 attachments 参数", "attachments" in params, str(list(params)))
        src = Path(hunt.__file__).read_text(encoding="utf-8")
        check("hunt CLI 有 --attach 参数", '"--attach"' in src)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    if FAILS:
        print(f"smoke_hunt_attachments 失败 {len(FAILS)} 项：" + "、".join(FAILS))
        return 1
    print("smoke_hunt_attachments 全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())