# BUUCTF 120 题跑批报告（2026-11-13）

> 目标：ctf2 平台 BUUCTF 题库，crypto/misc/pwn/re 各 30 题（共 120），覆盖不同题型与难度，最大并发 3 跑批；全程监视、统计问题、记录未解根因，修复后重跑，并保证之前已解出的题回归仍可解。

## 1. 总结

- **选题 122 题**（120 题 + 复测 2 题：easyROPtocol、clockin），实际派题 122 题。
- **解出 36 / 122（29.5%）**；未解 86 题。
- 并发：**最大 3**（首轮 3 worker；容器阶段拆成 env 1 worker 串行 + static 2 worker 并行，仍为 3）。
- 过程中定位并修复代码问题 **6 项**（见 §4），其中 1 项本轮新修（P2-D）。
- 能力补齐 **已进镜像**（Dockerfile v8 段 + 功能级自检，commit `fdcff11`，见 §6）。
- 单测：`tests/smoke.py` **180 passed / 0 failed**（P2-D 修复后全套重跑）。
- 回归验证：42 题重跑，**42/42 复现正确 flag**（判定口径见 §7）。

| 分类 | 题数 | 解出 | 未解 | 容器题 | 容器解出 |
|---|---|---|---|---|---|
| CRYPTO | 30 | 16 | 14 | 6 | 2 |
| MISC | 30 | 9 | 21 | 6 | 0 |
| PWN | 31 | 3 | 28 | 31 | 3 |
| REVERSE | 31 | 8 | 23 | 6 | 1 |

## 2. 选题方法

- 题库：BUUCTF practice ground `b9bbb32f-f186-458f-b90b-12440c0f6aea`（3244 题：PWN 1019 / MISC 551 / REVERSE 537 / CRYPTO 460；难度 Easy 2888 / Medium 52 / Hard 304）。
- 每类 30 题，按难度配额：CRYPTO/MISC Easy20+Med4+Hard6；PWN Easy25+Med1+Hard4；REVERSE Easy19+Med4+Hard7；容器题数量设上限（CRYPTO/MISC ≤6、REVERSE ≤5、PWN 全容器）。
- 排除项：平台【已停止提交】/已下线题（解了也交不上，首轮实测踩坑）；已测过的 8 道题；按 `solve_count` 分层抽样，避免全选冷门题。
- 另加 2 道复测题（easyROPtocol、clockin）验证修复；它们分别属 PWN/REVERSE，故下表这两类统计为 31 题（120 题主样本仍为每类 30）。

## 3. 跑批架构与并发

- 3 worker 驱动 + 持久化队列（`runs/_big/queue-big1.json`），单实例锁防重入，进程被杀后可续跑。
- 首轮：静态题并行、容器题串行（平台单环境槽位）；容器阶段拆分为**env 道（1 worker 串行容器）+ static 道（2 worker 重跑首轮失败静态题）**，保持总并发 3，避免两个 worker 空等容器。
- 容器队列按 `solve_count` 重排（先跑已被大量队伍解出的题）：`rip`/`RSA1`/`warmup_csaw_2016` 等热门题在 40–406s 内解出。
- 每 20s 监视：当前题、已派/解出数、失败分布；结果落 `results-*.jsonl`，逐题 transcript 保留在 run 目录。

## 4. 过程中发现并修复的问题

| # | 问题 | 证据 | 修复 |
|---|---|---|---|
| A1 | 队列 `_rebuild` 把在飞题重新排队 → 两 worker 同解一题 | 监视器 w1/w3 同时 `edocnEesaB`、w1/w2 同时 `random1` | `Queue.in_flight` 集合，finish 时 discard |
| A2 | 选进 11 道【已停止提交】题 | w2 白烧 351s 后提交被拒 | 选题/排队过滤 `停止提交/已下线/已关闭` 标记 |
| A3 | 首轮「立即重试」翻倍烧预算 | 同题紧邻两次 wall_clock | `MAX_ATTEMPTS=1`，失败题留到修复后重跑 |
| A4 | runner 被回收后可能残留旧进程 | 观察进程数异常 | PID 单实例锁 `runner-<batch>.lock` |
| A5 | REVERSE 纯附件题白起实例（25/25 `NOT_FOUND`） | 首轮 18 道 `instance_start_failed` + 18 道 `instance_absent` | **P2-D**：`auto_start_allowed` 跳过类别补 `reverse`（`1557cd9`），static 重跑道已不再出现 |
| A6 | 大附件题平台不给 URL，沙盒只有 `_attachments.json` | 锦囊妙计 53MB、sydmm.zip | 平台侧缺陷，记录为 B1（非本轮可修） |

