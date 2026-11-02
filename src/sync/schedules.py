"""赛程同步 + 结果同步。"""
from __future__ import annotations

from .. import config as C
from ..models import norm_team, to_dicts
from ..storage import read_json, write_json


def sync_fixtures(cfg: dict, client_of) -> None:
    """同步未来 N 天赛程。client_of(comp) -> 该联赛对应的数据源客户端。"""
    days = cfg["sync"]["upcoming_days"]
    for comp in cfg["competitions"]:
        fixtures = client_of(comp).fetch_upcoming(comp, days)
        write_json(C.FIXTURES_DIR / f"{comp.key}.json", to_dicts(fixtures))
        print(f"[赛程] {comp.name}: {len(fixtures)} 场")


def sync_results(cfg: dict, client_of, days_back: int = 60) -> None:
    """同步历史结果（用于近况/交手，联赛与杯赛分别存各自文件）。"""
    for comp in cfg["competitions"]:
        results = client_of(comp).fetch_results(comp, days_back)
        write_json(C.RESULTS_DIR / f"{comp.key}.json", to_dicts(results))
        print(f"[结果] {comp.name}: {len(results)} 场")


def sync_recent_results(cfg: dict, client_of, days_back: int = 1) -> None:
    """只同步最近 N 天（含今天）结果并合并进现有结果文件，不覆盖历史。

    供「进行中/实时结算」工作流高频调用：只抓少量页面，把今天刚完场的
    结果补进结果库，从而让推荐在赛果一出就结算命中。
    """
    for comp in cfg["competitions"]:
        existing = read_json(C.RESULTS_DIR / f"{comp.key}.json", default=[]) or []
        recent = client_of(comp).fetch_results(comp, days_back)
        seen = {(norm_team(r.get("home")), norm_team(r.get("away")), (r.get("date") or "")[:10])
                for r in existing}
        merged = list(existing)
        added = 0
        for r in to_dicts(recent):
            key = (norm_team(r["home"]), norm_team(r["away"]), r["date"][:10])
            if key in seen:
                continue
            seen.add(key)
            merged.append(r)
            added += 1
        merged.sort(key=lambda r: r.get("date") or "", reverse=True)
        write_json(C.RESULTS_DIR / f"{comp.key}.json", merged)
        print(f"[结果合并] {comp.name}: +{added} 场（累计 {len(merged)}）")
