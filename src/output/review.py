"""每日复盘：总结没命中（没中 / 半输）的推荐，输出 JSON + Markdown。

数据来源是 data/reports/history.json 的账本（已结算记录），
按开赛日期分组，逐场给出「推荐了什么、最终比分、为何没打穿」。
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone

from .. import config as C
from ..models import market_cn
from ..storage import read_json, write_json

_MISSED = ("loss", "half_loss")


def _fmt_w(v):
    return f"{v:.2f}" if isinstance(v, (int, float)) else "?"


def _fmt_hcap(line):
    if line is None:
        return "?"
    if abs(line) < 0.01:
        return "平手"
    return f"主{'让' if line < 0 else '受让'}{abs(line):g}"


def _fmt_total(line):
    return f"{line:g}" if line is not None else "?"


def _book(r: dict) -> str:
    """还原推荐当时的盘口（初盘 → 临场的盘口 + 水位），来自 signals 里的原始数值。"""
    sig = r.get("signals") or {}
    if r.get("market") == "handicap":
        parts = []
        if sig.get("line_first") is not None or sig.get("line_last") is not None:
            parts.append(f"盘口 {_fmt_hcap(sig.get('line_first'))} → {_fmt_hcap(sig.get('line_last'))}")
        if sig.get("home_water_first") is not None or sig.get("away_water_first") is not None:
            parts.append(f"水位 主{_fmt_w(sig.get('home_water_first'))}/{_fmt_w(sig.get('away_water_first'))}"
                         f" → 主{_fmt_w(sig.get('home_water_last'))}/{_fmt_w(sig.get('away_water_last'))}")
        return "，".join(parts) or "盘口信息缺失"
    parts = []
    if sig.get("line_first") is not None or sig.get("line_last") is not None:
        parts.append(f"盘口 {_fmt_total(sig.get('line_first'))} → {_fmt_total(sig.get('line_last'))}")
    if sig.get("over_first") is not None or sig.get("under_first") is not None:
        parts.append(f"水位 大{_fmt_w(sig.get('over_first'))}/小{_fmt_w(sig.get('under_first'))}"
                     f" → 大{_fmt_w(sig.get('over_last'))}/小{_fmt_w(sig.get('under_last'))}")
    return "，".join(parts) or "盘口信息缺失"


def _diagnose(r: dict) -> list[str]:
    """对一条未命中推荐做复盘要点（结合当时盘口 + 最终比分）。"""
    mkt = r.get("market")
    sig = r.get("signals") or {}
    hg, ag = r.get("home_goals"), r.get("away_goals")
    line = r.get("line")
    side = r.get("side")
    pts = [_book(r)]

    if mkt == "handicap":
        if hg is not None and ag is not None and line is not None:
            if side == "home":
                margin = (hg - ag) + line      # 主队相对让球线
            else:
                margin = (ag - hg) - line
            pts.append(f"最终 {hg}-{ag}，推荐侧相对盘口 {'+' if margin >= 0 else ''}{margin:+.2f} 球"
                       f"（{'打穿' if margin > 0 else '未打穿'}）")
        lf, ll = sig.get("line_first"), sig.get("line_last")
        if lf is not None and ll is not None and (lf - ll) > 0.05 and side == "home":
            pts.append("初盘→临场升盘仍推主队：盘口已偏深，注意「升盘陷阱」")
        elif lf is not None and ll is not None and (lf - ll) < -0.05 and side == "home":
            pts.append("初盘→临场降盘仍推主队：市场看弱主队，方向与盘口背离")
        wd = sig.get("water_desc") or ""
        if "升水" in wd and side == "home":
            pts.append("主队升水（资金偏向客队）却推主队，水位信号与方向背离")
    else:
        if hg is not None and ag is not None and line is not None:
            total = hg + ag
            pts.append(f"最终总进球 {total}，相对盘口 {line:g} {'偏大' if total > line else '偏小'}")
        od = sig.get("over_desc") or ""
        if "升水" in od and side == "over":
            pts.append("大球升水（资金偏向小球）却推大球，水位信号与方向背离")
    return pts


def _note(r: dict) -> str:
    hg, ag = r.get("home_goals"), r.get("away_goals")
    pick = r.get("pick", "")
    if hg is None or ag is None:
        return pick
    total = hg + ag
    if r.get("market") == "handicap":
        diff = hg - ag
        if r.get("side") == "home":
            d = f"主队净胜 {diff}"
        else:
            d = f"客队净胜 {-diff}"
        return f"{pick} · 最终 {hg}-{ag}（{d}），未打穿"
    return f"{pick} · 最终 {hg}-{ag}（总进球 {total}），未打出"


def _item(r: dict) -> dict:
    signals = r.get("signals") or {}
    reasons = r.get("reasons") or []
    return {
        "competition": r.get("competition"),
        "home": r.get("home"),
        "away": r.get("away"),
        "kickoff": r.get("kickoff"),
        "market": r.get("market"),
        "sport": r.get("sport", "football"),
        "pick": r.get("pick"),
        "side": r.get("side"),
        "line": r.get("line"),
        "score": r.get("score"),
        "confidence": r.get("confidence"),
        "outcome": r.get("outcome"),
        "outcome_cn": r.get("outcome_cn"),
        "home_goals": r.get("home_goals"),
        "away_goals": r.get("away_goals"),
        "book": _book(r),
        "diagnosis": _diagnose(r),
        "note": _note(r),
        "signals": signals,
        "reasons": reasons,
    }


def build_daily_review() -> dict:
    hist = read_json(C.REPORTS_DIR / "history.json", default={"records": []}) or {}
    records = hist.get("records", [])

    missed = [r for r in records if r.get("settled") and r.get("outcome") in _MISSED]

    by_day: dict[str, list[dict]] = defaultdict(list)
    for r in missed:
        by_day[(r.get("kickoff") or "")[:10]].append(r)

    days = []
    for day in sorted(by_day.keys(), reverse=True):
        items = sorted(by_day[day], key=lambda r: (r.get("kickoff") or ""), reverse=True)
        days.append({"date": day, "missed": len(items), "items": [_item(r) for r in items]})

    review = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_missed": len(missed),
        "days": days,
    }
    write_json(C.REPORTS_DIR / "review.json", review)
    write_json(C.DOCS_REPORTS_DIR / "review.json", review)
    _write_markdown(review)
    return review


def _write_markdown(review: dict) -> None:
    lines = [
        "# 足球推荐 · 每日复盘",
        "",
        f"- 生成时间：{review['generated_at']}（UTC）",
        f"- 未命中累计：{review['total_missed']} 场（没中/半输）",
        "",
    ]
    if not review["days"]:
        lines.append("暂无未命中的推荐。")
    for day in review["days"]:
        lines.append(f"## {day['date']}（{day['missed']} 场未命中）")
        lines.append("")
        for i, it in enumerate(day["items"], 1):
            mkt = market_cn(it["market"], it.get("sport", "football"))
            lines.append(f"{i}. {it['home']} vs {it['away']} — {mkt} {it['pick']}")
            lines.append(f"   → {it['outcome_cn']}，{it['note']}")
            if it.get("book"):
                lines.append(f"   当时盘口：{it['book']}")
            for d in it.get("diagnosis") or []:
                lines.append(f"   · {d}")
        lines.append("")

    from ..storage import ensure_dir
    path = C.REPORTS_DIR / "review.md"
    ensure_dir(path.parent)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
