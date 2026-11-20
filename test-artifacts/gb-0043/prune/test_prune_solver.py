#!/usr/bin/env python3
"""prune_solver 独立 oracle 测试（纯标准库）。

运行：python3 -I test_prune_solver.py
被测代码路径通过 --src 传入（默认 /workspace/backend）。
"""
import sys, os, json, time, random, itertools, unittest, argparse, tempfile, pathlib

parser = argparse.ArgumentParser()
parser.add_argument('--src', default='/workspace/backend')
args, _ = parser.parse_known_args()
sys.path.insert(0, args.src)

from src.services import prune_solver as ps


def solve(records, a, b, mr=1, seconds=30.0):
    scan = ps.scan_records(records, a, b)
    s = ps.PruneSolver(scan, mr)
    deadline = time.monotonic() + seconds
    while not s.slice(deadline):
        if time.monotonic() >= deadline:
            raise AssertionError(f'求解超时：{a}->{b} k={s.k} ub={s.ub}')
    return s


def brute_force(records, a, b, mr=1):
    """穷举所有剔除子集，返回最优 k（无可行子集返回 None）。仅用于小规模对拍。"""
    n = len(records)
    best = None
    for mask in range(1 << n):
        removed = {records[i]['session'] for i in range(n) if mask >> i & 1}
        m = ps.recompute(records, a, b, removed)
        if m['count'] >= mr and m['holds']:
            k = bin(mask).count('1')
            if best is None or k < best:
                best = k
    return best


