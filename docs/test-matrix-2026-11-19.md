# 测试矩阵复盘（2026-11-19）：BUUCTF 24 题 · 20/24 解出

> 用户指定测试集：hard web×4；medium/hard 的 pwn/rev/crypto/misc 各 2；easy pwn/rev 各 2。
> 平台 ctf2 / BUUCTF；VPS（39.107.43.191）v8 镜像 + wireproxy；**easy 优先**，顺序执行（并发 1）。
> 总耗时 **203.8 分钟**，24 份完整思考转录（`runs/test-matrix2-2026-11-19/`）。

## 1. 结果

| 难度 | 解出 | 说明 |
|---|---|---|
| easy | **4/4** | pwn 2/2（jarvisoj_level0、ret2text）、rev 2/2（新年快乐、reverse3） |
| medium | **6/8** | pwn 1/2、rev 2/2、crypto 2/2、misc 1/2 |
| hard | **10/12** | pwn 2/2、rev 2/2、crypto 2/2、misc 0/2、web 4/4 |

按类别：crypto 4/4、rev 6/6、web 4/4、pwn 5/6、**misc 1/4（短板）**。
token 合计 **7,483,772**（prompt 5,823,127 / completion 1,660,645），LLM 调用 1,416 次。

### 未解 4 题（全部烧满 800k 后 stalled）

| 题 | 类别 | 卡点 |
|---|---|---|
| easyROPtocol | medium pwn | 协议/状态机类：79 次 shell、11 次 pwnlocal，未构造出正确报文序列 |
| 菜刀流量 | medium misc | 流量分析：81 次 shell、33 次超时，缺 pcap 解析工具/套路 |
| Webshell Plus | hard misc | 395 条记录、TLS/连接层失败 22 次，Webshell 流量+接入卡点 |
| BlueTrace | hard misc | 取证/编码：控制台编码坑 23 次、超时 20 次 |

## 2. 关键正向证据

- **本地优先规则生效**：easy pwn 两题都是 `pwnlocal` 本地验证（offset/backdoor/gadget）→ 再打远端；
  全批 `pwnlocal` 调用 49 次、`pwnrop` 14 次、`elfpeek` 46 次。
- **工具层稳定**：24 题工具软失败 **0** 次；主要损耗是 shell 非零 41 次、超时 156 次。
- 解出题平均 token 约 25 万；未解 4 题全部撞 800k 上限 → misc 类要的是**正确套路**不是更多 token。

## 3. 本轮已修（提交 `3649a66`，已同步到 VPS repo16）

1. **misc 打法卡**（`method_cards.py`）：
   - 恢复 misc category 兜底卡：流量包用 scapy/dpkt（不赌 tshark 已装）、Webshell 流量、取证、编码链；
   - 新增 5 张 domain 卡：pcap/traffic、webshell、forensics、stego、protocol；
   - pwn 兜底卡补「协议/菜单题」段（本地 pwntools tube 跑通全流程再打远端）。
2. **复盘器环境误报**（`analyze_batch.py`）：环境坑判定改为按宿主 OS——Linux 容器里
   `python3`/`/bin/sh`/`tee` 是正常命令，原先按 Windows 硬编码误报 700+ 条，淹没真实问题。
3. 测试同步：`smoke.py`（misc 无 tag 走 category 兜底）、`smoke_domain_cards`（卡数 9→14）。

## 4. 仍待改进（赛后）

| 项 | 说明 |
|---|---|
| misc 专项工具链 | 流量/取证/编码类缺专用工具（tshark 不一定在镜像里；可考虑加 pyshark/scapy 套路、binwalk 已有） |
| 协议题 | easyROPtocol 类需要「先逆协议再写利用」的固定流程卡（已加卡，效果待验证） |
| 实例配额 | ctf2 实例配额 1：并发 2 会抢实例互相踩（RANGE KEEPER）→ 已改并发 1；CTFY 比赛是 3 |
| 预算分配 | misc 未解题全烧 800k 仍 0 → 可对 misc 提前触发换策略/收口 |
| 复盘器 | `analysis.md` 的环境坑分类已修，但仍以「出现次数」计，不看是否真失败（后续可加失败判定） |

## 5. 下一场（比赛）

同一套 VPS/镜像已用于 2026-11-19 10:00 TPCTF VPS 测试赛（自动跑，见
`docs/competition-tpctf-2026-11-19.md`）；本轮修好的 misc 卡与协议卡会随 repo 直接生效。