"""把推荐报告转成适合微信推送的文案。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ..models import market_cn


def _kickoff(iso: str) -> str:
    """内部存 UTC，展示转回北京时间(UTC+8)。"""
    try:
        dt = datetime.fromisoformat(iso).replace(tzinfo=timezone.utc) + timedelta(hours=8)
        return dt.strftime("%m-%d %H:%M")
    except (ValueError, TypeError):
        return ""


def build_title(report: dict) -> str:
    return f"足球水位分析 · 强推{report['total']}条"


def build_content(report: dict) -> str:
    recs = report["recommendations"]
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
            mkt = market_cn(r["market"], r.get("sport", "football"))
            tag = "🏆杯赛 " if r["competition_class"] == "cup" else ""
            ko = _kickoff(r.get("kickoff", ""))
            sig = (r.get("signals") or {}).get("water_desc") \
                or (r.get("signals") or {}).get("over_desc") \
                or (r.get("signals") or {}).get("line_desc") or ""
            out.append(f"{i}. {tag}{r['home']} vs {r['away']}" + (f"（{ko}开赛）" if ko else ""))
            out.append(f"   {mkt} {r['pick']} · {r['score']}分" + (f" · {sig}" if sig else ""))
        return out

    lines += _lines("🏆 强推", recs)
    return "\n".join(lines)
