"""赛程同步 + 结果同步。

赛程（fixtures）量小、每轮整体重建，继续用 JSON；赛果（results）量大且要跨源
合并去重，改走 SQLite（见 src/db.py），收尾导出 JSON 供 git 持久化。

并发：跨联赛并行抓取。结果同步里 win007 的 30 个日期分页也在适配器内部并行。
"""
from __future__ import annotations

from .. import config as C
from .. import db
from ..concurrency import pmap
from ..models import to_dicts
from ..storage import write_json


def _workers(cfg: dict) -> int:
    return int((cfg.get("sync") or {}).get("max_workers", 8))


def sync_fixtures(cfg: dict, client_of) -> None:
    """同步未来 N 天赛程。client_of(comp) -> 该联赛对应的数据源客户端。"""
    days = cfg["sync"]["upcoming_days"]

    def _one(comp):
        try:
            return comp, client_of(comp).fetch_upcoming(comp, days)
        except Exception as exc:      # 单个联赛失败（如站点超时）不应中断整轮同步
            print(f"[赛程] {comp.name}: 跳过（{exc}）")
            return comp, []

    pairs = pmap(_one, cfg["competitions"], max_workers=_workers(cfg))
    for comp, fixtures in pairs:
        write_json(C.FIXTURES_DIR / f"{comp.key}.json", to_dicts(fixtures))
        print(f"[赛程] {comp.name}: {len(fixtures)} 场")


def sync_results(cfg: dict, client_of, days_back: int = 10, season_days: int = 90,
                 football_7m=None, espn=None) -> None:
    """同步历史结果（用于近况/交手，联赛与杯赛分别存）。

    三个数据源按联赛分工，补全 win007 只回近 1 周的缺口：
    - win007：近 ~1 周 + 历史盘口线（主源，带 handicap_line/total_line）。win007 更早
      日期只会回空页（实测 ~1 周外无数据），所以用小窗口 days_back（默认 10）避免
      白打几百次请求触发 443 反爬；
    - 7m：五大联赛等 12 个联赛的全季赛果（无盘口线），窗口 season_days（默认 90）；
    - ESPN：欧冠/欧联/亚冠/南美/MLS/国家队的全季赛果（无盘口线，7m 覆盖不到），窗口 season_days。
    篮球（NBA）走 bdata 整季 match_list.js，用大窗口 season_days。

    去重由 SQLite 的 UNIQUE(competition, home_norm, away_norm, date) 承担；盘口线用
    COALESCE 合并——win007 完场带历史盘口线、其余源无盘口线，谁先谁后都能保住
    win007 的那条线（同时修掉了旧版把 Result 与 dict 混在一个 list 里 to_dicts 崩的 bug）。
    """
    def _one(comp):
        # 篮球走 bdata 整季，用大窗口；足球主源 win007 只回近 1 周，用小窗口省请求
        primary_days = season_days if getattr(comp, "sport", "football") == "basketball" else days_back
        results: list = []
        try:
            results = list(client_of(comp).fetch_results(comp, primary_days))
        except Exception as exc:
            # win007 主源失败（如本机被 443 限流）不影响 7m/ESPN 全季补全——它们是独立源
            print(f"[结果] {comp.name}: win007 跳过（{exc}）")

        extra_count = 0
        if football_7m is not None and getattr(comp, "sport", "football") != "basketball":
            try:
                extra = football_7m.fetch_results(comp, season_days)
                extra_count += len(extra)
                results.extend(extra)
            except Exception as exc:
                print(f"[结果·7m] {comp.name}: 跳过（{exc}）")
        if espn is not None and getattr(comp, "sport", "football") != "basketball":
            try:
                extra = espn.fetch_results(comp, season_days)
                extra_count += len(extra)
                results.extend(extra)
            except Exception as exc:
                print(f"[结果·ESPN] {comp.name}: 跳过（{exc}）")
        return comp, results, extra_count

    pairs = pmap(_one, cfg["competitions"], max_workers=_workers(cfg))
    conn = db.connect()
    try:
        for comp, results, extra_count in pairs:
            db.upsert_results(conn, results)
            print(f"[结果] {comp.name}: {len(results)} 场（含 7m {extra_count} 场）")
        conn.commit()
        db.export_results(conn, cfg["competitions"])
    finally:
        conn.close()


def sync_recent_results(cfg: dict, client_of, days_back: int = 1) -> None:
    """只同步最近 N 天（含今天）结果并合并进结果库，不覆盖历史。

    供「进行中/实时结算」工作流高频调用：只抓少量页面，把今天刚完场的
    结果补进结果库，从而让推荐在赛果一出就结算命中。去重交给 SQLite UNIQUE。
    """
    def _one(comp):
        try:
            return comp, client_of(comp).fetch_results(comp, days_back)
        except Exception as exc:
            print(f"[结果合并] {comp.name}: 跳过（{exc}）")
            return comp, []

    pairs = pmap(_one, cfg["competitions"], max_workers=_workers(cfg))
    conn = db.connect()
    try:
        for comp, recent in pairs:
            before = db.count_results(conn, comp.key)
            db.upsert_results(conn, recent)
            after = db.count_results(conn, comp.key)
            print(f"[结果合并] {comp.name}: +{after - before} 场（累计 {after}）")
        conn.commit()
        db.export_results(conn, cfg["competitions"])
    finally:
        conn.close()
