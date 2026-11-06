"""特征工程：把赔率水位历史 + 历史结果 转成可用于打分的信号。

关键点：近况 / 交手只使用同一口径（联赛 or 杯赛）的比赛，
实现"杯赛数据和联赛数据分开计算"。
"""
from __future__ import annotations

from typing import Optional

from ..models import OddsSnapshot, Result, norm_team


def _clamp(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _primary_snaps(snaps: list) -> list:
    """只取一家博彩公司的快照，避免多家盘口混在一起造成假的盘口/水位"变动"。

    数据准确性问题：the-odds-api 会同时返回 pinnacle/bet365/unibet 多家盘口，
    若直接用首/末两条快照算「变动」，实际是在拿不同公司的盘口互比（各公司水位、
    让球线本就不同），把「公司差异」误当成「市场变动」。这里优先取 pinnacle
    （最锐利、代表性最强），否则取快照数最多的那家；足球(win007)全部是 crown，
    等价于原样返回。返回按 ts 升序。
    """
    if not snaps:
        return []
    by_book: dict[str, list] = {}
    for s in snaps:
        by_book.setdefault(s.bookmaker or "", []).append(s)
    if "pinnacle" in by_book:
        return sorted(by_book["pinnacle"], key=lambda s: s.ts)
    return sorted(max(by_book.values(), key=len), key=lambda s: s.ts)


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


# ---------------------------------------------------------------- 历史盘口

def h2h_line_stats(results: list[Result], home: str, away: str, n: int = 5) -> dict:
    """历史交锋的「盘口覆盖」统计（分析结合过往比赛盘口）。

    对两队近 n 次直接交锋，用当时那场的让球线（主队视角，负=主让）判定
    「谁打穿了盘口」，返回当前主队/客队各自打穿次数与平均让球线。
    若历史结果没带盘口（结果里 handicap_line 为 None），meetings 记为 0。
    """
    h, a = norm_team(home), norm_team(away)
    meetings = [r for r in results if {norm_team(r.home), norm_team(r.away)} == {h, a}]
    meetings.sort(key=lambda r: r.date, reverse=True)
    meetings = meetings[:n]

    home_cover = away_cover = 0
    lines = []
    for r in meetings:
        if r.handicap_line is None:
            continue
        lines.append(r.handicap_line)
        # 历史那场：历史主队是否打穿（历史主净胜 + 让球线 > 0）
        hist_home_cover = (r.home_goals - r.away_goals) + r.handicap_line > 0
        if norm_team(r.home) == h:
            if hist_home_cover:
                home_cover += 1
            else:
                away_cover += 1
        else:
            if hist_home_cover:
                away_cover += 1
            else:
                home_cover += 1

    m = len(lines)
    return dict(meetings=m, home_cover=home_cover, away_cover=away_cover,
                avg_line=(sum(lines) / m if m else None))


def h2h_totals_stats(results: list[Result], home: str, away: str, n: int = 5) -> dict:
    """历史交锋的「大小球盘口覆盖」：近 n 次交手，按当时总进球线判定大/小次数。"""
    h, a = norm_team(home), norm_team(away)
    meetings = [r for r in results if {norm_team(r.home), norm_team(r.away)} == {h, a}]
    meetings.sort(key=lambda r: r.date, reverse=True)
    meetings = meetings[:n]

    over = under = 0
    lines = []
    for r in meetings:
        if r.total_line is None:
            continue
        lines.append(r.total_line)
        if (r.home_goals + r.away_goals) > r.total_line:
            over += 1
        else:
            under += 1

    m = len(lines)
    return dict(meetings=m, over=over, under=under,
                avg_line=(sum(lines) / m if m else None))


# ---------------------------------------------------------------- 水位/盘口信号

def handicap_signals(snaps: list[OddsSnapshot]) -> Optional[dict]:
    """亚盘信号：初盘 vs 临场（首条 vs 末条快照）。返回主队视角的方向信号。

    信号带量级（不再是 ±1 二值）：盘口按 0.5 球归一、水位按 0.15 归一，
    避免「升盘 0.25」和「升盘 1.0」被当成同样强。line_signal / water_signal 同号
    （正=主队被看好）时彼此印证；异号则 conflict=True。
    """
    h = [s for s in _primary_snaps(snaps) if s.handicap_line is not None]
    if not h:
        return None
    first, last = h[0], h[-1]

    line_signal = 0.0
    line_desc = "盘口不变"
    if first.handicap_line is not None and last.handicap_line is not None:
        d = first.handicap_line - last.handicap_line   # >0：让球加深，主队被看好
        if abs(d) > 0.05:
            line_signal = _clamp(d / 0.5)
            line_desc = (f"升盘 {first.handicap_line}->{last.handicap_line}（主队走强）"
                         if d > 0 else
                         f"降盘 {first.handicap_line}->{last.handicap_line}（主队走弱）")

    water_signal = 0.0
    water_desc = "水位平稳"
    if first.handicap_home and last.handicap_home:
        d = first.handicap_home - last.handicap_home   # >0：主队水位下降 = 降水
        if abs(d) > 0.02:
            water_signal = _clamp(d / 0.15)
            water_desc = (f"主队降水 {first.handicap_home}->{last.handicap_home}"
                          if d > 0 else
                          f"主队升水 {first.handicap_home}->{last.handicap_home}")

    conflict = bool(line_signal and water_signal and line_signal * water_signal < 0)
    return dict(
        line_first=first.handicap_line, line_last=last.handicap_line,
        home_water_first=first.handicap_home, home_water_last=last.handicap_home,
        away_water_first=first.handicap_away, away_water_last=last.handicap_away,
        line_signal=line_signal, water_signal=water_signal,
        line_desc=line_desc, water_desc=water_desc, conflict=conflict,
    )


def totals_signals(snaps: list[OddsSnapshot]) -> Optional[dict]:
    """大小球信号：初盘 vs 临场，返回"大球"视角的方向信号（带量级）。

    水位变化对大小球尤其噪声大（复盘里多次「大球降水→反而小球」），
    因此这里额外返回 conflict（盘口方向与水位方向背离）供推荐层降权/改走方向。
    """
    t = [s for s in _primary_snaps(snaps) if s.total_line is not None]
    if not t:
        return None
    first, last = t[0], t[-1]

    line_signal = 0.0
    line_desc = "盘口不变"
    if first.total_line is not None and last.total_line is not None:
        d = last.total_line - first.total_line       # >0：升盘，看大
        if abs(d) > 0.05:
            line_signal = _clamp(d / 0.5)
            line_desc = (f"升盘 {first.total_line}->{last.total_line}（看大）"
                         if d > 0 else
                         f"降盘 {first.total_line}->{last.total_line}（看小）")

    over_signal = 0.0
    over_desc = "大球水位平稳"
    if first.over and last.over:
        d = first.over - last.over                   # >0：大球降水，看大
        if abs(d) > 0.02:
            over_signal = _clamp(d / 0.15)
            over_desc = (f"大球降水 {first.over}->{last.over}"
                         if d > 0 else
                         f"大球升水 {first.over}->{last.over}")

    conflict = bool(line_signal and over_signal and line_signal * over_signal < 0)
    return dict(
        line_first=first.total_line, line_last=last.total_line,
        over_first=first.over, over_last=last.over,
        under_first=first.under, under_last=last.under,
        line_signal=line_signal, over_signal=over_signal,
        line_desc=line_desc, over_desc=over_desc, conflict=conflict,
    )