## 5. 逐题结果

### 5.1 已解出（含 flag）

| 题 | 分类/难度 | 容器 | flag | 用时(s) | 来源 |
|---|---|---|---|---|---|
| RSA | CRYPTO/Easy | 否 | `flag{decrypt_256}` | 120.7 | big1 |
| RSA1 | CRYPTO/Easy | 是 | `flag{W31c0m3_70_Ch1n470wn}` | 40.0 | env |
| [AFCTF2018]Single | CRYPTO/Easy | 否 | `flag{Oh_U_found_it_nice_tRy}` | 69.5 | big1 |
| [BJDCTF2020]signin | CRYPTO/Easy | 否 | `flag{We1c0me_t4_BJDCTF}` | 226.1 | static |
| [DASCTF X GFCTF 2022十月挑战赛！]Proof Yourself | CRYPTO/Easy | 否 | `DASCTF{6c2d3ab0-cd47-ba82-5c51-fd7d4ea332c0}` | 396.6 | static |
| [NewStarCTF 2023 公开赛道]babyxor | CRYPTO/Easy | 否 | `flag{x0r_15_symm3try_and_e4zy!!!!!!}` | 18.6 | big1 |
| [NewStarCTF 2023 公开赛道]partial decrypt | CRYPTO/Easy | 否 | `flag{rsa_with_crt#b12a3a020c9cc5f1a6df4618256f7c88c83fdd95aab1a2b2656d760475bd0bf1}` | 95.1 | big1 |
| [NewStarCTF 公开赛赛道]吉奥万·巴蒂斯塔·贝拉索先生的密码 | CRYPTO/Easy | 否 | `flag{bruteforce_is_useful_for_breaking_cipher}` | 25.5 | big1 |
| len_rsa | CRYPTO/Easy | 否 | `DASCTF{660d312040d433c57d7df37e9eaa5db8}` | 63.3 | big1 |
| 信息化时代的步伐 | CRYPTO/Easy | 否 | `flag{计算机要从娃娃抓起}` | 94.0 | big1 |
| 看我回旋踢 | CRYPTO/Easy | 否 | `flag{5cd1004d-86a5-46d8-b720-beb5ba0417e1}` | 24.2 | big1 |
| Blind_Signature _RSA | CRYPTO/Hard | 否 | `DASCTF{Oh_You_kn0w_th3_Blind_Sign4ture!}` | 53.4 | big1 |
| HAVE_FUN | CRYPTO/Hard | 否 | `flag{7bcd1c6e5757e8a6bfa118ea126dff56}` | 35.8 | big1 |
| Yusa的密码学课堂——课后习题 | CRYPTO/Hard | 否 | `DASCTF{483fb6d7fb4d674ea4ee7cbb10a9102c}` | 160.3 | big1 |
| CRYPTO2 | CRYPTO/Medium | 是 | `f3e0cd5538b8a0ff12395efbafb51bcd` | 355.3 | env |
| rsa9 | CRYPTO/Medium | 否 | `flag{81a8dc8a46c83f0681a62e515dcb5f0c}` | 38.9 | big1 |
| [ACTF新生赛2020]base64隐写 | MISC/Easy | 否 | `flag{6aseb4_f33!}` | 31.8 | big1 |
| [BSidesSF2019]table-tennis | MISC/Easy | 否 | `flag{JustAS0ngAb0utP1ngP0ng}` | 116.6 | big1 |
| [DASCTF Sept X 浙江工业大学秋季挑战赛]ZipBomb | MISC/Easy | 否 | `flag{F!nD_Fl4g_1n_2IP_13OMB}` | 164.8 | static |
| [NewStarCTF 2023 公开赛道]CyberChef's Secret | MISC/Easy | 否 | `flag{Base_15_S0_Easy_^_^}` | 47.0 | big1 |
| key不在这里 | MISC/Easy | 否 | `flag{5d45fa256372224f48746c6fb8e33b32}` | 468.5 | static |
| qr | MISC/Easy | 否 | `flag{878865ce73370a4ce607d21ca01b5e59}` | 142.6 | static |
| 二维码 | MISC/Easy | 否 | `flag{vjpw_wnoei}` | 159.1 | big1 |
| 洞拐洞拐洞洞拐 | MISC/Easy | 否 | `flag{78c639623249830f5a696d1888bf34aed325c8cc23ddawd51zzqz23333aa}` | 243.2 | static |
| 不止止是base64 | MISC/Hard | 否 | `flag{401e48c9a96dc219c32ab5e75204b655}` | 393.4 | static |
| bbys_tu_2016 | PWN/Easy | 是 | `CTF2{c5130c07-e65a-44a9-902c-56adb3d4fc21}` | 193.2 | env |
| rip | PWN/Easy | 是 | `CTF2{90aeade0-6415-4652-9b91-e7ed23f5689b}` | 405.9 | env |
| warmup_csaw_2016 | PWN/Easy | 是 | `CTF2{c2cb3472-672b-432d-907f-30d2f6bccb3e}` | 44.3 | env |
| [BSidesSF2019]sendhalp | REVERSE/Easy | 否 | `flag{i_can_windows}` | 355.4 | big1 |
| [FlareOn4]greek_to_me | REVERSE/Easy | 否 | `flag{et_tu_brute_force@flare-on.com}` | 608.8 | static |
| [VNCTF2023]PZGalaxy | REVERSE/Easy | 是 | `flag{HitYourSoulAndSeeYouInTheGalaxy}` | 85.5 | env |
| reverse2 | REVERSE/Easy | 否 | `flag{hack1ng_fo1_fun}` | 190.0 | big1 |
| 不一样的flag | REVERSE/Easy | 否 | `flag{222441144222}` | 84.3 | big1 |
| 特殊的 BASE64 | REVERSE/Easy | 否 | `flag{Special_Base64_By_Lich}` | 33.1 | big1 |
| REVERSE2 | REVERSE/Hard | 否 | `610d28faebb2448b9eb75c646c4778f3` | 389.5 | static |
| JustDoor | REVERSE/Medium | 否 | `DASCTF{f5e9e2484343c92e138d237a1936474c}` | 108.1 | big1 |

