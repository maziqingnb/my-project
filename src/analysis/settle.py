"""结算：把推荐与最终赛果对比，判定「中 / 没中 / 走盘 / 半赢 / 半输」，并统计命中率。

口径：
- 亚盘：用主队视角让球线（负=主队让球）。主队推荐命中条件：(主进球 - 客进球) + line > 0；
  客队推荐命中条件：(客进球 - 主进球) - line > 0。
- 大小球：大球命中 = 总进球 > line，小球命中 = 总进球 < line。
- 1/4 盘口（.25 / .75）按亚洲盘规则拆成两条半额注结算：
  全赢=中，一赢一走=半赢，一赢一输/两走=走盘，一输一走=半输，全输=没中。
"""
from __future__ import annotations

from typing import Optional

from ..models import norm_team, Recommendation, Result

OUTCOME_CN = {
    "win": "中",
    "half_win": "半赢",
    "push": "走盘",
    "half_loss": "半输",
    "loss": "没中",
}


def _line_legs(line: float) -> list[float]:
    """亚洲盘拆腿：整数/半球盘一条腿，1/4 盘口拆成相邻两条腿。"""
    q = round(line * 4)
    if q % 4 in (0, 2):          # 整数或 0.5 半球
        return [line]
    return [line - 0.25, line + 0.25]   # 0.25 / 0.75 四分盘


def _combine(legs: list[int]) -> str:
    """把每条腿的胜负(-1/0/1)合并成最终结算结果。"""
    w = sum(1 for x in legs if x > 0)
    l = sum(1 for x in legs if x < 0)
    p = sum(1 for x in legs if x == 0)
    if len(legs) == 1:
        return "win" if w else ("loss" if l else "push")
    if w == 2:
        return "win"
    if l == 2:
        return "loss"
    if p == 2:
        return "push"
    if w == 1 and p == 1:
        return "half_win"
    if l == 1 and p == 1:
        return "half_loss"
    return "push"               # 一赢一输 = 走盘


def settle(market: str, side: str, line: Optional[float],
           home_goals: int, away_goals: int) -> Optional[str]:
    """返回 outcome 键（win/half_win/push/half_loss/loss），无法结算返回 None。"""
    if line is None:
        return None
    legs: list[int] = []
    for leg in _line_legs(line):
        if market == "handicap":
            if side == "home":
                diff = home_goals - away_goals
                v = diff + leg
            else:
                diff = away_goals - home_goals
                v = diff - leg
        else:                    # totals
            total = home_goals + away_goals
            if side == "over":
                v = total - leg
            else:
                v = leg - total
        legs.append(1 if v > 0 else (-1 if v < 0 else 0))
    return _combine(legs)


def find_result(results: list[Result], comp_key: str, home: str, away: str,
                day: str) -> Optional[tuple[int, int]]:
    """按 联赛+日期+主客队 找最终比分，返回 (主进球, 客进球) 或 None。

    主客顺序与推荐一致时直接返回；若结果里顺序颠倒则交换比分。
    """
    h, a = norm_team(home), norm_team(away)
    for r in results:
        if r.competition != comp_key or (r.date or "")[:10] != day:
            continue
        rh, ra = norm_team(r.home), norm_team(r.away)
        if rh == h and ra == a:
            return r.home_goals, r.away_goals
    for r in results:
        if r.competition != comp_key or (r.date or "")[:10] != day:
            continue
        rh, ra = norm_team(r.home), norm_team(r.away)
        if rh == a and ra == h:
            return r.away_goals, r.home_goals
    return None


def settle_recommendation(rec: Recommendation, results: list[Result]) -> Optional[dict]:
    """结算一条推荐（Recommendation 对象）。有赛果返回 dict，无赛果返回 None。"""
    return settle_dict(
        dict(competition=rec.competition, home=rec.home, away=rec.away,
             kickoff=rec.kickoff, market=rec.market, side=rec.side, line=rec.line),
        results,
    )


def settle_dict(rec: dict, results: list[Result]) -> Optional[dict]:
    """结算一条推荐（dict 形式，含 competition/home/away/kickoff/market/side/line）。"""
    day = (rec.get("kickoff") or "")[:10]
    goals = find_result(results, rec.get("competition"), rec.get("home"),
                        rec.get("away"), day)
    if goals is None:
        return None
    hg, ag = goals
    outcome = settle(rec.get("market"), rec.get("side"), rec.get("line"), hg, ag)
    if outcome is None:
        return None
    return {
        "outcome": outcome,
        "outcome_cn": OUTCOME_CN[outcome],
        "home_goals": hg,
        "away_goals": ag,
        "settled": True,
    }


# ---------------------------------------------------------------- 命中率统计

def hit_rate_stats(records: list[dict]) -> dict:
    """统计命中率。口径：命中=全中+半赢，已结算=中+半赢+半输+没中（走盘不计）。"""
    counts = {"win": 0, "half_win": 0, "push": 0, "half_loss": 0, "loss": 0}
    for r in records:
        o = r.get("outcome")
        if o in counts:
            counts[o] += 1
    settled = counts["win"] + counts["half_win"] + counts["half_loss"] + counts["loss"]
    hit = counts["win"] + counts["half_win"]
    rate = round(hit / settled * 100, 1) if settled else None
    return {
        "counts": counts,
        "settled": settled,
        "hit": hit,
        "rate": rate,
    }


def group_hit_rates(records: list[dict], key: str) -> dict:
    """按某字段（market / competition）分组统计命中率。"""
    buckets: dict[str, list[dict]] = {}
    for r in records:
        buckets.setdefault(r.get(key, "?"), []).append(r)
    return {k: hit_rate_stats(v) for k, v in sorted(buckets.items())}
