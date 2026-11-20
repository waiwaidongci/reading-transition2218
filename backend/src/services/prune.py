"""接续剔除搜索：冻结、执行、并发保护与恢复。

口径与边界：
- 受理时把来源全部会话冻结为 JSONL（SHA256 校验），原样本永不被修改；
- 十万会话分批扫描聚合；搜索按时间片推进，每个时间片落盘已证进度；
- 约定时限到：写入已证下界与当前最佳可复算方案（status=timeout）；
- 只有证明完成才报最少（optimal）或无解（infeasible）；
- 中断后沿原输入（校验和一致才继续）与已证搜索进度恢复；
- 并发重试通过 seq 乐观锁保护：较旧的写不覆盖较新的搜索结果。
"""
import json, time, hashlib, logging
from datetime import datetime, timezone, timedelta
from redis import Redis
from sqlalchemy import select, update
from fastapi import HTTPException
from ..config.settings import REDIS_URL, ARTIFACT_DIR
from ..config.database import Session
from ..models.prune import PruneTask
from ..models.record import Record
from ..utils.serialization import row, now
from .sources import source_or_404
from .audit import log
from . import prune_solver as solver

queue = Redis.from_url(REDIS_URL)
SLICE_SEC = 1.5          # 搜索时间片：每片结束落盘一次已证进度
STALE_SEC = 90           # 心跳超过该秒数视为中断，可被他者恢复
DEFAULTS = {'time_limit_sec': 60, 'batch_size': 5000, 'min_remaining': 1}
RANGES = {'time_limit_sec': (5, 3600), 'batch_size': (100, 50000), 'min_remaining': (1, 1000)}


def validate_prune_parameters(params):
    if set(params) - set(DEFAULTS):
        raise ValueError('包含不支持的剔除搜索参数')
    out = {}
    for k, d in DEFAULTS.items():
        v = params.get(k, d)
        if isinstance(v, bool):
            raise ValueError('参数必须为整数：' + k)
        try:
            n = float(v)
        except (TypeError, ValueError):
            raise ValueError('参数必须为有限数值：' + k)
        if n != int(n):
            raise ValueError(k + '必须为整数')
        lo, hi = RANGES[k]
        if not lo <= n <= hi:
            raise ValueError(f'{k}需在{lo}至{hi}之间')
        out[k] = int(n)
    return out


def get_task(db, id):
    t = db.get(PruneTask, id)
    if not t:
        raise HTTPException(404, '未找到剔除搜索任务')
    return t


def _fresh(heartbeat):
    return bool(heartbeat) and heartbeat > (datetime.now(timezone.utc) - timedelta(seconds=STALE_SEC)).isoformat()


def _save(db, t, **fields):
    """乐观并发保存：仅当 seq 仍是读取时的值才写入；成功则推进本地 seq。
    并发重试下，较旧的写会因 seq 不匹配而失败，较新的搜索结果不被覆盖。"""
    expected = t.seq
    values = {**fields, 'seq': expected + 1, 'heartbeat': now(), 'updated_at': now()}
    res = db.execute(update(PruneTask).where(PruneTask.id == t.id, PruneTask.seq == expected).values(**values))
    db.commit()
    if res.rowcount != 1:
        return False
    t.seq = expected + 1
    for k, v in values.items():
        setattr(t, k, v)
    return True


def _freeze_and_scan(db, source_id, a, b, input_path, batch_size):
    """受理时冻结文集：分批读出全部会话写 JSONL（原样本不动），同趟完成签名聚合。"""
    h = hashlib.sha256()
    count = 0
    with open(input_path, 'w', encoding='utf-8') as f:
        def gen():
            nonlocal count
            offset = 0
            while True:
                rows = db.scalars(select(Record).where(Record.source_id == source_id)
                                  .order_by(Record.id).limit(batch_size).offset(offset)).all()
                if not rows:
                    break
                for r in rows:
                    rec = {'session': r.values['session'], 'path': r.values['path']}
                    line = json.dumps(rec, ensure_ascii=False) + '\n'
                    h.update(line.encode('utf-8'))
                    f.write(line)
                    count += 1
                    yield rec
                offset += len(rows)
        scan = solver.scan_records(gen(), a, b, batch_size=batch_size)
    return scan, h.hexdigest(), count


