"""推荐引擎：临场水位 + 历史战绩 -> 亚盘 / 大小球 推荐。

规则化打分，透明可解释；分数 0-100，越远离 50 代表方向越明确。
"""
from __future__ import annotations

from typing import Optional

from ..models import OddsSnapshot, Recommendation, Result
from . import features as F


def _clamp(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _form_signal(hf: dict, af: dict) -> float:
    """主队 vs 客队近期状态（同口径）。"""
    if not hf["played"] or not af["played"]:
        return 0.0
    win_diff = hf["win_rate"] - af["win_rate"]                        # [-1,1]
    gd_diff = (hf["avg_gf"] - hf["avg_ga"]) - (af["avg_gf"] - af["avg_ga"])
    return _clamp(0.7 * win_diff + 0.3 * (gd_diff / 2.0))


def _h2h_signal(h2h: dict) -> float:
    if not h2h["meetings"]:
        return 0.0
    home_pts = h2h["home_wins"] * 3 + h2h["draws"]
    away_pts = h2h["away_wins"] * 3 + h2h["draws"]
    return _clamp((home_pts - away_pts) / (3 * h2h["meetings"]))


def _home_away_signal(ha: dict) -> float:
    return _clamp(ha["home_home_win_rate"] - ha["away_away_win_rate"])


def _confidence(score: float, thresholds: dict) -> str:
    if score >= thresholds["strong"]:
        return "strong"
    if score >= thresholds["watch"]:
        return "watch"
    return "pass"


def recommend_handicap(comp, cfg: dict, results: list[Result],
                       snaps: list[OddsSnapshot]) -> Optional[Recommendation]:
    sig = F.handicap_signals(snaps)
    if not sig:
        return None

    home, away = snaps[-1].home, snaps[-1].away
    hf = F.form_stats(results, home, cfg["analysis"]["form_matches"])
    af = F.form_stats(results, away, cfg["analysis"]["form_matches"])
    h2h = F.h2h_stats(results, home, away, cfg["analysis"]["h2h_matches"])
    ha = F.home_away_stats(results, home, away)

    w = cfg["analysis"]["weights"]["handicap"]
    combined = (
        w["water_move"] * sig["water_signal"]
        + w["line_move"] * sig["line_signal"]
        + w["form"] * _form_signal(hf, af)
        + w["h2h"] * _h2h_signal(h2h)
        + w["home_away"] * _home_away_signal(ha)
    )

    side = "home" if combined >= 0 else "away"
    score = 50 + 50 * abs(_clamp(combined))
    conf = _confidence(score, cfg["analysis"]["thresholds"])

    line = sig["line_last"] if sig["line_last"] is not None else 0.0
    # line 从主队视角：<=0 主队让球，>0 主队受让；方向侧决定"让"还是"受让"
    if abs(line) < 0.01:
        pick = "平手盘"
    elif side == "home":
        pick = f"主队（{'让' if line <= 0 else '受让'} {abs(line):g}）"
    else:
        pick = f"客队（{'受让' if line <= 0 else '让'} {abs(line):g}）"

    reasons = [
        f"亚盘 {sig['line_desc']}",
        sig["water_desc"],
        f"主队近{hf['played']}场 {hf['wins']}胜{hf['draws']}平{hf['losses']}负，场均进{hf['avg_gf']:.1f}/失{hf['avg_ga']:.1f}",
        f"客队近{af['played']}场 {af['wins']}胜{af['draws']}平{af['losses']}负，场均进{af['avg_gf']:.1f}/失{af['avg_ga']:.1f}",
        f"交手{h2h['meetings']}次：主队{h2h['home_wins']}胜{h2h['draws']}平{h2h['away_wins']}负",
    ]

    history = [
        {"ts": s.ts, "handicap_line": s.handicap_line,
         "handicap_home": s.handicap_home, "handicap_away": s.handicap_away}
        for s in sorted(snaps, key=lambda x: x.ts)
    ]

    return Recommendation(
        fixture_key=snaps[-1].fixture_key, competition=comp.key,
        home=home, away=away, kickoff=snaps[-1].kickoff,
        market="handicap", pick=pick, side=side, confidence=conf,
        score=round(score, 1), reasons=reasons,
        signals=dict(line_desc=sig["line_desc"], water_desc=sig["water_desc"]),
        history=history,
    )


def recommend_totals(comp, cfg: dict, results: list[Result],
                     snaps: list[OddsSnapshot]) -> Optional[Recommendation]:
    sig = F.totals_signals(snaps)
    if not sig:
        return None

    home, away = snaps[-1].home, snaps[-1].away
    hf = F.form_stats(results, home, cfg["analysis"]["form_matches"])
    af = F.form_stats(results, away, cfg["analysis"]["form_matches"])

    # 进攻：双方场均进球之和；防守：双方场均失球之和（相对 2.6 的基线）
    attack = (hf["avg_gf"] + af["avg_gf"] - 2.6) / 0.9
    defense = (hf["avg_ga"] + af["avg_ga"] - 2.6) / 0.9

    w = cfg["analysis"]["weights"]["totals"]
    combined = (
        w["water_move"] * sig["over_signal"]
        + w["line_move"] * sig["line_signal"]
        + w["attacking_form"] * _clamp(attack)
        + w["defensive_form"] * _clamp(defense)
    )

    side = "over" if combined >= 0 else "under"
    score = 50 + 50 * abs(_clamp(combined))
    conf = _confidence(score, cfg["analysis"]["thresholds"])

    line = sig["line_last"] if sig["line_last"] is not None else 2.5
    pick = f"{'大球' if side == 'over' else '小球'}（{line:g}）"

    reasons = [
        f"大小球 {sig['line_desc']}",
        sig["over_desc"],
        f"主队近{hf['played']}场场均总进球 {hf['avg_total']:.1f}",
        f"客队近{af['played']}场场均总进球 {af['avg_total']:.1f}",
    ]

    history = [
        {"ts": s.ts, "total_line": s.total_line, "over": s.over, "under": s.under}
        for s in sorted(snaps, key=lambda x: x.ts)
    ]

    return Recommendation(
        fixture_key=snaps[-1].fixture_key, competition=comp.key,
        home=home, away=away, kickoff=snaps[-1].kickoff,
        market="totals", pick=pick, side=side, confidence=conf,
        score=round(score, 1), reasons=reasons,
        signals=dict(line_desc=sig["line_desc"], over_desc=sig["over_desc"]),
        history=history,
    )


def recommend_competition(comp, cfg: dict, results: list[Result],
                          odds_by_fixture: dict) -> list[Recommendation]:
    """对某联赛/杯赛的所有场次出推荐（联赛与杯赛各自独立调用、独立口径）。"""
    recs: list[Recommendation] = []
    for fk, snaps in odds_by_fixture.items():
        snaps = sorted(snaps, key=lambda s: s.ts)
        for maker in (recommend_handicap, recommend_totals):
            r = maker(comp, cfg, results, snaps)
            if r and r.confidence != "pass":
                recs.append(r)
    return recs
