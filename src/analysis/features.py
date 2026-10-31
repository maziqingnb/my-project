"""特征工程：把赔率水位历史 + 历史结果 转成可用于打分的信号。

关键点：近况 / 交手只使用同一口径（联赛 or 杯赛）的比赛，
实现"杯赛数据和联赛数据分开计算"。
"""
from __future__ import annotations

from typing import Optional

from ..models import OddsSnapshot, Result, norm_team


def _clamp(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


# ---------------------------------------------------------------- 历史战绩

def form_stats(results: list[Result], team: str, n: int = 5) -> dict:
    """某队近 n 场同口径比赛的表现。"""
    tm = norm_team(team)
    matches = [r for r in results if norm_team(r.home) == tm or norm_team(r.away) == tm]
    matches.sort(key=lambda r: r.date, reverse=True)
    matches = matches[:n]

    if not matches:
        return dict(played=0, wins=0, draws=0, losses=0,
                    gf=0, ga=0, avg_gf=0.0, avg_ga=0.0, avg_total=0.0,
                    win_rate=0.0, ppg=0.0)

    wins = draws = losses = 0
    gf = ga = 0
    for r in matches:
        is_home = norm_team(r.home) == tm
        hg, ag = r.home_goals, r.away_goals
        gf += hg if is_home else ag
        ga += ag if is_home else hg
        if hg == ag:
            draws += 1
        elif (hg > ag) == is_home:
            wins += 1
        else:
            losses += 1

    p = len(matches)
    return dict(
        played=p, wins=wins, draws=draws, losses=losses,
        gf=gf, ga=ga,
        avg_gf=gf / p, avg_ga=ga / p, avg_total=(gf + ga) / p,
        win_rate=wins / p, ppg=(wins * 3 + draws) / p,
    )


def h2h_stats(results: list[Result], home: str, away: str, n: int = 5) -> dict:
    """两队近 n 次直接交锋（同口径）。"""
    h, a = norm_team(home), norm_team(away)
    meetings = [r for r in results if {norm_team(r.home), norm_team(r.away)} == {h, a}]
    meetings.sort(key=lambda r: r.date, reverse=True)
    meetings = meetings[:n]

    home_wins = away_wins = draws = 0
    total_goals = 0
    for r in meetings:
        is_home = norm_team(r.home) == h
        hg, ag = r.home_goals, r.away_goals
        total_goals += hg + ag
        if hg == ag:
            draws += 1
        elif (hg > ag) == is_home:
            home_wins += 1
        else:
            away_wins += 1

    m = len(meetings)
    return dict(meetings=m, home_wins=home_wins, away_wins=away_wins, draws=draws,
                avg_total=total_goals / m if m else 0.0)


def home_away_stats(results: list[Result], home: str, away: str) -> dict:
    """主队主场战绩 vs 客队客场战绩（同口径）。"""
    def win_rate(rs):
        if not rs:
            return 0.0
        w = sum(1 for r in rs if r.home_goals > r.away_goals)
        return w / len(rs)

    home_home = [r for r in results if norm_team(r.home) == norm_team(home)]
    away_away = [r for r in results if norm_team(r.away) == norm_team(away)]
    return dict(home_home_win_rate=win_rate(home_home), away_away_win_rate=win_rate(away_away))


# ---------------------------------------------------------------- 水位/盘口信号

def handicap_signals(snaps: list[OddsSnapshot]) -> Optional[dict]:
    """亚盘信号：初盘 vs 临场（首条 vs 末条快照）。返回主队视角的方向信号。"""
    h = [s for s in snaps if s.handicap_line is not None]
    if not h:
        return None
    first, last = h[0], h[-1]

    line_signal = 0.0
    line_desc = "盘口不变"
    if first.handicap_line is not None and last.handicap_line is not None:
        d = first.handicap_line - last.handicap_line   # >0：让球加深，主队被看好
        if d > 0.05:
            line_signal, line_desc = 1.0, f"升盘 {first.handicap_line}->{last.handicap_line}（主队走强）"
        elif d < -0.05:
            line_signal, line_desc = -1.0, f"降盘 {first.handicap_line}->{last.handicap_line}（主队走弱）"

    water_signal = 0.0
    water_desc = "水位平稳"
    if first.handicap_home and last.handicap_home:
        d = first.handicap_home - last.handicap_home   # >0：主队水位下降 = 降水
        if d > 0.03:
            water_signal, water_desc = 1.0, f"主队降水 {first.handicap_home}->{last.handicap_home}"
        elif d < -0.03:
            water_signal, water_desc = -1.0, f"主队升水 {first.handicap_home}->{last.handicap_home}"

    return dict(
        line_first=first.handicap_line, line_last=last.handicap_line,
        home_water_first=first.handicap_home, home_water_last=last.handicap_home,
        line_signal=line_signal, water_signal=water_signal,
        line_desc=line_desc, water_desc=water_desc,
    )


def totals_signals(snaps: list[OddsSnapshot]) -> Optional[dict]:
    """大小球信号：初盘 vs 临场，返回"大球"视角的方向信号。"""
    t = [s for s in snaps if s.total_line is not None]
    if not t:
        return None
    first, last = t[0], t[-1]

    line_signal = 0.0
    line_desc = "盘口不变"
    if first.total_line is not None and last.total_line is not None:
        d = last.total_line - first.total_line       # >0：升盘，看大
        if d > 0.05:
            line_signal, line_desc = 1.0, f"升盘 {first.total_line}->{last.total_line}（看大）"
        elif d < -0.05:
            line_signal, line_desc = -1.0, f"降盘 {first.total_line}->{last.total_line}（看小）"

    over_signal = 0.0
    over_desc = "大球水位平稳"
    if first.over and last.over:
        d = first.over - last.over                   # >0：大球降水，看大
        if d > 0.03:
            over_signal, over_desc = 1.0, f"大球降水 {first.over}->{last.over}"
        elif d < -0.03:
            over_signal, over_desc = -1.0, f"大球升水 {first.over}->{last.over}"

    return dict(
        line_first=first.total_line, line_last=last.total_line,
        over_first=first.over, over_last=last.over,
        line_signal=line_signal, over_signal=over_signal,
        line_desc=line_desc, over_desc=over_desc,
    )
