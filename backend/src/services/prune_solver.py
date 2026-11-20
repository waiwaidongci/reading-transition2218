"""接续剔除搜索求解器（纯标准库，可独立运行与测试）。

业务问题：对一条提升度 > 1 的接续 a→b，剔除最少的完整会话，使按剩余会话
重算后提升度 ≤ 1；接续消失（剩余次数 < min_remaining）不算翻转。

口径与 services.algorithm 完全一致：仅同一会话内相邻章节构成接续边；
  提升度 = (count/outgoing) / (incoming/total) = count·total / (outgoing·incoming)。
全部判定使用整数运算：提升度 ≤ 1 ⟺ count·total ≤ outgoing·incoming，无浮点误差。

求解结构：
  scan_records      分批扫描冻结文集，按签名 (c,o,n,e) 聚合会话；
  proven_lower_bound 松弛下界（已证：少于该数不可能翻转）；
  greedy_ub         多策略贪心，给出可行剔除方案（上界）；
  PruneSolver       k 递增 + 深度优先判定，第一个可行 k 即已证最少；
                    搜索状态可序列化，中断后沿原输入与已证进度恢复。
"""
import json, time, hashlib


def signature_of(chapters, a, b):
    """单会话对目标接续 (a,b) 的签名：c=目标边数, o=a出边数, n=b入边数, e=总边数。"""
    c = o = n = 0
    for x, y in zip(chapters, chapters[1:]):
        if x == a:
            o += 1
        if y == b:
            n += 1
        if x == a and y == b:
            c += 1
    return c, o, n, len(chapters) - 1


def scan_records(records, a, b, on_batch=None, batch_size=5000):
    """分批扫描会话，返回签名聚合（可 JSON 序列化）。records 为 {'session','path'} 迭代器。"""
    totals = [0, 0, 0, 0]
    sigs, order, seen = {}, [], set()
    count = 0
    for r in records:
        sid = r['session']
        if sid in seen:
            raise ValueError('会话编号重复，请将一个完整会话保存在同一行：' + str(sid))
        seen.add(sid)
        chapters = [x.strip() for x in r['path'].split('>')]
        c, o, n, e = signature_of(chapters, a, b)
        totals[0] += c
        totals[1] += o
        totals[2] += n
        totals[3] += e
        key = (c, o, n, e)
        s = sigs.get(key)
        if s is None:
            s = sigs[key] = {'c': c, 'o': o, 'n': n, 'e': e, 'sessions': []}
            order.append(key)
        s['sessions'].append({'session': sid, 'path': r['path']})
        count += 1
        if on_batch and count % batch_size == 0:
            on_batch(count)
    if on_batch and count % batch_size:
        on_batch(count)
    # 效率序：对 slack 的一阶贡献 c·T + e·C − o·N − n·O 降序，确定性排序
    C, O, N, T = totals
    order.sort(key=lambda k: (-(k[0] * T + k[3] * C - k[1] * N - k[2] * O), -k[0], -k[3], k))
    return {'a': a, 'b': b, 'count': count,
            'totals': {'c': totals[0], 'o': totals[1], 'n': totals[2], 't': totals[3]},
            'signatures': [sigs[k] for k in order]}


def scan_jsonl(path, a, b, on_batch=None, batch_size=5000, with_sha=False):
    """从冻结输入 JSONL 重新分批扫描；with_sha 时同时校验文件校验和。"""
    h = hashlib.sha256() if with_sha else None

    def gen():
        with open(path, 'rb') as f:
            for line in f:
                if h is not None:
                    h.update(line)
                line = line.strip()
                if line:
                    yield json.loads(line)

    scan = scan_records(gen(), a, b, on_batch, batch_size)
    return scan, (h.hexdigest() if h is not None else None)


def metrics_of(c, o, n, t):
    """四个分母与派生指标；lift 仅用于展示，判定一律用整数式 c·t ≤ o·n。"""
    return {'count': c, 'outgoing': o, 'incoming': n, 'total': t,
            'conditional_probability': (c / o) if o else None,
            'target_base_rate': (n / t) if t else None,
            'lift': ((c / o) / (n / t)) if (o and n and t) else None,
            'holds': c * t <= o * n}