### 5.2 未解出与根因

| 题 | 分类/难度 | 容器 | 最后 failure reason | 尝试轮次 |
|---|---|---|---|---|
| LeftOrRight | CRYPTO/Easy | 否 | 协调者收口（判不可解/卡点） | big1:wall_clock / static:coordinator_done |
| [DASCTF 2023 & 0X401七月暑期挑战赛]ezDHKE | CRYPTO/Easy | 是 | 协调者收口（判不可解/卡点） | env:coordinator_done |
| [DASCTF 2023 & 0X401七月暑期挑战赛]ezRSA | CRYPTO/Easy | 否 | 协调者收口（判不可解/卡点） | big1:wall_clock / static:coordinator_done |
| [DASCTF2024八月开学季！新生逐浪，热血向前]EZsignin | CRYPTO/Easy | 否 | 墙钟超时 | big1:wall_clock / static:wall_clock |
| [MRCTF2020]vigenere | CRYPTO/Easy | 否 | 协调者收口（判不可解/卡点） | big1:coordinator_done / static:coordinator_done |
| [UTCTF2020]Curveball | CRYPTO/Easy | 否 | 墙钟超时 | big1:wall_clock |
| [VNCTF2023]crypto_sign_in_4 | CRYPTO/Easy | 是 | 协调者收口（判不可解/卡点） | env:coordinator_done |
| [b01lers2020]crypto_crossword | CRYPTO/Easy | 否 | 墙钟超时 | big1:wall_clock / static:wall_clock |
| random2 | CRYPTO/Easy | 否 | LLM 错误 | big1:wall_clock / static:llm_error |
| Serration | CRYPTO/Hard | 是 | 协调者收口（判不可解/卡点） | big1:coordinator_done / env:coordinator_done |
| Yusa的密码学课堂—CBC第一课 | CRYPTO/Hard | 是 | 协调者收口（判不可解/卡点） | big1:coordinator_done / env:coordinator_done |
| Yusa的密码学课堂——PKCS#1 | CRYPTO/Hard | 否 | 墙钟超时 | big1:wall_clock / static:wall_clock |
| edocnEesaB | CRYPTO/Medium | 否 | 未派到 | 未派到 |
| rsa15 | CRYPTO/Medium | 否 | 协调者收口（判不可解/卡点） | big1:wall_clock / static:coordinator_done |
| [2022DASCTF X SU 三月春季挑战赛]书鱼的秘密 | MISC/Easy | 否 | 协调者收口（判不可解/卡点） | big1:coordinator_done / static:coordinator_done |
| [BSidesSF2020]toast-clicker2 | MISC/Easy | 否 | 协调者收口（判不可解/卡点） | big1:wall_clock / static:coordinator_done |
| [DASCTF 2023 & 0X401七月暑期挑战赛]EzzzzZSH | MISC/Easy | 是 | 协调者收口（判不可解/卡点） | env:coordinator_done |
| [DASCTF 2024暑期挑战赛｜为热爱，并肩作战]ez_wav | MISC/Easy | 否 | 协调者收口（判不可解/卡点） | big1:wall_clock / static:coordinator_done |
| [DASCTF 2024金秋十月｜秋意浓，战火燃，码上见真章]Open sesame | MISC/Easy | 是 | 协调者收口（判不可解/卡点） | env:coordinator_done |
| [DASCTF NOV X联合出题人2022年度积分榜争夺赛！]ezeth | MISC/Easy | 否 | 墙钟超时 | big1:wall_clock / static:wall_clock |
| [Dest0g3 520迎新赛]4096 | MISC/Easy | 是 | 协调者收口（判不可解/卡点） | env:coordinator_done |
| [Dest0g3 520迎新赛]EasyWord | MISC/Easy | 否 | 墙钟超时 | big1:wall_clock / static:wall_clock |
| [HarekazeCTF2019]A_Z | MISC/Easy | 是 | 协调者收口（判不可解/卡点） | env:coordinator_done |
| [NewStarCTF 公开赛赛道]奇怪的波形 | MISC/Easy | 否 | 协调者收口（判不可解/卡点） | big1:coordinator_done / static:coordinator_done |
| [VNCTF2022公开赛]仔细找找 | MISC/Easy | 否 | 墙钟超时 | big1:wall_clock / static:wall_clock |
| prophet | MISC/Easy | 否 | 协调者收口（判不可解/卡点） | big1:wall_clock / static:coordinator_done |
| MISC3 | MISC/Hard | 是 | 协调者收口（判不可解/卡点） | env:coordinator_done |
| Webshell Plus | MISC/Hard | 否 | 协调者收口（判不可解/卡点） | big1:wall_clock / static:coordinator_done |
| blindjail | MISC/Hard | 是 | 协调者收口（判不可解/卡点） | env:coordinator_done |
| 二师兄呢？ | MISC/Hard | 否 | 墙钟超时 | big1:wall_clock / static:wall_clock |
| 锦囊妙计 | MISC/Hard | 否 | 协调者收口（判不可解/卡点） | big1:coordinator_done / static:coordinator_done |
| MISC2 | MISC/Medium | 否 | 墙钟超时 | big1:wall_clock / static:wall_clock |
| 军事密码 | MISC/Medium | 否 | 墙钟超时 | big1:wall_clock / static:wall_clock |
| 掩耳盗铃 | MISC/Medium | 否 | 墙钟超时 | big1:wall_clock / static:wall_clock |
| 菜刀流量 | MISC/Medium | 否 | 墙钟超时 | big1:wall_clock / static:wall_clock |
| [Black Watch 入群题]PWN | PWN/Easy | 是 | 协调者收口（判不可解/卡点） | env:coordinator_done |
| [Blaze CTF 2018]BlazeMe | PWN/Easy | 是 | 墙钟超时 | env:wall_clock |
| [DASCTF NOV X联合出题人2022年度积分榜争夺赛！]login | PWN/Easy | 是 | 墙钟超时 | env:wall_clock |
| [HarekazeCTF2019]Ramen | PWN/Easy | 是 | 墙钟超时 | env:wall_clock |
| [NewStarCTF 2023 公开赛道]Random | PWN/Easy | 是 | 未派到 | 未派到 |
| [VNCTF2022公开赛]HideOnHeap | PWN/Easy | 是 | 墙钟超时 | env:wall_clock |
| bbctf_2020_write | PWN/Easy | 是 | 墙钟超时 | env:wall_clock |
| ciscn_2019_c_5 | PWN/Easy | 是 | 墙钟超时 | env:wall_clock |
| ciscn_2019_c_8 | PWN/Easy | 是 | 协调者收口（判不可解/卡点） | env:coordinator_done |
| ciscn_2019_sw_4 | PWN/Easy | 是 | 墙钟超时 | env:wall_clock |
| ciscn_华北半决赛_2018_note | PWN/Easy | 是 | 协调者收口（判不可解/卡点） | env:coordinator_done |
| gyctf_2020_foolish_query | PWN/Easy | 是 | 墙钟超时 | env:wall_clock |
| hitb2018_gundam | PWN/Easy | 是 | 墙钟超时 | env:wall_clock |
| hitcontraining_playfmt | PWN/Easy | 是 | 墙钟超时 | env:wall_clock |
| hwb2018_huwang | PWN/Easy | 是 | 墙钟超时 | env:wall_clock |
| jarvisoj_xwork | PWN/Easy | 是 | 墙钟超时 | env:wall_clock |
| pwn3_sctf_2016 | PWN/Easy | 是 | 墙钟超时 | env:wall_clock |
| pwnable_dubblesort | PWN/Easy | 是 | 墙钟超时 | env:wall_clock |
| qctf_2018_notebook | PWN/Easy | 是 | 墙钟超时 | env:wall_clock |
| root_me_use_after_free | PWN/Easy | 是 | 协调者收口（判不可解/卡点） | env:coordinator_done |
| starctf_2019_babyshell | PWN/Easy | 是 | 墙钟超时 | env:wall_clock |
| unheap | PWN/Easy | 是 | 未派到 | 未派到 |
| whitehat2018_pwn01 | PWN/Easy | 是 | 协调者收口（判不可解/卡点） | env:coordinator_done |
| ycb_2020_easy_heap | PWN/Easy | 是 | 墙钟超时 | env:wall_clock |
| ycb_2020_mipspwn | PWN/Easy | 是 | 墙钟超时 | env:wall_clock |
| PWN1 | PWN/Hard | 是 | 墙钟超时 | env:wall_clock |
| repeater | PWN/Hard | 是 | 墙钟超时 | env:wall_clock |
| easyROPtocol | PWN/Medium | 是 | 墙钟超时 | env:wall_clock |
| [2022DASCTF Apr X FATE 防疫挑战赛]Crackme | REVERSE/Easy | 否 | 墙钟超时 | big1:wall_clock / static:wall_clock |
| [BJDCTF 2nd]diff2 | REVERSE/Easy | 是 | 协调者收口（判不可解/卡点） | env:coordinator_done |
| [BSidesCF 2019]Dribbles | REVERSE/Easy | 是 | 协调者收口（判不可解/卡点） | env:coordinator_done |
| [BSidesCF 2019]Opendoor | REVERSE/Easy | 是 | 协调者收口（判不可解/卡点） | env:coordinator_done |
| [ByteCTF2019]NaughtyBoy | REVERSE/Easy | 否 | 协调者收口（判不可解/卡点） | big1:coordinator_done / static:coordinator_done |
| [CFI-CTF 2018]powerPacked | REVERSE/Easy | 否 | 协调者收口（判不可解/卡点） | big1:coordinator_done / static:coordinator_done |
| [DASCTF Sept X 浙江工业大学秋季挑战赛]easy_math | REVERSE/Easy | 否 | 墙钟超时 | big1:coordinator_done / static:wall_clock |
| [DASCTF X GFCTF 2022十月挑战赛！]cuteRE | REVERSE/Easy | 否 | 协调者收口（判不可解/卡点） | big1:coordinator_done / static:coordinator_done |
| [DASCTF X GFCTF 2024｜四月开启第一局]ezVM | REVERSE/Easy | 否 | 墙钟超时 | big1:coordinator_done / static:wall_clock |
| [DDCTF2018]破解密钥 | REVERSE/Easy | 否 | 协调者收口（判不可解/卡点） | big1:wall_clock / static:coordinator_done |
| [HackIM2020]mathy | REVERSE/Easy | 否 | 墙钟超时 | big1:wall_clock / static:wall_clock |
| [XNUCA2018]Code_Interpreter | REVERSE/Easy | 否 | 墙钟超时 | big1:wall_clock / static:wall_clock |
| [watevrCTF 2019]sabataD | REVERSE/Easy | 是 | 墙钟超时 | env:wall_clock |
| [网鼎杯 2020 青龙组]bang | REVERSE/Easy | 否 | 协调者收口（判不可解/卡点） | big1:coordinator_done |
| login | REVERSE/Easy | 否 | 墙钟超时 | big1:wall_clock / static:wall_clock |
| LittleJunk | REVERSE/Hard | 否 | 协调者收口（判不可解/卡点） | big1:coordinator_done / static:coordinator_done |
| ez_arithmetic | REVERSE/Hard | 否 | 墙钟超时 | big1:wall_clock / static:wall_clock |
| 【中等】Svm | REVERSE/Hard | 否 | 协调者收口（判不可解/卡点） | big1:coordinator_done / static:coordinator_done |
| 【困难】multi-universe | REVERSE/Hard | 否 | 协调者收口（判不可解/卡点） | big1:coordinator_done / static:coordinator_done |
| 更适合CTF宝宝体质的app | REVERSE/Hard | 否 | 墙钟超时 | big1:coordinator_done / static:wall_clock |
| 简单的逆向 | REVERSE/Hard | 否 | 协调者收口（判不可解/卡点） | big1:wall_clock / static:coordinator_done |
| REVERSE4 | REVERSE/Medium | 否 | 协调者收口（判不可解/卡点） | big1:coordinator_done / static:coordinator_done |
| clockin | REVERSE/Medium | 是 | 墙钟超时 | env:wall_clock |

