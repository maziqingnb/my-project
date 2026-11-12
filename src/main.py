"""命令行入口。

用法：
  python src/main.py sync --all            # 同步赛程/结果/赔率/阵容（需 API key）
  python src/main.py sync --odds           # 只同步赔率水位
  python src/main.py analyze               # 基于已有数据出推荐
  python src/main.py analyze --demo        # 演示模式：用内置样例数据跑通全流程
  python src/main.py live                  # 同步进行中比分 + 今日结果并实时结算命中
  python src/main.py review --notify       # 生成/推送每日未命中复盘
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Windows 控制台默认 GBK，中文打印会乱码；能用 UTF-8 就用 UTF-8
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from src import config as C
from src.adapters.mock import MockFixtures, MockOdds
from src.adapters.the_odds_api import TheOddsApi
from src.adapters.crown_scraper import CrownScraper
from src.adapters.win007 import Win007Client
from src.adapters.api_football import ApiFootball
from src.models import OddsSnapshot, Result
from src.storage import read_json


def _league_map(cfg: dict) -> dict:
    return {str(c.win007_id): c.key for c in cfg["competitions"] if c.win007_id}


def _win007_client(cfg: dict, scrapers: dict, name_map: dict):
    sources = cfg.get("sources") or {}
    prof = scrapers.get("win007") or {}
    return Win007Client(
        base_url=sources.get("win007_url", ""),
        analysis_url=prof.get("analysis_url", ""),
        league_map=_league_map(cfg),
        team_name_map=name_map,
        handicap_sign=prof.get("handicap_sign", -1),
        days=prof.get("days", 1),
        results_days=prof.get("results_days", 30),
    )


def make_clients(cfg: dict, demo: bool):
    """返回 {sport: {"odds": OddsClient, "fixtures": FixturesClient}}。

    足球走 sources.odds / sources.fixtures（默认 win007 免 key）；
    篮球（NBA）让分/总分走 the-odds-api，赛程/结果走球探网篮球（免 key）。
    """
    if demo:
        return {"football": {"odds": MockOdds(), "fixtures": MockFixtures()}}

    scrapers = cfg.get("scrapers") or {}
    name_map = cfg.get("team_name_map") or {}
    sources = cfg.get("sources") or {}
    odds_src = sources.get("odds", "the-odds-api")
    fx_src = sources.get("fixtures", "api-football")

    win = _win007_client(cfg, scrapers, name_map) if (odds_src == "win007" or fx_src == "win007") else None

    if odds_src == "win007":
        odds = win
    elif odds_src == "crown":
        url = sources.get("crown_url", "") or os.getenv("CROWN_ODDS_URL", "")
        odds = CrownScraper(url, name_map, columns=(scrapers.get("crown") or {}).get("columns"))
    else:
        odds = TheOddsApi(cfg["api_keys"]["the_odds_api"], cfg["odds"]["bookmakers"], cfg["odds"]["markets"])

    fixtures = win if fx_src == "win007" else ApiFootball(cfg["api_keys"]["api_football"])

    # 篮球（NBA）：赔率用 the-odds-api 的 spreads/totals；结果用球探网篮球 match_list.js
    from src.adapters.basketball_7m import Basketball7mClient
    bball_odds = TheOddsApi(cfg["api_keys"]["the_odds_api"],
                            cfg["odds"]["bookmakers"], ["spreads", "totals"])
    bball_fixtures = Basketball7mClient(sources.get("basketball_url", ""), name_map)

    return {
        "football": {"odds": odds, "fixtures": fixtures},
        "basketball": {"odds": bball_odds, "fixtures": bball_fixtures},
    }


def _client_of(clients: dict, kind: str):
    """返回一个 comp -> client 的解析器（按 comp.sport 路由到对应数据源）。"""
    def pick(comp):
        bucket = clients.get(getattr(comp, "sport", "football"), clients["football"])
        return bucket[kind]
    return pick


def _enter_demo_dirs() -> None:
    """demo 模式把数据目录重定向到独立临时目录，避免抹掉/污染真实 data/。

    每次 --demo 用全新临时目录：结果确定、不累积重复快照，退出时自动清理，
    完全不会触碰真实 data/ 与 docs/reports/。
    """
    import atexit
    import shutil
    import tempfile
    root = Path(tempfile.mkdtemp(prefix="football-odds-demo-"))
    C.set_data_root(root)
    atexit.register(shutil.rmtree, root, ignore_errors=True)


def do_sync(cfg: dict, clients: dict, what: str, demo: bool = False) -> None:
    from src.sync import schedules, odds as odds_sync, lineups as lineups_sync
    from src.output.render import build_schedule
    from src.adapters.football_7m import Football7mClient
    from src.adapters.espn import EspnClient

    fixtures_of = _client_of(clients, "fixtures")
    odds_of = _client_of(clients, "odds")
    # 7m 全季补全 + ESPN 全季补全只在真实同步时启用；demo 模式走 mock，保持离线、确定
    football_7m = None if demo else Football7mClient()
    espn = None if demo else EspnClient()
    if what in ("fixtures", "all"):
        schedules.sync_fixtures(cfg, fixtures_of)
        build_schedule(cfg)      # 未开赛赛程提前展示（不含推荐）
        schedules.sync_results(cfg, fixtures_of, football_7m=football_7m, espn=espn)
    if what in ("odds", "all"):
        odds_sync.sync_odds(cfg, odds_of)
    if what in ("lineups", "all"):
        lineups_sync.sync_lineups(cfg, fixtures_of)


def load_results(cfg: dict) -> dict[str, list[Result]]:
    from src import db
    conn = db.connect()
    try:
        return {comp.key: db.load_results(conn, comp.key) for comp in cfg["competitions"]}
    finally:
        conn.close()


def load_odds_history() -> dict[str, list[OddsSnapshot]]:
    """读取所有水位历史，按联赛 key 分组。"""
    from src import db
    by_comp: dict[str, list[OddsSnapshot]] = {}
    conn = db.connect()
    try:
        for snap in db.load_odds(conn):
            by_comp.setdefault(snap.competition, []).append(snap)
        return by_comp
    finally:
        conn.close()


def do_analyze(cfg: dict, demo: bool = False) -> dict:
    from src.analysis.recommender import recommend_competition
    from src.output.render import render_report

    results_by_comp = load_results(cfg)
    odds_by_comp = load_odds_history()

    all_recs = []
    for comp in cfg["competitions"]:
        snaps = odds_by_comp.get(comp.key, [])
        by_fixture: dict[str, list[OddsSnapshot]] = {}
        for s in snaps:
            by_fixture.setdefault(s.fixture_key, []).append(s)

        recs = recommend_competition(comp, cfg, results_by_comp.get(comp.key, []), by_fixture)
        all_recs.extend(recs)
        cls_cn = "杯赛" if comp.cls == "cup" else "联赛"
        print(f"[分析] {comp.name}（{cls_cn}口径）: {len(recs)} 条推荐")

    report = render_report(cfg, all_recs, results_by_comp, demo=demo)
    if not demo:
        from src.output.review import build_daily_review
        build_daily_review()
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="足球水位分析系统")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("sync")
    g = sp.add_mutually_exclusive_group()
    g.add_argument("--all", dest="what", action="store_const", const="all")
    g.add_argument("--odds", dest="what", action="store_const", const="odds")
    g.add_argument("--fixtures", dest="what", action="store_const", const="fixtures")
    g.add_argument("--lineups", dest="what", action="store_const", const="lineups")
    sp.set_defaults(what="all")
    sp.add_argument("--demo", action="store_true")

    ap = sub.add_parser("analyze")
    ap.add_argument("--demo", action="store_true")

    lp = sub.add_parser("live", help="同步进行中比分 + 今日结果并重新结算（实时命中）")

    rp = sub.add_parser("review", help="生成/推送每日未命中复盘")
    rp.add_argument("--notify", action="store_true", help="同时推送到微信")

    np = sub.add_parser("notify", help="把最新报告推送到微信（企业微信机器人/Server酱）")
    np.add_argument("--prematch-hours", type=float, default=None,
                    help="只推 N 小时内开赛的比赛（赛前提醒，每场只推一次）")

    lg = sub.add_parser("leagues", help="发现球探网联赛 ID（给 config.yaml 植入新联赛用）")
    lg.add_argument("keyword", nargs="?", default=None, help="按联赛名关键字过滤，如「欧联」「亚冠」「NBA」")
    lg.add_argument("--date", default=None, help="抓取日期（默认今天；杯赛要在其比赛日查）")

    args = parser.parse_args()
    cfg = C.load_config()

    if args.cmd == "sync":
        clients = make_clients(cfg, args.demo)
        do_sync(cfg, clients, args.what, demo=args.demo)
    elif args.cmd == "analyze":
        if args.demo:
            _enter_demo_dirs()               # 先隔离数据目录，再灌样例数据
            clients = make_clients(cfg, True)
            do_sync(cfg, clients, "all", demo=True)
        report = do_analyze(cfg, demo=args.demo)
        if args.demo:
            print(f"\n完成（演示数据，未写真实 data/ 与 docs/reports/）：共 {report['total']} 条推荐")
        else:
            print(f"\n完成：共 {report['total']} 条推荐，见 data/reports/report.md")
    elif args.cmd == "live":
        clients = make_clients(cfg, False)
        from src.sync.live import sync_live_scores
        from src.sync import schedules
        from src.adapters.espn import EspnClient
        fixtures_of = _client_of(clients, "fixtures")
        espn = EspnClient()
        sync_live_scores(cfg, fixtures_of, espn=espn)
        schedules.sync_recent_results(cfg, fixtures_of, days_back=1, espn=espn)
        do_analyze(cfg)
    elif args.cmd == "review":
        from src.output.review import build_daily_review
        review = build_daily_review()
        print(f"\n复盘完成：累计未命中 {review['total_missed']} 场，见 data/reports/review.md")
        if args.notify:
            from src.notify.review import push_review
            push_review(review)
    elif args.cmd == "notify":
        report = read_json(C.REPORTS_DIR / "latest.json", default=None)
        if not report:
            print("暂无报告，请先运行 analyze")
        else:
            from src.notify.push import push_report
            notified_path = C.DATA_DIR / "prematch_notified.json"
            push_report(report, prematch_hours=args.prematch_hours,
                        notified_path=str(notified_path) if args.prematch_hours is not None else None)
    elif args.cmd == "leagues":
        win = _win007_client(cfg, cfg.get("scrapers") or {}, cfg.get("team_name_map") or {})
        try:
            rows = win.discover_leagues(args.date)
        except Exception as e:
            print(f"[联赛发现] 抓取失败：{e}")
            rows = []
        kw = (args.keyword or "").strip()
        if kw:
            rows = [(n, i) for n, i in rows if kw in n]
        if not rows:
            print("未找到联赛（或该日期无数据）。杯赛请在其比赛日查询，如欧联杯比赛日。")
        else:
            print(f"共 {len(rows)} 个联赛（名称 => win007_id）：")
            for name, sid in rows:
                print(f"  {name} => {sid}")


if __name__ == "__main__":
    main()
