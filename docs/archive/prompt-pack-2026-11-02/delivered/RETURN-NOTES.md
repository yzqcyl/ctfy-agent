# 提示词外包回收 · rev / pwn（2026-11-02）

对应包：`prompt-pack-2026-11-02.zip`（任务书 08 的 T1–T5）。交付形式取 08 的优先级 1：完整替换字符串。

## 一、交付物清单（本目录）

| 文件 | 内容 |
|---|---|
| `agent.json.new` | 整份 agent.json，**只**替换了 `roles.rev.system` 与 `roles.pwn.system`，其余字段逐字未动（可直接喂给 README §四 的落地脚本）|
| `rev-system-new.txt` / `pwn-system-new.txt` | 两段正文的可读纯文本（与 JSON 内字符串逐字一致，给人眼看的）|
| `rev-diff.txt` / `pwn-diff.txt` | 逐行 unified diff（README §七 要求的人眼复核用）|

生成方式：脚本从原 `agent.json` 出发做**锚点替换**（每个锚点断言恰好命中 1 次），未触及部分逐字保留，不存在手抄引入的偏差。

## 二、T1–T5 落地明细

| 任务 | 落点 | 做法 |
|---|---|---|
| T1 假话 | rev disasm 行、pwn 静态段 | "v4 镜像含 capstone" 删除，改为条件式："未装 capstone 自动退化字节视图，不是故障，别 pip install"。见下文决策 1 |
| T2 文件偏移 | 同上两处 | "offset 传文件偏移——取 view=sections 的 offset 列，symbols 的 value / info 的 entry 是 vaddr，直接传必报\"超出文件长度\""（pwn 版措辞略异，同义）|
| T3 分工 | rev 静态段尾、pwn 静态段尾各加一行 | 单点结论 → elfpeek 单视图一步拿；全量扫描/批量验证 → write_file 落脚本一次跑完，别拆成 5 次 elfpeek；护栏："同一假设最多一次脚本，无产出即换路线" |
| T4 新视图 | rev 加 relocs + dynamic 两行；pwn 在静态段内并入 | relocs：`JUMP_SLOT 的 @地址即 foo@got、同节顺序=PLT 顺序；表空=静态链接`。dynamic：`NEEDED 看链接哪个 libc、RUNPATH 看随题 .so、BIND_NOW=full RELRO（GOT 只读，别打 GOT 劫持）` |
| T5 去冗余 | 两处【0 取料】 | 删"DNS 失败=本机无隧道"（与无条件追加的 UNAVAILABLE_RULE 语义重复），保留 `netcheck` 与 `report` 两个关键词，改为"不通立即 report 卡点" |

结构红线全部满足：分段【0】–【5】未动、步数分配未动、pwn 攻击方法论未动、四条安全底线逐字保留、无 PLAN、含 evidence/report、无新增工具承诺。

## 三、两个自主决策（README §二 留白，我按"两种结局都为真"取的条件式）

### 决策 1 · v5 措辞（对应 README §二.1）

没有采用"v5 起内置"也没有断言"本环境没有 capstone"，写的是**条件式**：

> 未装 capstone 自动退化字节视图，不是故障，别 pip install

- v5 **建**：这句话自动失活（有 capstone 就不退化），且与工具描述"v5 起镜像内置"不矛盾；
- v5 **不建**：这句话准确描述现状，并直接堵死 CYBENCH-004 那种 3 步 pip install 螺旋。

即：**无论 6 个问题里的第 1 条怎么决定，这句话都不需要回改。** 若你们已明确建 v5 且想更直白，可把该半句换成"v5 起镜像内置 capstone"（锚点：rev 的"未装 capstone 自动退化字节视图，不是故障，别 pip install"整段；pwn 同理），换完长度只减不增。

### 决策 2 · R3 的护栏取舍（09 文末留给判断的点）

采纳了保守版："**同一假设最多一次脚本，无产出即换路线**"。理由：账本只能拦截"同工具同参数"，拦不住"换个脚本重跑同一假设"，不设这句等于在错误方向上开了无限重试的口子。

同时补了一句"**小计算可直接 python3 -c**"（rev/pwn 各一处）。依据 06 §四：`python -c` 130 次 vs `write_file` 25 会话，模型本就在用一步直跑；不合法化它，模型会继续用 `open().write()` 这种被禁姿势写文件。这句把"一步直算"和"落盘脚本"划开：计算可以 `-c`，写文件必须 write_file。

## 四、验证结果

本地复刻了 `tools/check_prompt_assembly.py` 的全部检查逻辑（检查 2–7，含 6b 的 30 字符否定窗口与"nc 类"豁免；enum 取自 04 的 elfpeek schema）：

| 角色 | 长度 | 不含 PLAN | 含 evidence/report | view= 合法 | 无幻觉工具 | 拼装后三规则 |
|---|---|---|---|---|---|---|
| rev | 2223 → **2573** | ✓ | ✓ | ✓ | ✓ | ✓（拼装后 2804）|
| pwn | 2235 → **2526** | ✓ | ✓ | ✓ | ✓ | ✓（拼装后 2757）|

`agent.json.new` 已做过 JSON round-trip 自检。

两点如实说明：

1. **必须在本仓库重跑真检查**：`python tools/check_prompt_assembly.py`（8/8）与 `python tests/smoke.py`（160/0）。我的复刻没有跑第 1 项所依赖的仓库环境，enum 也是从 04 硬编码的，不是从 `tools_domain_ext` 现取的。
2. **长度口径**：2573 / 2526 都落在 08 验收表与检查脚本的 1900–2600 硬区间内；但 rev 比 07 §八"±15%"的行文口径（≈2556）超 17 字。若你们按 ±15% 从严，删掉 rev 分工行末尾"小计算可直接 python3 -c。"（15 字）即可回到 2558，其余不动。

## 五、范围外发现（未动，建议单独处理）

两段正文末尾共享的【二进制/附件先落盘】尾段让模型用 `http_request(url, save=...)` 取附件、用 `unpack_archive` 解包，但 **rev / pwn 的 local 工具面里没有这两个工具**（04：rev 只有 shell/write_file/elfpeek/hexpeek/tcp/tcp_batch/netcheck，pwn 加 pwngen/pwnfind）。rev/pwn 取附件的真实通道是 `download_challenge_attachment`。模型若照尾段调 `http_request` 会直接调用失败，白烧一步。这段尾文在 7 个角色间共享，本次任务书未授权动它——建议与 HANDOFF §B.7 的 web 角色同类问题一起统一核一遍。

## 六、诚实条款（复述 08 §末，防"全绿=更好"错觉）

本次改动是**结构性**的（假话移除、取数路径写对、分工明确、冗余合并），本地能验证的只有装配正确性。rev 池 3 道 / pwn 池 1 道离线可跑的现状下**无法 A/B**，不宣称 solve rate 提升。预期收益集中在步数效率与 token 效率（合并 5 次 elfpeek 为 1 次脚本、不再 pip install、不再 vaddr 撞墙），建议按 README §二.5 的三指标（solve rate / 平均步数 / 平均 token 每题）观测，等池子扩到 ≥8 rev / ≥5 pwn 再下结论。
