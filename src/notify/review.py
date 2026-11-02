"""把每日复盘推送到微信（复用 push 里的渠道装配）。"""
from __future__ import annotations

from ..models import market_cn
from .push import _channels


def _build_content(review: dict) -> str:
    days = review.get("days") or []
    lines = [
        f"**📋 足球推荐 · 每日复盘**",
        f"> 累计未命中 {review.get('total_missed', 0)} 场（没中/半输）",
    ]
    for day in days[:3]:        # 微信推送只取最近 3 天，避免过长
        lines.append(f"\n**{day['date']}（{day['missed']} 场）**")
        for it in day["items"][:10]:
            mkt = market_cn(it["market"], it.get("sport", "football"))
            lines.append(f"- {it['home']} vs {it['away']} · {mkt} {it['pick']} → {it['outcome_cn']}")
            if it.get("book"):
                lines.append(f"  盘口：{it['book']}")
            if it.get("diagnosis"):
                lines.append(f"  复盘：{'；'.join(it['diagnosis'])}")
    return "\n".join(lines)


def push_review(review: dict) -> None:
    channels = _channels()
    if not channels:
        print("[通知] 未配置推送渠道，跳过复盘推送")
        return
    title = f"足球复盘 · 未命中 {review.get('total_missed', 0)} 场"
    content = _build_content(review)
    for ch in channels:
        try:
            ch.send(title, content)
            print(f"[通知] 复盘已推送至 {type(ch).__name__}")
        except Exception as e:
            print(f"[通知] {type(ch).__name__} 推送失败: {e}")
