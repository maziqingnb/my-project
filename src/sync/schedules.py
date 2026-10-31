"""赛程同步 + 结果同步。"""
from __future__ import annotations

from .. import config as C
from ..models import to_dicts
from ..storage import write_json


def sync_fixtures(cfg: dict, client) -> None:
    """同步未来 N 天赛程。"""
    days = cfg["sync"]["upcoming_days"]
    for comp in cfg["competitions"]:
        fixtures = client.fetch_upcoming(comp, days)
        write_json(C.FIXTURES_DIR / f"{comp.key}.json", to_dicts(fixtures))
        print(f"[赛程] {comp.name}: {len(fixtures)} 场")


def sync_results(cfg: dict, client, days_back: int = 60) -> None:
    """同步历史结果（用于近况/交手，联赛与杯赛分别存各自文件）。"""
    for comp in cfg["competitions"]:
        results = client.fetch_results(comp, days_back)
        write_json(C.RESULTS_DIR / f"{comp.key}.json", to_dicts(results))
        print(f"[结果] {comp.name}: {len(results)} 场")
