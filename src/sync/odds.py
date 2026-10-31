"""赔率/水位监测：每次同步给每场比赛追加一条带时间戳的快照，形成水位历史。"""
from __future__ import annotations

from dataclasses import asdict

from .. import config as C
from ..storage import append_odds_snapshot


def sync_odds(cfg: dict, client) -> None:
    total = 0
    for comp in cfg["competitions"]:
        snaps = client.fetch_odds(comp)
        for s in snaps:
            append_odds_snapshot(C.ODDS_DIR, asdict(s))
        total += len(snaps)
        print(f"[赔率] {comp.name}: {len(snaps)} 条快照")
    print(f"[赔率] 合计新增 {total} 条快照")
