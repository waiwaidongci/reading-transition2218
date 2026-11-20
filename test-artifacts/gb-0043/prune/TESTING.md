# 接续剔除搜索 · 求解器测试

被测代码：`backend/src/services/prune_solver.py`（纯标准库）。本目录测试不依赖
FastAPI / SQLAlchemy / Redis，可直接在本机运行。

## 运行

```bash
cd test-artifacts/gb-0043/prune
python3 -I gen_sessions.py        # 生成十万会话数据（固定种子，恰好 100000 行）
python3 -I test_prune_solver.py   # 11 个用例
```

独立复算 CLI（在 `backend/` 目录下运行）：

```bash
python -m scripts.prune_cli ../../test-artifacts/gb-0043/prune/sessions-100k.jsonl 序章 材料 --limit 30
python -m scripts.prune_cli <冻结输入.jsonl> 起点 终点 --verify <剔除清单.csv>
```

## 覆盖点

| 用例 | 断言 |
|---|---|
| test_readme_oracle_infeasible | README oracle（A-B-C 与 A-C）：lift=1.5，唯一翻转途径会消除接续 → 已证无解 |
| test_single_chapter_session_no_edge | 单章会话有效但不产生边 |
| test_minimal_removal_two | 手算最优=2 的构造例：已证最少、剔除后 lift≤1 且接续仍在 |
| test_vanish_is_not_flip | 接续消失不算翻转：只能删光目标会话才能翻转时报无解；min_remaining=2 同理 |
| test_duplicate_session_rejected | 会话编号重复拒绝（与 algorithm 口径一致） |
| test_brute_force_random | 120 个随机小数据集与穷举全部子集对拍：最优 k 与无解判定完全一致 |
| test_resume_consistency | 小实例：JSON 序列化恢复后继续求解，结果与一次性求解一致 |
| test_merge_progress | 合并规则：已证结果优先、下界取大、上界取小 |
| test_cas_older_never_overwrites_newer | 并发协议模拟：旧 seq 写入失败，已证进度不回退 |
| test_100k_batched | 十万会话分批扫描（20 批）+ 完整证明；剔除清单复算自洽 |
| test_100k_resume_with_cursor | 时间片在 DFS 中途让出（携带搜索游标）；558 片恢复求解与一次性一致 |

## 最近一次结果（2026-11-20）

```
Ran 11 tests in 20.3s — OK
brute-force 对拍 120 例（跳过 lift≤1 样本 201 个）
扫描 100000 会话 0.28s（20 批）· 搜索 3.55s · 状态 optimal · 下界 277 · 上界 277 · 节点 2269795
剔除 277 会话后：count=4920 lift=0.9999
十万会话分 558 片完成（534 次中途让出），与一次性求解一致
```

十万数据集：`gen_sessions.py` 生成 94803 条零目标边背景会话 + 5197 条纯目标会话
（路径恰为 序章>材料），目标接续 序章→材料 提升度≈1.0371，已证最少剔除 277。
