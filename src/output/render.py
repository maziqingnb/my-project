"""渲染推荐结果：JSON（供看板）+ Markdown（供人读）。"""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone

from .. import config as C
from ..storage import write_json

_CONF_CN = {"strong": "强推", "watch": "可关注", "pass": "观望"}
_MARKET_CN = {"handicap": "亚盘(让球)", "totals": "大小球"}


def render_report(cfg: dict, recs) -> dict:
    comp_map = {c.key: c for c in cfg["competitions"]}
    items = []
    for r in recs:
        c = comp_map[r.competition]
        items.append({
            **asdict(r),
            "competition_name": c.name,
            "competition_class": c.cls,
        })

    order = {"strong": 0, "watch": 1, "pass": 2}
    items.sort(key=lambda x: (order[x["confidence"]], -x["score"]))

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope": "五大联赛 + 欧冠",
        "note": "杯赛与联赛数据分开计算",
        "total": len(items),
        "recommendations": items,
    }

    write_json(C.REPORTS_DIR / "latest.json", report)
    write_json(C.DOCS_REPORTS_DIR / "latest.json", report)   # 供 GitHub Pages 看板读取
    _write_markdown(report)
    return report


def _write_markdown(report: dict) -> None:
    lines = [
        "# 足球水位分析报告",
        "",
        f"- 生成时间：{report['generated_at']}（UTC）",
        f"- 覆盖范围：{report['scope']}；{report['note']}",
        f"- 推荐条数：{report['total']}",
        "",
    ]
    if not report["recommendations"]:
        lines.append("暂无推荐（可能缺少临场水位数据）。")
    else:
        lines.append("| 联赛/杯赛 | 比赛 | 盘口 | 推荐 | 方向 | 信心 | 分数 |")
        lines.append("|---|---|---|---|---|---|---|")
        for r in report["recommendations"]:
            tag = "🏆杯赛" if r["competition_class"] == "cup" else "联赛"
            lines.append(
                f"| {r['competition_name']}({tag}) | {r['home']} vs {r['away']} "
                f"| {_MARKET_CN[r['market']]} | {r['pick']} | {r['side']} "
                f"| {_CONF_CN[r['confidence']]} | {r['score']} |"
            )

        lines += ["", "## 推荐理由", ""]
        for r in report["recommendations"]:
            lines.append(f"### {r['home']} vs {r['away']} — {r['pick']}（{_CONF_CN[r['confidence']]} / {r['score']} 分）")
            for reason in r["reasons"]:
                lines.append(f"- {reason}")
            lines.append("")

    _write_text(C.REPORTS_DIR / "report.md", "\n".join(lines) + "\n")


def _write_text(path, text: str) -> None:
    from ..storage import ensure_dir
    ensure_dir(path.parent)
    path.write_text(text, encoding="utf-8")
