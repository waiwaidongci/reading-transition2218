"""接续剔除搜索独立复算 CLI（不依赖数据库/队列）。

用法（在 backend 目录下）：
  python -m scripts.prune_cli INPUT.jsonl 起点章 终点章 [--limit 秒] [--min-remaining N] [--batch N]
  python -m scripts.prune_cli INPUT.jsonl 起点章 终点章 --verify removal.csv

INPUT.jsonl 为冻结输入（每行 {"session","path"}，与任务受理时冻结的字节一致可核对 SHA256）；
--verify 按剔除清单 CSV（列 session,path）在剩余路径上重算接续次数与全部分母。
"""
import argparse, csv, json, sys, time
from src.services import prune_solver as ps


def main():
    p = argparse.ArgumentParser(description='接续剔除搜索复算')
    p.add_argument('input')
    p.add_argument('frm')
    p.add_argument('to')
    p.add_argument('--limit', type=int, default=60, help='约定时限（秒），到点报已证下界与最佳方案')
    p.add_argument('--min-remaining', type=int, default=1, help='剩余接续次数下限（接续消失不算翻转）')
    p.add_argument('--batch', type=int, default=5000, help='分批扫描批大小')
    p.add_argument('--verify', help='剔除清单 CSV：只复算不搜索')
    a = p.parse_args()

    if a.verify:
        with open(a.verify, encoding='utf-8-sig') as f:
            removed = {r['session'] for r in csv.DictReader(f)}
        with open(a.input, encoding='utf-8') as f:
            after = ps.recompute((json.loads(x) for x in f if x.strip()), a.frm, a.to, removed)
        print(json.dumps({'removed': len(removed), 'after': after}, ensure_ascii=False, indent=2))
        ok = after['count'] >= a.min_remaining and after['holds']
        print('复算结论：剔除后接续仍存在且提升度≤1 → 翻转成立' if ok else '复算结论：翻转不成立')
        sys.exit(0 if ok else 1)

    t0 = time.monotonic()
    scan, sha = ps.scan_jsonl(a.input, a.frm, a.to, batch_size=a.batch, with_sha=True,
                              on_batch=lambda n: print(f'  已分批扫描 {n} 会话', flush=True))
    t = scan['totals']
    print(f'冻结输入 {scan["count"]} 会话 · SHA256 {sha}')
    print(f'目标 {a.frm}→{a.to}：接续 {t["c"]} 次 · 起点出边 {t["o"]} · 终点入边 {t["n"]} · 总边 {t["t"]} '
          f'· 提升度 {(t["c"] / t["o"]) / (t["n"] / t["t"]):.4f}')
    if t['c'] * t['t'] <= t['o'] * t['n']:
        print('提升度未大于 1，无需剔除')
        return
    s = ps.PruneSolver(scan, a.min_remaining)
    deadline = time.monotonic() + a.limit
    while not s.done and time.monotonic() < deadline:
        s.slice(min(deadline, time.monotonic() + 1.5))
        print(f'  搜索中… 已证下界 {s.proven_lb} · 最佳方案 {s.ub} · 节点 {s.nodes}', flush=True)
    if s.done:
        status = '已证最少' if s.done['status'] == 'optimal' else '已证无解'
    else:
        status = '已达约定时限'
    print(f'{status} · 下界 {s.proven_lb} · 上界 {s.ub} · 耗时 {time.monotonic() - t0:.2f}s')
    if s.incumbent:
        removed = ps.selection_sessions(scan, s.incumbent['selection'])
        refs = {r['session'] for r in removed}
        with open(a.input, encoding='utf-8') as f:
            after = ps.recompute((json.loads(x) for x in f if x.strip()), a.frm, a.to, refs)
        print(f'可复算方案：剔除 {len(removed)} 个完整会话 → 接续 {after["count"]} 次 · 提升度 {after["lift"]:.4f}')
        for r in removed[:20]:
            print(f'  {r["session"]}  {r["path"]}')
        if len(removed) > 20:
            print(f'  … 共 {len(removed)} 条')


if __name__ == '__main__':
    main()