def create_task(db, user, data):
    s = source_or_404(db, data.source_id)
    try:
        params = validate_prune_parameters(data.parameters or {})
    except ValueError as e:
        raise HTTPException(422, str(e))
    a, b = data.target_from.strip(), data.target_to.strip()
    if not a or not b:
        raise HTTPException(422, '目标接续章节不能为空')
    t = PruneTask(source_id=s.id, name=data.name, target_from=a, target_to=b, parameters=params,
                  state='queued', progress={}, created_at=now(), updated_at=now(), heartbeat=now())
    db.add(t)
    db.flush()
    input_path = ARTIFACT_DIR / f'prune-{t.id}-input.jsonl'
    scan_path = ARTIFACT_DIR / f'prune-{t.id}-scan.json'
    try:
        scan, sha, count = _freeze_and_scan(db, s.id, a, b, input_path, params['batch_size'])
    except ValueError as e:
        input_path.unlink(missing_ok=True)
        raise HTTPException(422, str(e))
    totals = scan['totals']
    def reject(msg):
        input_path.unlink(missing_ok=True)
        raise HTTPException(422, msg)
    if count == 0:
        reject('至少录入1个完整阅读会话后才能执行')
    if totals['t'] == 0:
        reject('没有相邻章节转移；单章会话不形成接续边')
    if totals['c'] < params['min_remaining']:
        reject('该接续在冻结文集中不存在或次数不足，无法作为目标')
    if totals['c'] * totals['t'] <= totals['o'] * totals['n']:
        reject('该接续提升度未大于1，无需剔除')
    scan_path.write_text(json.dumps(scan))
    t.input_file = input_path.name
    t.input_sha256 = sha
    t.input_count = count
    t.progress = {'phase': 'search', 'scanned': count, 'lb': None, 'ub': None, 'search': None, 'nodes': 0}
    log(db, user, 'prune.queued', t.id, f'{a}->{b} {data.name}')
    db.commit()
    try:
        queue.rpush('prune:queue', t.id)
    except Exception:
        t.state = 'failed'
        t.error = '队列暂不可用，请重新创建任务'
        db.commit()
        raise HTTPException(503, t.error)
    return row(t)


def list_tasks(db):
    out = []
    for t in db.scalars(select(PruneTask).order_by(PruneTask.id.desc())):
        d = {k: v for k, v in row(t).items() if k not in ('progress', 'result')}
        p = t.progress or {}
        d['lb'] = p.get('lb')
        d['ub'] = p.get('ub')
        d['status'] = (t.result or {}).get('status') if t.result else None
        out.append(d)
    return out


def task_detail(t):
    d = row(t)
    p = dict(t.progress or {})
    search = p.get('search')
    if isinstance(search, dict) and 'dfs_state' in search:
        search = dict(search)
        search.pop('dfs_state')          # 搜索游标不进入 API 响应
        p['search'] = search
    d['progress'] = p
    return d


def resume_task(db, user, id, time_limit_sec=None):
    t = get_task(db, id)
    if t.state == 'completed' and t.result and t.result.get('status') in ('optimal', 'infeasible'):
        raise HTTPException(409, '搜索已证完（最少或无解），无需继续')
    if t.state in ('queued', 'running') and _fresh(t.heartbeat):
        raise HTTPException(409, '任务正在执行中，请稍候')
    if not t.input_file:
        raise HTTPException(409, '任务尚未冻结输入，无法继续')
    if time_limit_sec is not None:
        try:
            t.parameters = validate_prune_parameters({**t.parameters, 'time_limit_sec': time_limit_sec})
        except ValueError as e:
            raise HTTPException(422, str(e))
    t.state = 'queued'
    t.heartbeat = now()
    t.updated_at = now()
    t.seq = (t.seq or 0) + 1  # 使任何在飞执行者的下一次 CAS 失败：以本次重入队为准
    log(db, user, 'prune.resumed', t.id, '')
    db.commit()
    try:
        queue.rpush('prune:queue', t.id)
    except Exception:
        raise HTTPException(503, '队列暂不可用，请稍后重试')
    return row(t)


