"""进行中比赛实时比分同步。

输出 data/live.json（并镜像到 docs/reports/live.json 供看板读取），
结构：{"generated_at": ISO, "matches": [{competition, home, away, kickoff,
home_goals, away_goals, status, fixture_key}]}
"""
from __future__ import annotations

from datetime import datetime, timezone

from .. import config as C
from ..models import to_dicts
from ..storage import write_json


def sync_live_scores(cfg: dict, client) -> dict:
    matches = []
    for comp in cfg["competitions"]:
        live = client.fetch_live(comp)
        for f in live:
            matches.append({
                "fixture_key": f.key,
                "competition": f.competition,
                "home": f.home,
                "away": f.away,
                "kickoff": f.kickoff,
                "status": f.status,
                "home_goals": f.home_goals,
                "away_goals": f.away_goals,
            })
        print(f"[进行中] {comp.name}: {len(live)} 场")

    doc = {"generated_at": datetime.now(timezone.utc).isoformat(), "matches": matches}
    write_json(C.DATA_DIR / "live.json", doc)
    write_json(C.DOCS_REPORTS_DIR / "live.json", doc)
    return doc