class OracleTest(unittest.TestCase):
    def test_readme_oracle_infeasible(self):
        """README oracle：会话 A-B-C 与 A-C。目标 A→B：C=1,O=2,N=1,T=3，lift=1.5。
        删 s2 后 lift=(1/1)/(1/2)=2 更糟；删 s1 接续消失。已证无解。"""
        records = [{'session': 's1', 'path': 'A>B>C'}, {'session': 's2', 'path': 'A>C'}]
        scan = ps.scan_records(records, 'A', 'B')
        self.assertEqual((scan['totals']['c'], scan['totals']['o'], scan['totals']['n'], scan['totals']['t']), (1, 2, 1, 3))
        s = solve(records, 'A', 'B')
        self.assertEqual(s.done['status'], 'infeasible')

    def test_single_chapter_session_no_edge(self):
        """单章会话有效但不产生边（README 已知边界）。"""
        records = [{'session': 's1', 'path': 'A'}, {'session': 's2', 'path': 'A>B'}]
        scan = ps.scan_records(records, 'A', 'B')
        self.assertEqual(scan['totals']['t'], 1)
        self.assertEqual(scan['totals']['c'], 1)

    def test_minimal_removal_two(self):
        """C=3,O=3,N=3,T=5，lift=5/3。删 2 个中性会话 C>D 后 T=3，lift=1。已证最少=2。"""
        records = [{'session': 's1', 'path': 'A>B'}, {'session': 's2', 'path': 'A>B'},
                   {'session': 's3', 'path': 'A>B'}, {'session': 's4', 'path': 'C>D'},
                   {'session': 's5', 'path': 'C>D'}]
        s = solve(records, 'A', 'B')
        self.assertEqual(s.done, {'status': 'optimal', 'k': 2})
        removed = {r['session'] for r in ps.selection_sessions(s.scan, s.incumbent['selection'])}
        after = ps.recompute(records, 'A', 'B', removed)
        self.assertTrue(after['holds'])
        self.assertGreaterEqual(after['count'], 1)
        self.assertEqual(len(removed), 2)

    def test_vanish_is_not_flip(self):
        """接续消失不算翻转：唯一翻转途径是删光目标会话时必须报无解。"""
        records = [{'session': 's1', 'path': 'A>B'}, {'session': 's2', 'path': 'B>A>B'}]
        # C=2,O=2,N=2,T=3，lift=(2/2)/(2/3)=1.5；删 s2 后 lift=1（k=1 可行）
        s = solve(records, 'A', 'B')
        self.assertEqual(s.done, {'status': 'optimal', 'k': 1})
        removed = {r['session'] for r in ps.selection_sessions(s.scan, s.incumbent['selection'])}
        self.assertEqual(removed, {'s2'})
        # 只剩目标会话：删光才≤1 → 无解
        records2 = [{'session': 's1', 'path': 'A>B'}, {'session': 's2', 'path': 'A>B'}]
        scan = ps.scan_records(records2, 'A', 'B')
        self.assertEqual((scan['totals']['c'], scan['totals']['t']), (2, 2))
        # lift=(2/2)/(2/2)=1 不大于 1，构造 lift>1：加一条只含 A 的出边？A>C 使 O 增 → lift 降。
        # 用 B>A>B 变体：C=2,O=2,N=2,T=3 lift=1.5，mr=2 时删 s2 后 C=1<2 → 无解
        s2 = solve(records, 'A', 'B', mr=2)
        self.assertEqual(s2.done['status'], 'infeasible')

    def test_duplicate_session_rejected(self):
        records = [{'session': 's1', 'path': 'A>B'}, {'session': 's1', 'path': 'A>C'}]
        with self.assertRaises(ValueError):
            ps.scan_records(records, 'A', 'B')

    def test_brute_force_random(self):
        """随机小数据集与穷举对拍：最优 k 与无解判定必须一致。"""
        rng = random.Random(20261120)
        checked = skipped = 0
        while checked < 120:
            n = rng.randint(4, 9)
            records = []
            for i in range(n):
                length = rng.randint(1, 4)
                path = '>'.join(rng.choice('ABC') for _ in range(length))
                records.append({'session': f's{i}', 'path': path})
            a, b = rng.choice('ABC'), rng.choice('ABC')
            scan = ps.scan_records(records, a, b)
            t = scan['totals']
            if t['c'] == 0 or t['c'] * t['t'] <= t['o'] * t['n']:
                skipped += 1
                continue  # 只需要 lift>1 的样本
            s = solve(records, a, b, seconds=10.0)
            expect = brute_force(records, a, b)
            if expect is None:
                self.assertEqual(s.done['status'], 'infeasible', msg=f'{records} {a}->{b}')
            else:
                self.assertEqual(s.done['status'], 'optimal', msg=f'{records} {a}->{b}')
                self.assertEqual(s.done['k'], expect, msg=f'{records} {a}->{b}')
                removed = {r['session'] for r in ps.selection_sessions(s.scan, s.incumbent['selection'])}
                after = ps.recompute(records, a, b, removed)
                self.assertTrue(after['holds'] and after['count'] >= 1)
                self.assertEqual(len(removed), expect)
            checked += 1
        print(f'  brute-force 对拍 {checked} 例（跳过 lift≤1 样本 {skipped} 个）')

    def test_resume_consistency(self):
        """中断恢复（小实例）：序列化恢复后继续求解，结果与一次性求解一致。"""
        rng = random.Random(7)
        records = None
        for attempt in range(200):  # 找一个需要真实搜索的实例（lb<ub 且构造时未证完）
            cand = []
            for i in range(200):
                length = rng.randint(2, 6)
                cand.append({'session': f's{i}', 'path': '>'.join(rng.choice('ABCDE') for _ in range(length))})
            scan0 = ps.scan_records(cand, 'A', 'B')
            t = scan0['totals']
            if t['c'] == 0 or t['c'] * t['t'] <= t['o'] * t['n']:
                continue
            probe = ps.PruneSolver(scan0, 1)
            if not probe.done and probe.ub is not None and probe.lb_relax < probe.ub:
                records = cand
                break
        self.assertIsNotNone(records, '未生成到需要真实搜索的实例')
        a, b = 'A', 'B'
        scan = ps.scan_records(records, a, b)
        once = ps.PruneSolver(scan, 1)
        once.slice(time.monotonic() + 30)
        self.assertIsNotNone(once.done)
        scan2 = ps.scan_records(records, a, b)
        s = ps.PruneSolver(scan2, 1)
        guard = 0
        while not s.done and guard < 100000:
            s.slice(time.monotonic() + 0.002)
            state = json.loads(json.dumps(s.get_state()))
            s = ps.PruneSolver(scan2, 1, state=state)
            guard += 1
        self.assertEqual(s.done, once.done)
        self.assertEqual(s.ub, once.ub)

    def test_merge_progress(self):
        old = {'k': 3, 'ub': 7, 'incumbent': {'k': 7}, 'nodes': 100}
        new = {'k': 2, 'ub': 5, 'incumbent': {'k': 5}, 'nodes': 40}
        m = ps.merge_progress(old, new)
        self.assertEqual(m['k'], 3)                 # 下界取大（已证更多）
        self.assertEqual(m['ub'], 5)                # 上界取小（方案更好）
        self.assertEqual(m['incumbent'], {'k': 5})
        done = {'k': 4, 'done': {'status': 'optimal', 'k': 4}}
        self.assertEqual(ps.merge_progress(done, new), done)   # 已证结果不被覆盖
        self.assertEqual(ps.merge_progress(new, done), done)

    def test_cas_older_never_overwrites_newer(self):
        """并发协议模拟：旧 seq 的写必须失败；合并后已证进度不回退。"""
        store = {'seq': 0, 'progress': None}

        def cas_save(expected, progress):
            if store['seq'] != expected:
                return False
            store['seq'] += 1
            store['progress'] = progress
            return True

        self.assertTrue(cas_save(0, {'k': 5, 'ub': 9}))      # worker A 推进到 k=5
        self.assertFalse(cas_save(0, {'k': 2, 'ub': 12}))    # worker B 持旧 seq 写更旧进度
        self.assertEqual(store['progress']['k'], 5)          # 较新结果未被覆盖
        merged = ps.merge_progress(store['progress'], {'k': 2, 'ub': 12})
        self.assertEqual((merged['k'], merged['ub']), (5, 9))


