"""赔率/水位监测：每次同步给每场比赛追加一条带时间戳的快照，形成水位历史。

并发：跨联赛并行抓取（线程池），单联赛内部（win007 分析页）仍有自己的线程池，
整体峰值由 config.sync.max_workers 约束。抓到后单线程写入 SQLite 并导出 JSON。
"""
from __future__ import annotations

from .. import config as C
from .. import db
from ..concurrency import pmap


def _workers(cfg: dict) -> int:
    return int((cfg.get("sync") or {}).get("max_workers", 8))


def sync_odds(cfg: dict, client_of) -> None:
    def _one(comp):
        try:
            return comp, client_of(comp).fetch_odds(comp)
        except Exception as exc:  # 单个联赛失败（如缺 THE_ODDS_API_KEY）不应中断整轮同步
            print(f"[赔率] {comp.name}: 跳过（{exc}）")
            return comp, []

    pairs = pmap(_one, cfg["competitions"], max_workers=_workers(cfg))
    conn = db.connect()
    try:
        total = 0
        for comp, snaps in pairs:
            n = db.append_odds(conn, snaps)
            total += n
            print(f"[赔率] {comp.name}: {n} 条快照")
        conn.commit()
        db.export_odds(conn)
    finally:
        conn.close()
    print(f"[赔率] 合计新增 {total} 条快照")