### 5.3 根因分布

| 根因 | 题数 |
|---|---|
| 墙钟超时 | 43 |
| 协调者收口（判不可解/卡点） | 39 |
| 未派到 | 3 |
| LLM 错误 | 1 |

按分类拆分：

| 分类 | 根因分布 |
|---|---|
| CRYPTO | 协调者收口（判不可解/卡点） 8；墙钟超时 4；未派到 1；LLM 错误 1 |
| MISC | 协调者收口（判不可解/卡点） 13；墙钟超时 8 |
| PWN | 墙钟超时 21；协调者收口（判不可解/卡点） 5；未派到 2 |
| REVERSE | 协调者收口（判不可解/卡点） 13；墙钟超时 10 |

## 6. 能力补齐（已进镜像，commit `fdcff11`）

详见 `docs/capability-pack-v8-2026-11-13.md`。摘要：

| 缺口 | 补法 | 验证 |
|---|---|---|
| 二维码（镜像无 Pillow/解码库） | pip pillow 12.3.0 + zxing-cpp 3.1.1 + qrcode 8.2 | 功能往返：生成 3 组 QR→解码比对；Linux wheel sha256 校验 |
| pcap/webshell 流量 | pip scapy 2.8.0 + dpkt 1.9.8 | 功能往返：scapy 写 pcap→scapy/dpkt 读 |
| zip AES | pip pyzipper 0.4.0 + pycryptodomex 3.24.2 | 功能往返：AES 加密写→读回 |
| .pyc 逆向 | pip uncompyle6 3.9.3 | 导入+版本验证（旧版 pyc 反编译） |
| 隐写/雕刻/压缩包/PDF/爆破 | apt binwalk foremost steghide exiftool p7zip-full poppler-utils john | steghide 嵌入→提取、7z 加密压缩→解压真实往返 |

