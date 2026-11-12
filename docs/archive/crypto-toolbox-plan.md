# crypto 方向工具箱 v1 · 落地记录（R1）

> 会话：toolbox（rev/pwn/crypto 专用，与并行会话零文件冲突）
> 日期：2026-10-30 · 状态：**R1 已完成并通过全部回归**；**零镜像依赖**（全纯 stdlib，不增加镜像体积）。
> 姊妹文档：`docs/archive/rev-toolbox-plan.md`（rev v2）、`docs/archive/pwn-toolbox-plan.md`（pwn v1）。

## 1. 为什么做（实测依据）

| 事实 | 来源 |
|---|---|
| crypto 历史 **47 试 10 解（21%）** | `runs/**/state.json` 全量扫描 |
| 镜像无 pycryptodome/gmpy2/z3/sympy；模型每道 AES/大数题都要现场手写原语，步数烧光 | 工具面盘点 + 失败转录 |
| 提示词已列全攻击清单（Wiener/Håstad/Fermat/p-1/MT19937…），但**没有对应工具**，模型只能写一次性脚本 | config 旧 crypto 提示词 |
| oracle 类题（padding oracle / 爆破）需要块构造原语，此前只能靠 shell 手搓 | 失败转录 |

## 2. 交付物与标识

| 文件 | 改动 | 标识 |
|---|---|---|
| `src/ctfy_agent/tools_domain_ext.py` | ① **rsatool**（9 个 op：small_e / common_modulus / wiener / fermat / pollard_pm1 / pollard_rho / hastad / crt / factor）；② **aesblock**（纯 stdlib AES-128/192/256：ecb/cbc/ctr、xor、CBC bitflip、pkcs7）；③ **prng**（MT19937 标准实现 + untemper 状态恢复、LCG 参数恢复与预测）；④ 辅助 `_int_of/_bytes_of/_iroot/_egcd/_modinv/_crt/_primes_upto` | `>>> [crypto-toolbox 2026-10-30]` / `# [crypto-toolbox]` |
| `tests/smoke_crypto.py`（新） | 21 用例：FIPS-197 AES-128 KAT、NIST SP800-38A CBC/CTR 向量、PKCS7、xor/bitflip、9 个 RSA 攻击（含构造因子）、MT19937 标准向量 + 状态恢复、LCG 恢复、自动注册契约、派发 | 文件头 `[crypto-toolbox]` |
| `config/agent.json` | crypto 块 4 处（见 §3） | JSON 无注释，以 §3 为准 |
| `docs/archive/crypto-toolbox-plan.md` | 本文件 | — |

**注册机制**：与 rev/pwn 相同——`DOMAIN_SPECS` → `tools_misc_ext.MISC_EXT_SPECS` → `LocalTools.specs()`，`call()` 按 `_DOMAIN_NAMES` 动态路由。**未改 crew.py / solver.py / tools_local.py**。

## 3. config/agent.json 的 crypto 块精确 diff（4 处）

```jsonc
// crypto.max_steps: 12 → 14
// crypto.local: ["shell","tcp","tcp_batch","netcheck"] → 追加 "hexpeek","rsatool","aesblock","prng"
// crypto.system 开头: 工具清单加 rsatool / aesblock / prng / hexpeek
// crypto.system 步数行: "只有 12 步：1 取料/1 判型/3 分析/4 攻击/1 验证/1 收口" → "只有 14 步：1/1/3/5/2/1"
// crypto.system 【2.工具面】: 重写为"重活已封装：优先用工具不要手写" + 三工具 op 清单
// crypto.system 【3.攻击清单】: 每条攻击后括注对应工具 op（RSA→rsatool、AES→aesblock、PRNG→prng）
```

## 4. 验收记录（2026-10-30）

| 检查 | 结果 |
|---|---|
| `python tests/smoke_crypto.py` | **21/21 PASS** |
| AES 已知答案 | FIPS-197 `69c4e0d8…b4c55a` ✓；NIST CBC `7649abac…` ✓；NIST CTR `874d6191…` ✓ |
| MT19937 已知答案 | seed=5489 前 5 输出 `3499211612, 581869302, 3890346734, 3586334585, 545404204` ✓；624 输出恢复后预测逐位一致 ✓ |
| RSA | small_e / common_modulus / wiener(d=5) / fermat / p-1(65537·65539,B=400) / rho / hastad(e=3) / crt 全命中 ✓ |
| `python tests/smoke.py` 全量回归 | **144 passed / 0 failed**（rev 15/15、pwn 16/16 同轮全绿） |
| config 接线 | `json.load` OK；三个角色的 `local` 名单全部能解析到真实工具（无 unknown） |

## 5. 未做 / 待批（R2+）

1. **哈希长度扩展**（MD5/SHA1/SHA256 状态注入）——纯 stdlib 可做（约 +150 行），R2 候选。
2. 格攻击 / Coppersmith（需要 lattice 归约）：明确**不引入 z3/fpylll**（体积 +60MB 级），继续用"已知弱点优先"策略。
3. ECC 辅助（曲线参数校验/小阶点）：优先级中，视正赛题型再定。
4. 真机验收：从 47 道历史 crypto 里挑 2–3 道 stalled 题复跑（1.2M/14 步），需模型预算单独申请。