"""阵容抓取：临近开赛时抓首发。"""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta, timezone

from .. import config as C
from ..models import Fixture
from ..storage import read_json, write_json


def _parse_iso(s: str) -> datetime:
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def sync_lineups(cfg: dict, client) -> None:
    window = cfg["sync"]["lineup_window_hours"]
    now = datetime.now(timezone.utc)
    count = 0
    for comp in cfg["competitions"]:
        fixtures = read_json(C.FIXTURES_DIR / f"{comp.key}.json", default=[]) or []
        for raw in fixtures:
            fx = Fixture(**raw)
            try:
                ko = _parse_iso(fx.kickoff)
            except (ValueError, TypeError):
                continue
            if now - timedelta(hours=window) <= ko <= now + timedelta(hours=window):
                lineup = client.fetch_lineup(comp, fx)
                if lineup:
                    safe = fx.key.replace("|", "_").replace("/", "_")
                    write_json(C.LINEUPS_DIR / f"{safe}.json", asdict(lineup))
                    count += 1
    print(f"[阵容] 抓取 {count} 场首发")