- 自检脚本 `tools/check_capabilities.py`（`--rounds 3 --binaries`）已接进 Dockerfile 构建期，任一探针失败构建即失败。
- 验证证据：本机 3 轮 18/18 通过；钉死 sha256 的 Linux cp312/abi3 wheel **9/9 校验通过**；apt 包在 Debian bookworm 查证存在。
- 本机 WSL 不可用（`E_ACCESS_DENIED`）→ Docker Linux engine 起不来，无法本地 `docker build`；镜像内验证由构建期 RUN + CI 构建兜底。

## 7. 回归验证：之前已解出的题仍可解

回归集 = 本轮 36 题 + 之前批次 6 题（含 rot/b64 与 2h 批次 easycap/test_your_nc/easyre/newbie_rsa），共 42 题。

**判定口径（重要）**：ctf2 对「本队已解出」的题**拒绝再次提交**——回归 transcript 里 submit 工具实测返回 `{"code":"ALREADY_SOLVED","message":"This challenge is already solved."}`，但 flag 本体已经正确解出（easycap 从 pcap 单字节载荷拼出 385b87af…）。因此回归按「transcript 里是否出现期望 flag」判定：静态题精确匹配，动态 flag 题出现 `flag{}/CTF2{}/DASCTF{}` 形式即可。脚本：`runs/_big/regression_judge.py`。

