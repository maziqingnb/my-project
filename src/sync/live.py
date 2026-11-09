"""进行中比赛实时比分同步。

输出 data/live.json（并镜像到 docs/reports/live.json 供看板读取），
结构：{"generated_at": ISO, "matches": [{competition, home, away, kickoff,
home_goals, away_goals, status, fixture_key}]}

量小、每轮整体重建，继续用 JSON；仅把跨联赛抓取并行化。
"""
from __future__ import annotations

from datetime import datetime, timezone

from .. import config as C
from ..concurrency import pmap
from ..storage import write_json


def _workers(cfg: dict) -> int:
    return int((cfg.get("sync") or {}).get("max_workers", 8))


def sync_live_scores(cfg: dict, client_of) -> dict:
    def _one(comp):
        try:
            return comp, client_of(comp).fetch_live(comp)
        except Exception as exc:      # 单个联赛失败（如站点超时）不应中断整轮
            print(f"[进行中] {comp.name}: 跳过（{exc}）")
            return comp, []

    matches = []
    for comp, live in pmap(_one, cfg["competitions"], max_workers=_workers(cfg)):
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
