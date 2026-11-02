"""每日复盘：总结没命中（没中 / 半输）的推荐，输出 JSON + Markdown。

数据来源是 data/reports/history.json 的账本（已结算记录），
按开赛日期分组，逐场给出「推荐了什么、最终比分、为何没打穿」。
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone

from .. import config as C
from ..storage import read_json, write_json

_MARKET = {"handicap": "亚盘(让球)", "totals": "大小球"}
_MISSED = ("loss", "half_loss")


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
        "pick": r.get("pick"),
        "side": r.get("side"),
        "line": r.get("line"),
        "score": r.get("score"),
        "confidence": r.get("confidence"),
        "outcome": r.get("outcome"),
        "outcome_cn": r.get("outcome_cn"),
        "home_goals": r.get("home_goals"),
        "away_goals": r.get("away_goals"),
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
            mkt = _MARKET.get(it["market"], it["market"])
            lines.append(f"{i}. {it['home']} vs {it['away']} — {mkt} {it['pick']}")
            lines.append(f"   → {it['outcome_cn']}，{it['note']}")
            sig = it.get("signals") or {}
            sig_txt = sig.get("water_desc") or sig.get("over_desc") or sig.get("line_desc")
            if sig_txt:
                lines.append(f"   当时信号：{sig_txt}")
        lines.append("")

    from ..storage import ensure_dir
    path = C.REPORTS_DIR / "review.md"
    ensure_dir(path.parent)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
