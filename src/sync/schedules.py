"""赛程同步 + 结果同步。"""
from __future__ import annotations

from .. import config as C
from ..models import norm_team, to_dicts
from ..storage import read_json, write_json


def sync_fixtures(cfg: dict, client_of) -> None:
    """同步未来 N 天赛程。client_of(comp) -> 该联赛对应的数据源客户端。"""
    days = cfg["sync"]["upcoming_days"]
    for comp in cfg["competitions"]:
        try:
            fixtures = client_of(comp).fetch_upcoming(comp, days)
        except Exception as exc:      # 单个联赛失败（如站点超时）不应中断整轮同步
            print(f"[赛程] {comp.name}: 跳过（{exc}）")
            continue
        write_json(C.FIXTURES_DIR / f"{comp.key}.json", to_dicts(fixtures))
        print(f"[赛程] {comp.name}: {len(fixtures)} 场")


def sync_results(cfg: dict, client_of, days_back: int = 60, football_7m=None) -> None:
    """同步历史结果（用于近况/交手，联赛与杯赛分别存各自文件）。

    五大联赛额外用 7m 全季赛果补全样本：win007 赛程页只回近 ~1 周，近况样本太少，
    7m fixture.js 能回整季。合并按 (主队,客队,日期) 去重，win007 已有的保留其历史盘口线，
    7m 补的场次没有盘口线（历史盘口只在最近一周可用）。
    """
    for comp in cfg["competitions"]:
        try:
            results = client_of(comp).fetch_results(comp, days_back)
        except Exception as exc:
            print(f"[结果] {comp.name}: 跳过（{exc}）")
            continue

        # 7m 全季补全（仅足球；篮球走 bdata 篮球全季，无需此步）
        if football_7m is not None and getattr(comp, "sport", "football") != "basketball":
            try:
                extra = football_7m.fetch_results(comp, days_back)
            except Exception as exc:
                extra = []
                print(f"[结果·7m] {comp.name}: 跳过（{exc}）")
            if extra:
                seen = {(norm_team(r.get("home")), norm_team(r.get("away")),
                         (r.get("date") or "")[:10]) for r in to_dicts(results)}
                merged = list(results)
                added = 0
                for r in to_dicts(extra):
                    key = (norm_team(r["home"]), norm_team(r["away"]), r["date"][:10])
                    if key in seen:
                        continue
                    seen.add(key)
                    merged.append(r)
                    added += 1
                merged.sort(key=lambda r: r.get("date") or "", reverse=True)
                results = merged
                print(f"[结果·7m] {comp.name}: +{added} 场全季（累计 {len(merged)}）")

        write_json(C.RESULTS_DIR / f"{comp.key}.json", to_dicts(results))
        print(f"[结果] {comp.name}: {len(results)} 场")


def sync_recent_results(cfg: dict, client_of, days_back: int = 1) -> None:
    """只同步最近 N 天（含今天）结果并合并进现有结果文件，不覆盖历史。

    供「进行中/实时结算」工作流高频调用：只抓少量页面，把今天刚完场的
    结果补进结果库，从而让推荐在赛果一出就结算命中。
    """
    for comp in cfg["competitions"]:
        existing = read_json(C.RESULTS_DIR / f"{comp.key}.json", default=[]) or []
        try:
            recent = client_of(comp).fetch_results(comp, days_back)
        except Exception as exc:
            print(f"[结果合并] {comp.name}: 跳过（{exc}）")
            continue
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
