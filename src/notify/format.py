"""把推荐报告转成适合微信推送的文案。"""
from __future__ import annotations

_MARKET = {"handicap": "亚盘", "totals": "大小球"}


def build_title(report: dict) -> str:
    strong = sum(1 for r in report["recommendations"] if r["confidence"] == "strong")
    return f"足球水位分析 · 强推{strong}条/共{report['total']}条"


def build_content(report: dict) -> str:
    recs = report["recommendations"]
    strong = [r for r in recs if r["confidence"] == "strong"]
    watch = [r for r in recs if r["confidence"] == "watch"]
    ts = report["generated_at"][:16].replace("T", " ")

    lines = [
        f"**⚽ 足球水位分析 · {report['total']} 条推荐**",
        f"> {ts} (UTC) · {report['note']}",
    ]

    def _lines(section_title: str, items: list) -> list[str]:
        if not items:
            return []
        out = ["", f"**{section_title}**"]
        for i, r in enumerate(items, 1):
            mkt = _MARKET.get(r["market"], r["market"])
            tag = "🏆杯赛 " if r["competition_class"] == "cup" else ""
            sig = (r.get("signals") or {}).get("water_desc") \
                or (r.get("signals") or {}).get("over_desc") \
                or (r.get("signals") or {}).get("line_desc") or ""
            out.append(f"{i}. {tag}{r['home']} vs {r['away']}")
            out.append(f"   {mkt} {r['pick']} · {r['score']}分" + (f" · {sig}" if sig else ""))
        return out

    lines += _lines("🏆 强推", strong)
    lines += _lines("👀 可关注", watch)
    return "\n".join(lines)
