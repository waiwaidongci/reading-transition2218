#!/usr/bin/env python3
"""生成十万会话演示数据（固定随机种子）。

两阶段构造：先生成不含 序章→材料 边的背景会话，再把末尾若干条替换为
纯目标会话（路径恰为 序章>材料），使目标接续提升度略大于 1、
最优剔除数落在可完整证明的区间（默认 15–40）。输出恰好 100000 行。

运行：python3 -I gen_sessions.py
输出：sessions-100k.jsonl（每行 {"session","path"}）
"""
import json, random, pathlib

A, B = '序章', '材料'
TOTAL = 100000
rng = random.Random(202611)
chapters = ['序章', '材料', '工艺', '案例', '附录', '索引', '术语', '图表', '习题', '扩展', '小结', '参考文献']
# 背景模板：均不含 序章→材料 这条相邻边
templates = [
    ['序章', '工艺', '案例'],
    ['序章', '案例', '附录'],
    ['材料', '工艺', '案例', '附录'],
    ['工艺', '材料', '案例'],
    ['序章', '术语', '图表'],
    ['案例', '附录'],
    ['序章'],
    ['习题', '扩展', '小结'],
]
weights = [20, 16, 16, 14, 12, 10, 6, 6]

background = []
for i in range(TOTAL):
    tpl = templates[rng.choices(range(len(templates)), weights)[0]]
    path = list(tpl)
    if rng.random() < 0.15 and len(path) > 1:  # 随机扰动制造签名多样性
        path.insert(rng.randrange(len(path)), rng.choice(chapters))
    if any(x == A and y == B for x, y in zip(path, path[1:])):
        path = list(tpl)  # 扰动拼出目标边则回退，背景必须零目标边
    background.append('>'.join(path))


def counts(paths):
    o = n = t = 0
    for p in paths:
        ch = p.split('>')
        for x, y in zip(ch, ch[1:]):
            t += 1
            if x == A:
                o += 1
            if y == B:
                n += 1
    return o, n, t


def k_star(c0, o_bg, n_bg, t_bg):
    """仅删纯目标会话时的最优剔除数（对该注入方式即真实最优）。"""
    o, n, t = o_bg + c0, n_bg + c0, t_bg + c0
    for x in range(c0 + 1):
        if (c0 - x) * (t - x) <= (o - x) * (n - x):
            return x
    return None


# 用全集背景计数估计注入量：lift>1 且最优剔除数落在 [15, 40]
o_full, n_full, t_full = counts(background)
chosen = None
for c0 in range(1, TOTAL):
    lhs, rhs = c0 * (t_full + c0), (o_full + c0) * (n_full + c0)
    if lhs <= rhs:
        continue
    k = k_star(c0, o_full, n_full, t_full)
    if k is None:
        continue
    if 15 <= k <= 40:
        chosen = c0
        break
    if k > 40:
        break
assert chosen, '未找到合适的注入量'
c0 = chosen

# 末尾 c0 条背景替换为纯目标会话，最终恰好 TOTAL 行；按最终组成重算指标
final_paths = background[:TOTAL - c0] + [f'{A}>{B}'] * c0
o_bg, n_bg, t_bg = counts(final_paths[:TOTAL - c0])
c, o, n, t = c0, o_bg + c0, n_bg + c0, t_bg + c0
lift = (c * t) / (o * n)
k = k_star(c0, o_bg, n_bg, t_bg)

out = pathlib.Path(__file__).parent / 'sessions-100k.jsonl'
with out.open('w', encoding='utf-8') as f:
    for i, path in enumerate(final_paths):
        f.write(json.dumps({'session': f'SY-100K-{i + 1:06}', 'path': path}, ensure_ascii=False) + '\n')
print(f'{out} written: {len(final_paths)} 会话（背景 {TOTAL - c0} + 纯目标 {c0}）· lift≈{lift:.4f} · 预期最优剔除 {k}')