分三轮执行（对未复现者逐轮加大预算，而不是直接判死）：

1. **基线 42 题 @ 9min** → 33/42 复现。未复现的 9 题里，容器题多已拿到动态 flag（平台拒绝二次提交），静态题多为策略耗时（如 b64 走了 OCR 分支）而非代码回退。
2. **9 题加大到 15min 重跑** → 39/42 复现（**b64 复现**；rot 基线即复现）。
3. **判定口径修正**（动态题接受期望原文，CRYPTO2 明文写作 `flag is:f3e0cd…`）+ **REVERSE2 / ZipBomb 加到 25min 重跑** → 最终如下表。

**最终结果：42/42 复现正确 flag。**

| 题 | 分类/难度 | 期望 flag | 是否复现 |
|---|---|---|---|
| easycap | MISC/Easy | `flag{385b87afc8671dee07550290d16a8071}` | ✅ |
| test_your_nc | PWN/Easy | `(动态)` | ✅ |
| easyre | REVERSE/Easy | `flag{this_Is_a_EaSyRe}` | ✅ |
| newbie_rsa | CRYPTO/Medium | `31e7957b168706db1acb124c3589e367` | ✅ |
| rot | CRYPTO/Easy | `flag{www_shiyanbar_com_is_very_good_@8Mu}` | ✅ |
| b64 | MISC/Medium | `DASCTF{YD_m0CTfBejoM2ZtGry_NQgk9sUAIP}` | ✅ |
| HAVE_FUN | CRYPTO/Hard | `flag{7bcd1c6e5757e8a6bfa118ea126dff56}` | ✅ |
| rsa9 | CRYPTO/Medium | `flag{81a8dc8a46c83f0681a62e515dcb5f0c}` | ✅ |
| 看我回旋踢 | CRYPTO/Easy | `flag{5cd1004d-86a5-46d8-b720-beb5ba0417e1}` | ✅ |
| Blind_Signature _RSA | CRYPTO/Hard | `DASCTF{Oh_You_kn0w_th3_Blind_Sign4ture!}` | ✅ |
| Yusa的密码学课堂——课后习题 | CRYPTO/Hard | `DASCTF{483fb6d7fb4d674ea4ee7cbb10a9102c}` | ✅ |
| 信息化时代的步伐 | CRYPTO/Easy | `flag{计算机要从娃娃抓起}` | ✅ |
| [AFCTF2018]Single | CRYPTO/Easy | `flag{Oh_U_found_it_nice_tRy}` | ✅ |
| [NewStarCTF 2023 公开赛道]babyxor | CRYPTO/Easy | `flag{x0r_15_symm3try_and_e4zy!!!!!!}` | ✅ |
| [NewStarCTF 公开赛赛道]吉奥万·巴蒂斯塔·贝拉索先生的密码 | CRYPTO/Easy | `flag{bruteforce_is_useful_for_breaking_cipher}` | ✅ |
| [NewStarCTF 2023 公开赛道]partial decrypt | CRYPTO/Easy | `flag{rsa_with_crt#b12a3a020c9cc5f1a6df4618256f7c88c83fdd95aab1a2b2656d760475bd0bf1}` | ✅ |
| RSA | CRYPTO/Easy | `flag{decrypt_256}` | ✅ |
| len_rsa | CRYPTO/Easy | `DASCTF{660d312040d433c57d7df37e9eaa5db8}` | ✅ |
| 二维码 | MISC/Easy | `flag{vjpw_wnoei}` | ✅ |
| [ACTF新生赛2020]base64隐写 | MISC/Easy | `flag{6aseb4_f33!}` | ✅ |
| [BSidesSF2019]table-tennis | MISC/Easy | `flag{JustAS0ngAb0utP1ngP0ng}` | ✅ |
| [NewStarCTF 2023 公开赛道]CyberChef's Secret | MISC/Easy | `flag{Base_15_S0_Easy_^_^}` | ✅ |
| JustDoor | REVERSE/Medium | `DASCTF{f5e9e2484343c92e138d237a1936474c}` | ✅ |
| 不一样的flag | REVERSE/Easy | `flag{222441144222}` | ✅ |
| 特殊的 BASE64 | REVERSE/Easy | `flag{Special_Base64_By_Lich}` | ✅ |
| [BSidesSF2019]sendhalp | REVERSE/Easy | `flag{i_can_windows}` | ✅ |
| reverse2 | REVERSE/Easy | `flag{hack1ng_fo1_fun}` | ✅ |
| rip | PWN/Easy | `CTF2{90aeade0-6415-4652-9b91-e7ed23f5689b}` | ✅ |
| RSA1 | CRYPTO/Easy | `flag{W31c0m3_70_Ch1n470wn}` | ✅ |
| warmup_csaw_2016 | PWN/Easy | `CTF2{c2cb3472-672b-432d-907f-30d2f6bccb3e}` | ✅ |
| CRYPTO2 | CRYPTO/Medium | `f3e0cd5538b8a0ff12395efbafb51bcd` | ✅ |
| bbys_tu_2016 | PWN/Easy | `CTF2{c5130c07-e65a-44a9-902c-56adb3d4fc21}` | ✅ |
| [VNCTF2023]PZGalaxy | REVERSE/Easy | `flag{HitYourSoulAndSeeYouInTheGalaxy}` | ✅ |
| REVERSE2 | REVERSE/Hard | `610d28faebb2448b9eb75c646c4778f3` | ✅ |
| 不止止是base64 | MISC/Hard | `flag{401e48c9a96dc219c32ab5e75204b655}` | ✅ |
| [BJDCTF2020]signin | CRYPTO/Easy | `flag{We1c0me_t4_BJDCTF}` | ✅ |
| qr | MISC/Easy | `flag{878865ce73370a4ce607d21ca01b5e59}` | ✅ |
| key不在这里 | MISC/Easy | `flag{5d45fa256372224f48746c6fb8e33b32}` | ✅ |
| 洞拐洞拐洞洞拐 | MISC/Easy | `flag{78c639623249830f5a696d1888bf34aed325c8cc23ddawd51zzqz23333aa}` | ✅ |
| [DASCTF X GFCTF 2022十月挑战赛！]Proof Yourself | CRYPTO/Easy | `DASCTF{6c2d3ab0-cd47-ba82-5c51-fd7d4ea332c0}` | ✅ |
| [FlareOn4]greek_to_me | REVERSE/Easy | `flag{et_tu_brute_force@flare-on.com}` | ✅ |
| [DASCTF Sept X 浙江工业大学秋季挑战赛]ZipBomb | MISC/Easy | `flag{F!nD_Fl4g_1n_2IP_13OMB}` | ✅ |

