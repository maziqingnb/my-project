"""回测：评估当前规则引擎在历史场次上的命中率（按盘口/联赛分解）。

关键（数据泄漏修正）：模拟「开赛时」的信息集——近况/交手只用该场**开赛前**的
赛果（date < 该场 kickoff 日期），不把该场自己的赛果喂给 form_stats，否则命中率
被自己这场的结果虚高。生产环境的 analyze 是开赛前跑，天然无泄漏；回测必须显式剔除。

用法：
  python src/backtest.py             # 全量回测，打印命中率分解
"""
from __future__ import annotations

import sys
from collections import defaultdict, Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import db, config
from src.analysis.recommender import recommend_competition
from src.analysis.settle import settle_dict


def _hit_rate(c: Counter) -> str:
    hit = c["win"] + c["half_win"]
    settled = c["win"] + c["half_win"] + c["half_loss"] + c["loss"]
    return f"{hit}/{settled} = {hit/settled*100:.0f}%" if settled else "-"


def run_backtest(cfg: dict, conn) -> dict:
    comp_map = {c.key: c for c in cfg["competitions"]}
    # 赔率快照按 (联赛, fixture) 分组
    odds_by_comp: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    for s in db.load_odds(conn):
        odds_by_comp[s.competition][s.fixture_key].append(s)

    results_by_comp = {c.key: db.load_results(conn, c.key) for c in cfg["competitions"]}

    by_market: dict[str, Counter] = defaultdict(Counter)
    by_comp: dict[str, Counter] = defaultdict(Counter)
    by_form_sample: dict[str, Counter] = defaultdict(Counter)   # 近况样本量桶 -> outcome
    total = settled = 0

    for comp in cfg["competitions"]:
        odds = odds_by_comp.get(comp.key, {})
        if not odds:
            continue
        results = results_by_comp.get(comp.key, [])
        for fk, snaps in odds.items():
            snaps = sorted(snaps, key=lambda s: s.ts)
            kickoff = snaps[-1].kickoff
            day = (kickoff or "")[:10]
            if not day:
                continue
            # 泄漏修正：只用开赛前的赛果做近况/交手
            pre = [r for r in results if (r.date or "")[:10] < day]
            recs = recommend_competition(comp, cfg, pre, {fk: snaps})
            if not recs:
                continue
            r = recs[0]
            if r.confidence != "strong":
                continue
            total += 1
            s = settle_dict(dict(competition=r.competition, home=r.home, away=r.away,
                                 kickoff=r.kickoff, market=r.market, side=r.side,
                                 line=r.line), results)
            if not s:
                continue
            settled += 1
            by_market[r.market][s["outcome"]] += 1
            by_comp[comp.key][s["outcome"]] += 1
            # 近况样本量：双方开赛前赛果合计（用于验证「样本少→大小球噪声」的假设）
            home = r.home
            away = r.away
            n = sum(1 for x in pre if x.home == home or x.away == home) + \
                sum(1 for x in pre if x.home == away or x.away == away)
            bucket = "0-1" if n <= 1 else ("2-3" if n <= 3 else ("4-5" if n <= 5 else "6+"))
            by_form_sample[bucket][s["outcome"]] += 1

    return {
        "total": total,
        "settled": settled,
        "by_market": by_market,
        "by_comp": by_comp,
        "by_form_sample": by_form_sample,
    }


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    cfg = config.load_config()
    conn = db.connect()
    try:
        r = run_backtest(cfg, conn)
    finally:
        conn.close()

    print(f"强推 {r['total']} 场，已结算 {r['settled']} 场")
    print("\n按盘口：")
    for m, c in sorted(r["by_market"].items()):
        print(f"  {m:9s} {dict(c)} 命中 {_hit_rate(c)}")

    print("\n按联赛（命中率）：")
    name = {c.key: c.name for c in cfg["competitions"]}
    for k, c in sorted(r["by_comp"].items(), key=lambda kv: -_hit_rate_pct(kv[1])):
        print(f"  {name.get(k, k):20s} {_hit_rate(c)}  (n={sum(c.values())})")

    print("\n按开赛前近况样本量（双方合计）：")
    for k in ("0-1", "2-3", "4-5", "6+"):
        c = r["by_form_sample"].get(k)
        if c:
            print(f"  {k:4s} {dict(c)} 命中 {_hit_rate(c)}  (n={sum(c.values())})")


def _hit_rate_pct(c: Counter) -> float:
    hit = c["win"] + c["half_win"]
    settled = c["win"] + c["half_win"] + c["half_loss"] + c["loss"]
    return hit / settled * 100 if settled else -1.0


if __name__ == "__main__":
    main()