def recompute(records, a, b, removed):
    """按剔除清单 removed（会话编号集合）在剩余路径上重算接续次数与全部分母。"""
    C = O = N = T = 0
    for r in records:
        if r['session'] in removed:
            continue
        chapters = [x.strip() for x in r['path'].split('>')]
        c, o, n, e = signature_of(chapters, a, b)
        C += c
        O += o
        N += n
        T += e
    return metrics_of(C, O, N, T)


def _k_max(count, sigs, mr):
    """最多可剔除会话数：至少保留 g 个会话使剩余目标边 ≥ mr；超过则接续必然消失。"""
    need, g = mr, 0
    for s in sorted(sigs, key=lambda s: -s['c']):
        if need <= 0:
            break
        if s['c'] <= 0:
            continue
        # 该签名每个会话贡献 s['c'] 条目标边
        use = min(len(s['sessions']), -(-need // s['c']))
        need -= use * s['c']
        g += use
    if need > 0:
        return -1  # 全集也不足 mr，接续存在性都无法保证
    return count - g


def proven_lower_bound(sigs, totals, mr, k_max):
    """已证下界：若剔除 ≤ k 个会话时 (C−topk_c)·(T−topk_e) 仍大于可达的 O'·N' 上界，
    则 k 次剔除不可能翻转。返回最小可能可行的 k；若 > k_max 则已证无解。"""
    C, O, N, T = totals
    cs, es, os_, ns = [], [], [], []
    for s in sigs:
        m = len(s['sessions'])
        cs += [s['c']] * m
        es += [s['e']] * m
        os_ += [s['o']] * m
        ns += [s['n']] * m
    cs.sort(reverse=True)
    es.sort(reverse=True)
    os_.sort()
    ns.sort()

    def prefix(values):
        out, acc = [0], 0
        for v in values:
            acc += v
            out.append(acc)
        return out

    pc, pe, po, pn = prefix(cs), prefix(es), prefix(os_), prefix(ns)
    for k in range(0, k_max + 1):
        cmin = max(C - pc[k], mr)          # 剩余目标边至少这么多（且须 ≥ mr）
        tmin = max(T - pe[k], cmin)        # 剩余总边至少这么多（且 ≥ 剩余目标边）
        omax = O - po[k]                   # 剩余 a 出边至多这么多
        nmax = N - pn[k]                   # 剩余 b 入边至多这么多
        if omax <= 0 or nmax <= 0:
            continue                       # 该 k 下右端必为 0，不可能可行
        if cmin * tmin <= omax * nmax:
            return k
    return k_max + 1


def greedy_ub(sigs, totals, mr):
    """多策略贪心：返回 (上界k, incumbent) 或 (None, None)。incumbent={'selection':{签名序号:数量}}。"""
    C, O, N, T = totals
    S = len(sigs)
    mults = [len(s['sessions']) for s in sigs]
    best = None
    for strat in ('slack', 'c', 'e'):
        avail = mults[:]
        dc = do_ = dn = de = 0
        used, sel = 0, {}
        while True:
            C1, O1, N1, T1 = C - dc, O - do_, N - dn, T - de
            if C1 >= mr and O1 * N1 >= C1 * T1:
                if best is None or used < best[0]:
                    best = (used, {'selection': dict(sel), 'k': used})
                break
            bi, bkey = -1, None
            for i in range(S):
                if not avail[i]:
                    continue
                s = sigs[i]
                if C1 - s['c'] < mr:
                    continue
                if strat == 'slack':
                    key = ((O1 - s['o']) * (N1 - s['n']) - (C1 - s['c']) * (T1 - s['e']), s['c'], s['e'])
                elif strat == 'c':
                    key = (s['c'], s['e'])
                else:
                    key = (s['e'], s['c'])
                if bkey is None or key > bkey:
                    bkey, bi = key, i
            if bi < 0:
                break
            s = sigs[bi]
            avail[bi] -= 1
            sel[bi] = sel.get(bi, 0) + 1
            dc += s['c']
            do_ += s['o']
            dn += s['n']
            de += s['e']
            used += 1
    if best:
        return best[0], best[1]
    return None, None


def _top_sum(cnt, extra_v, extra_m, r):
    """计数数组 cnt（下标=值）与额外 (extra_v×extra_m) 中，最大的 r 个值之和。"""
    s, need, v = 0, r, len(cnt) - 1
    while v >= 0 and need > 0:
        avail = cnt[v] + (extra_m if v == extra_v else 0)
        if avail:
            take = avail if avail < need else need
            s += v * take
            need -= take
        v -= 1
    return s


def _bot_sum(cnt, extra_v, extra_m, r):
    """计数数组 cnt 与额外 (extra_v×extra_m) 中，最小的 r 个值之和。"""
    s, need, v = 0, r, 0
    while v < len(cnt) and need > 0:
        avail = cnt[v] + (extra_m if v == extra_v else 0)
        if avail:
            take = avail if avail < need else need
            s += v * take
            need -= take
        v += 1
    return s


def selection_sessions(scan, selection):
    """把 {签名序号: 数量} 展开为确定性的剔除会话清单（按扫描顺序取前 q 个）。"""
    out = []
    for i, q in sorted(selection.items()):
        out.extend(scan['signatures'][i]['sessions'][:q])
    return out


def merge_progress(old, new):
    """并发保存冲突时的合并规则：已证结果优先；下界取大、上界取小；搜索进度取 k 较深者。
    保证较新的（已证更多的）搜索结果不被较旧的覆盖。"""
    if not old:
        return new
    if not new:
        return old
    if old.get('done') and not new.get('done'):
        return old
    if new.get('done') and not old.get('done'):
        return new
    m = dict(old)
    m['k'] = max(old.get('k', 0), new.get('k', 0))
    ou, nu = old.get('ub'), new.get('ub')
    if ou is None or (nu is not None and nu <= ou):
        m['ub'] = nu
        m['incumbent'] = new.get('incumbent')
    else:
        m['ub'] = ou
        m['incumbent'] = old.get('incumbent')
    m['dfs_state'] = new.get('dfs_state') if new.get('k', 0) > old.get('k', 0) else old.get('dfs_state')
    m['nodes'] = max(old.get('nodes', 0), new.get('nodes', 0))
    return m


class PruneSolver:
    """k 递增的精确搜索：每个 k 用带剪枝 DFS 判定可行；第一个可行 k 即已证最少。
    状态可经 get_state()/传入 state= 恢复，支持时间分片与中断恢复。"""

    def __init__(self, scan, min_remaining=1, state=None):
        self.scan = scan
        self.mr = min_remaining
        t = scan['totals']
        self.C, self.O, self.N, self.T = t['c'], t['o'], t['n'], t['t']
        self.sigs = scan['signatures']
        self.count = scan['count']
        self.k_max = _k_max(self.count, self.sigs, self.mr)
        self.lb_relax = proven_lower_bound(self.sigs, (self.C, self.O, self.N, self.T), self.mr, max(self.k_max, 0))
        self._suf = None
        if state:
            self.k = state.get('k', self.lb_relax)
            self.dfs_state = state.get('dfs_state')
            self.ub = state.get('ub')
            incumbent = state.get('incumbent')
            if incumbent and incumbent.get('selection'):
                # JSON 恢复后签名字典键是字符串，统一转回整数下标
                incumbent = {**incumbent, 'selection': {int(i): q for i, q in incumbent['selection'].items()}}
            self.incumbent = incumbent
            self.nodes = state.get('nodes', 0)
            self.done = state.get('done')
        else:
            self.k = self.lb_relax
            self.dfs_state = None
            self.nodes = 0
            self.ub, self.incumbent = greedy_ub(self.sigs, (self.C, self.O, self.N, self.T), self.mr)
            self.done = None
            if self.k_max < 0 or self.lb_relax > self.k_max:
                self.done = {'status': 'infeasible'}
            elif self.ub is not None and self.k >= self.ub:
                self.done = {'status': 'optimal', 'k': self.ub}

    @property
    def proven_lb(self):
        """已证下界：剔除数 ≤ k−1 不可能翻转（松弛证明与已穷举的 k 共同保证）。"""
        return self.k

    def get_state(self):
        return {'k': self.k, 'dfs_state': self.dfs_state, 'ub': self.ub,
                'incumbent': self.incumbent, 'nodes': self.nodes, 'done': self.done}

    def slice(self, deadline):
        """在 deadline（time.monotonic）前推进搜索；返回 True 表示已证完（最少或无解）。"""
        if self.done:
            return True
        while time.monotonic() < deadline:
            if self.ub is not None and self.k >= self.ub:
                self.done = {'status': 'optimal', 'k': self.ub}
                return True
            if self.k > self.k_max:
                self.done = {'status': 'infeasible'}
                return True
            res, self.dfs_state = self._dfs_slice(self.k, self.dfs_state, deadline)
            if res is None:
                return False
            if res is False:
                self.k += 1
                self.dfs_state = None
            else:
                self.ub = self.k
                self.incumbent = {'selection': res, 'k': self.k}
                self.done = {'status': 'optimal', 'k': self.k}
                return True
        return False

    def _build_suffix(self):
        """每个签名层之后（含当前层调整）剩余池的 c/o/n/e 计数数组，用于 O(值域) 剪枝。"""
        S = len(self.sigs)
        maxv = 1
        for s in self.sigs:
            maxv = max(maxv, s['c'], s['o'], s['n'], s['e'])
        maxv += 1
        suf = [[[0] * maxv for _ in range(4)] for _ in range(S + 1)]
        for idx in range(S - 1, -1, -1):
            s = self.sigs[idx]
            m = len(s['sessions'])
            row, nxt = suf[idx], suf[idx + 1]
            for j, key in enumerate(('c', 'o', 'n', 'e')):
                row[j][:] = nxt[j][:]
                row[j][s[key]] += m
        self._suf = suf

    def _bound_ok(self, idx_next, sig, rem_mult, dc, do_, dn, de, r):
        """松弛剪枝：剩余预算 r 内即使最优地再删，c'·t' 仍可能 ≤ o'·n' 才返回 True。"""
        C1, O1, N1, T1 = self.C - dc, self.O - do_, self.N - dn, self.T - de
        suf = self._suf[idx_next]
        rc = _top_sum(suf[0], sig['c'], rem_mult, r)
        re_ = _top_sum(suf[3], sig['e'], rem_mult, r)
        ro = _bot_sum(suf[1], sig['o'], rem_mult, r)
        rn = _bot_sum(suf[2], sig['n'], rem_mult, r)
        cmin = max(C1 - rc, self.mr)
        tmin = max(T1 - re_, cmin)
        omax = O1 - ro
        nmax = N1 - rn
        if omax <= 0 or nmax <= 0:
            return False
        return cmin * tmin <= omax * nmax

    def _dfs_slice(self, k, state, deadline):
        """判定"≤ k 次剔除是否可行"。返回 (selection|False|None, 新state)；None 表示时间片用完。"""
        S = len(self.sigs)
        if self._suf is None:
            self._build_suffix()
        if state:
            stack = [list(f) for f in state['stack']]
        else:
            if S == 0:
                return False, None
            stack = [[0, min(len(self.sigs[0]['sessions']), k), 0, 0, 0, 0, 0, 0]]
        C, O, N, T, mr = self.C, self.O, self.N, self.T, self.mr
        # 栈帧：[签名层, 待试数量q(递减), 当前q, 已减c, 已减o, 已减n, 已减e, 已用名额]
        while stack:
            self.nodes += 1
            if self.nodes % 2048 == 0 and time.monotonic() >= deadline:
                return None, {'k': k, 'stack': stack}
            f = stack[-1]
            if f[1] < 0:
                stack.pop()
                continue
            q = f[1]
            f[1] -= 1
            f[2] = q
            s = self.sigs[f[0]]
            mult = len(s['sessions'])
            if q > mult:
                continue
            used = f[7] + q
            if used > k:
                continue
            dc = f[3] + q * s['c']
            do_ = f[4] + q * s['o']
            dn = f[5] + q * s['n']
            de = f[6] + q * s['e']
            C1 = C - dc
            if C1 < mr:  # 接续消失不算翻转
                continue
            O1, N1, T1 = O - do_, N - dn, T - de
            if O1 * N1 >= C1 * T1:
                sel = {}
                for fr in stack:
                    if fr[2] > 0:
                        sel[fr[0]] = fr[2]
                return sel, None
            r = k - used
            if r <= 0 or f[0] + 1 >= S:
                continue
            if not self._bound_ok(f[0] + 1, s, mult - q, dc, do_, dn, de, r):
                continue
            ns = self.sigs[f[0] + 1]
            stack.append([f[0] + 1, min(len(ns['sessions']), r), 0, dc, do_, dn, de, used])
        return False, None