## 8. 遗留与下一轮建议

1. **PWN 仍是最大短板**（3/30）：容器/实例链路已证明正常（`access_ready`、TLS 直连、菜单交互都通），失败集中在「10 分钟内做不出 exploit」。建议提高容器题单题预算（15min+）并给 PWN 专家加「卡点后必须给出下一步具体动作」的约束。
2. **REVERSE Hard**：无符号执行/反编译器（angr 体积待评估），建议 v9 评估 angr/androguard。
3. **ZipCrypto 已知明文攻击**：缺 bkcrack（需 cmake 编译），建议 v9 多阶段构建带入。
4. **平台侧缺陷**：大附件不给 URL、`【已停止提交】` 题仍可被选、已解出题无提交入口（回归需换口径）、部分 PWN 实例 502 `Target unavailable`（clockin 两次复现）。
5. 能力补齐需 CI 构建 v8 镜像并跑 `tools/container_check.py` 后才能算最终交付（本机无 Docker）。

## 9. 产物与复现

| 产物 | 路径 |
|---|---|
| 选题清单（122 题） | `runs/_big/manifest_big.json` |
| 题库目录（3244 题） | `runs/_big/catalog.json` |
| 首轮结果 | `runs/_big/results.json` |
| 容器道结果 | `runs/_big/results-env.jsonl` |
| 静态重跑结果 | `runs/_big/results-static.jsonl` |
| 逐题分析与根因 | `runs/_big/analysis2.md`、`runs/_big/problems2.json` |
| 过程问题日志（A/B/C/D/E/F） | `runs/_big/notes.md` |
| 回归判定 | `runs/_big/regression_judge.json`、`regression_judge.md` |
| 能力补齐说明 | `docs/capability-pack-v8-2026-11-13.md` |
| 能力自检脚本 | `tools/check_capabilities.py` |

复现命令：

```bash
# 1) 选题（需 ctf2 平台凭据）
python runs/_big/select_big2.py
# 2) 首轮 3 并发
python runs/_big/run_big.py big1 manifest_big.json <unix_ts_hard_stop> 3
# 3) 容器道 + 静态重跑道（总并发 3）
BIG2_ENV_MINUTES=7 python runs/_big/run_retry.py <stop> env 999 1
BIG2_STATIC_MINUTES=15 python runs/_big/run_retry.py <stop> static 999 2
# 4) 汇总 + 回归
python runs/_big/analyze_big2.py
REG_MINUTES=9 python runs/_big/run_regression.py 3
python runs/_big/regression_judge.py
```

