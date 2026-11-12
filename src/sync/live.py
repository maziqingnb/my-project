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


def sync_live_scores(cfg: dict, client_of, espn=None, leisu=None) -> dict:
    """leisu: 预抓一次的雷速进行中比赛（dict[comp_key -> list[Fixture]]），无头浏览器
    只跑一趟；有雷速数据的联赛优先用它（秒级），否则回退 ESPN，再回退 win007。"""
    leisu_by_comp = leisu or {}

    def _one(comp):
        live = leisu_by_comp.get(comp.key)
        if live:
            return comp, live
        # ESPN 实时比分（秒级）远快于 win007 移动版赛程页；足球优先用 ESPN，无数据/失败
        # 再回退 win007（篮球及 ESPN 未覆盖的联赛如亚冠二级/波甲仍走 win007）。
        if espn is not None and getattr(comp, "sport", "football") == "football":
            try:
                live = espn.fetch_live(comp)
                if live:
                    return comp, live
            except Exception as exc:
                print(f"[进行中·ESPN] {comp.name}: 跳过（{exc}）")
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