def _iter_jsonl(path):
    with open(path, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def _load_scan(t):
    """加载签名聚合；artifact 缺失时沿冻结输入分批重扫（校验和一致才继续）。"""
    scan_path = ARTIFACT_DIR / f'prune-{t.id}-scan.json'
    if scan_path.exists():
        return json.loads(scan_path.read_text())
    scan, sha = solver.scan_jsonl(ARTIFACT_DIR / t.input_file, t.target_from, t.target_to,
                                  batch_size=t.parameters['batch_size'], with_sha=True)
    if sha != t.input_sha256:
        raise ValueError('冻结输入校验和不匹配，拒绝在变动后的输入上继续')
    scan_path.write_text(json.dumps(scan))
    return scan


def _checkpoint(db, t, s, phase):
    progress = {'phase': phase, 'scanned': t.input_count, 'lb': s.proven_lb, 'ub': s.ub,
                'incumbent_k': s.incumbent['k'] if s.incumbent else None,
                'search': s.get_state(), 'nodes': s.nodes}
    return _save(db, t, progress=progress, state='running')


def _finalize(db, t, s, scan, started):
    a, b = t.target_from, t.target_to
    totals = scan['totals']
    before = solver.metrics_of(totals['c'], totals['o'], totals['n'], totals['t'])
    removed = solver.selection_sessions(scan, s.incumbent['selection']) if s.incumbent else []
    after = None
    if removed:
        refs = {r['session'] for r in removed}
        after = solver.recompute(_iter_jsonl(ARTIFACT_DIR / t.input_file), a, b, refs)
    status = s.done['status'] if s.done else 'timeout'
    result = {'status': status,
              'target': {'from': a, 'to': b},
              'before': before,
              'after': after,
              'removed_count': len(removed),
              'removed_sessions': removed,
              'bounds': {'lower': s.proven_lb, 'upper': s.ub},
              'proof': {'nodes': s.nodes, 'k_max': s.k_max},
              'input_sha256': t.input_sha256,
              'input_count': t.input_count,
              'min_remaining': t.parameters['min_remaining'],
              'elapsed_sec': round(time.monotonic() - started, 3),
              'explanation': '提升度=接续次数×总边数/(起点出边×终点入边)，全程整数重算；'
                             '剔除后接续仍存在（次数≥min_remaining）且提升度≤1 才算翻转，接续消失不算翻转。'}
    progress = dict(t.progress or {})
    progress.update({'phase': 'done', 'lb': s.proven_lb, 'ub': s.ub, 'search': s.get_state(), 'nodes': s.nodes})
    if not _save(db, t, result=result, state='completed', progress=progress):
        return False  # 并发：较新结果已落库，本次结果丢弃
    log(db, 'worker', 'prune.completed', t.id, status)
    db.commit()
    return True


def execute(id):
    """worker 入口：沿原输入与已证进度推进一次搜索，直到证完或到达约定时限。"""
    with Session() as db:
        t = db.get(PruneTask, id)
        if not t or t.state not in ('queued', 'running'):
            return
        if t.state == 'running' and _fresh(t.heartbeat):
            return  # 另一执行者正在推进，不并发抢跑
        if not _save(db, t, state='running'):
            return  # 占坑失败：他者已更新
        started = time.monotonic()
        deadline = started + t.parameters['time_limit_sec']
        try:
            scan = _load_scan(t)
            state = (t.progress or {}).get('search')
            s = solver.PruneSolver(scan, t.parameters['min_remaining'], state=state)
            if not _checkpoint(db, t, s, 'search'):
                return
            while not s.done:
                s.slice(time.monotonic() + SLICE_SEC)
                if not _checkpoint(db, t, s, 'search'):
                    return  # 较新进度已落库，放弃本次执行
                if time.monotonic() >= deadline:
                    break
            _finalize(db, t, s, scan, started)
        except Exception as e:
            logging.exception('prune search failed')
            db.rollback()
            # 仅当任务仍在 running 才标失败：不覆盖他者已写入的较新结果
            db.execute(update(PruneTask).where(PruneTask.id == id, PruneTask.state == 'running')
                       .values(state='failed', error=str(e)[:2000], updated_at=now()))
            log(db, 'worker', 'prune.failed', id, str(e)[:2000])
            db.commit()


_recover_last = [0.0]


def recover_stale():
    """回收中断任务：心跳过期的 queued/running 任务重新入队（worker 每轮节流调用）。"""
    if time.monotonic() - _recover_last[0] < 30:
        return
    _recover_last[0] = time.monotonic()
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=STALE_SEC)).isoformat()
    with Session() as db:
        ids = db.scalars(select(PruneTask.id).where(PruneTask.state.in_(('queued', 'running')),
                                                    PruneTask.heartbeat < cutoff)).all()
    for id in ids:
        try:
            queue.rpush('prune:queue', id)
        except Exception:
            logging.exception('prune requeue failed')