class ScaleTest(unittest.TestCase):
    def test_100k_resume_with_cursor(self):
        """十万会话：时间片必须在 DFS 中途让出（携带搜索游标），且全程恢复后结果一致。"""
        path = pathlib.Path(__file__).parent / 'sessions-100k.jsonl'
        if not path.exists():
            self.skipTest('先用 gen_sessions.py 生成十万会话数据')
        scan, _ = ps.scan_jsonl(path, '序章', '材料')
        once = ps.PruneSolver(scan, 1)
        once.slice(time.monotonic() + 60)
        self.assertIsNotNone(once.done)
        s = ps.PruneSolver(scan, 1)
        slices = mid = 0
        while not s.done and slices < 20000:
            s.slice(time.monotonic() + 0.005)
            state = json.loads(json.dumps(s.get_state()))
            if state.get('dfs_state'):
                mid += 1
            s = ps.PruneSolver(scan, 1, state=state)
            slices += 1
        self.assertEqual(s.done, once.done)
        self.assertEqual(s.ub, once.ub)
        self.assertGreater(mid, 0, '时间片应在 DFS 中途让出并携带搜索游标')
        print(f'  十万会话分 {slices} 片完成（{mid} 次中途让出），与一次性求解一致')

    def test_100k_batched(self):
        """十万会话分批扫描 + 全链路求解；断言自洽并输出耗时。"""
        path = pathlib.Path(__file__).parent / 'sessions-100k.jsonl'
        if not path.exists():
            self.skipTest('先用 gen_sessions.py 生成十万会话数据')
        t0 = time.monotonic()
        batches = []
        scan, sha = ps.scan_jsonl(path, '序章', '材料', on_batch=lambda n: batches.append(n), batch_size=5000, with_sha=True)
        t1 = time.monotonic()
        self.assertEqual(scan['count'], 100000)
        self.assertGreater(len(batches), 1)  # 确实分批
        s = ps.PruneSolver(scan, 1)
        deadline = time.monotonic() + 60
        while not s.slice(deadline):
            if time.monotonic() >= deadline:
                break
        t2 = time.monotonic()
        print(f'  扫描 100000 会话 {t1 - t0:.2f}s（{len(batches)} 批）· 搜索 {t2 - t1:.2f}s · '
              f'状态 {s.done["status"] if s.done else "timeout"} · 下界 {s.proven_lb} · 上界 {s.ub} · 节点 {s.nodes}')
        self.assertIsNotNone(s.done, '该数据集应在一分钟内证完')
        if s.done['status'] == 'optimal':
            removed = ps.selection_sessions(scan, s.incumbent['selection'])
            refs = {r['session'] for r in removed}
            self.assertEqual(len(refs), s.done['k'])
            with open(path) as f:
                after = ps.recompute((json.loads(x) for x in f if x.strip()), '序章', '材料', refs)
            self.assertTrue(after['holds'])
            self.assertGreaterEqual(after['count'], 1)
            print(f'  剔除 {len(refs)} 会话后：count={after["count"]} lift={after["lift"]:.4f}')


if __name__ == '__main__':
    unittest.main(verbosity=2)
