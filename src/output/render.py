"""渲染推荐结果：JSON（供看板）+ Markdown（供人读）+ 结算与命中率统计。

每次 analyze 会：
1. 为每条推荐匹配最终赛果，结算「中/没中/走盘/半赢/半输」；
2. 把当前推荐并入历史账本（data/reports/history.json），赛果一到就补结算；
3. 输出当前报告与累计命中率（整体 + 按盘口 + 按联赛）。
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta, timezone

from .. import config as C
from ..storage import write_json, read_json
from ..analysis.settle import (settle_dict, hit_rate_stats, group_hit_rates,
                               OUTCOME_CN)

_CONF_CN = {"strong": "强推", "watch": "可关注", "pass": "观望"}
_MARKET_CN = {"handicap": "亚盘(让球)", "totals": "大小球"}
_HISTORY_FIELDS = ("fixture_key", "competition", "home", "away", "kickoff",
                   "market", "pick", "side", "line", "score", "confidence",
                   "prematch")


def _parse_ts(s):
    """解析 ISO 时间（容忍无时区，按 UTC 处理）。"""
    if not s:
        return None
    s = s.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _is_prematch(kickoff, history, hours: float) -> bool:
    """最新一条水位快照是否落在开赛前 hours 小时内（临场）。"""
    ko = _parse_ts(kickoff)
    if not ko or not history:
        return False
    last = None
    for h in history or []:
        dt = _parse_ts(h.get("ts") if isinstance(h, dict) else None)
        if dt and (last is None or dt > last):
            last = dt
    if not last:
        return False
    delta = ko - last
    return timedelta(0) <= delta <= timedelta(hours=hours)


def _is_counted(item: dict) -> bool:
    """命中率只统计强推 + 临场。"""
    return item.get("confidence") == "strong" and bool(item.get("prematch"))


def _record(item: dict) -> dict:
    return {k: item.get(k) for k in _HISTORY_FIELDS}


def _settle_item(item: dict, results_by_comp: dict) -> None:
    """就地给 item 附加 result 字段（有赛果则结算，否则标记未结算）。"""
    results = results_by_comp.get(item.get("competition"), [])
    res = settle_dict(item, results)
    item["result"] = res if res else {"settled": False}


def _update_history(items: list[dict], results_by_comp: dict) -> dict:
    """把当前推荐并入历史账本，重结算已有记录，返回账本（含累计命中率）。"""
    history_path = C.REPORTS_DIR / "history.json"
    prev = read_json(history_path, default={"records": []}) or {}
    by_id = {}
    for rec in prev.get("records", []):
        by_id[rec.get("id") or rec.get("fixture_key")] = rec

    # 合并本次推荐（同一场比赛只保留最新推荐）
    for item in items:
        rid = item.get("fixture_key")
        merged = {**by_id.get(rid, {}), **_record(item)}
        merged["id"] = rid
        by_id[rid] = merged

    # 对账本里所有记录重结算（赛果陆续到位后自动补上）
    records = []
    now = datetime.now(timezone.utc).isoformat()
    for rid, rec in by_id.items():
        results = results_by_comp.get(rec.get("competition"), [])
        res = settle_dict(rec, results)
        if res:
            rec.update({k: res[k] for k in
                        ("outcome", "outcome_cn", "home_goals", "away_goals", "settled")})
        else:
            rec.setdefault("settled", False)
        rec["generated_at"] = now
        records.append(rec)

    records.sort(key=lambda r: (r.get("kickoff") or ""), reverse=True)
    counted = [r for r in records if _is_counted(r)]
    history = {
        "generated_at": now,
        "total": len(counted),              # 累计推荐 = 强推 + 临场
        "stats": {
            "overall": hit_rate_stats(counted),
            "by_market": group_hit_rates(counted, "market"),
            "by_competition": group_hit_rates(counted, "competition"),
        },
        "records": records,                 # 全量账本（供审计/合并，不参与统计）
    }

    write_json(C.REPORTS_DIR / "history.json", history)
    write_json(C.DOCS_REPORTS_DIR / "history.json", history)
    return history


def render_report(cfg: dict, recs, results_by_comp: dict | None = None,
                  demo: bool = False) -> dict:
    comp_map = {c.key: c for c in cfg["competitions"]}
    results_by_comp = results_by_comp or {}
    prematch_hours = float(cfg.get("analysis", {}).get("prematch_hours", 2))

    items = []
    for r in recs:
        c = comp_map[r.competition]
        item = {
            **asdict(r),
            "competition_name": c.name,
            "competition_class": c.cls,
        }
        item["prematch"] = _is_prematch(item.get("kickoff"), item.get("history"),
                                        prematch_hours)
        _settle_item(item, results_by_comp)
        items.append(item)

    order = {"strong": 0, "watch": 1, "pass": 2}
    items.sort(key=lambda x: (order[x["confidence"]], -x["score"]))

    # 结算统计：只统计强推+临场，并把 result 里的 outcome 拍平到顶层供统计读取
    settled = [{**i, **i["result"]}
               for i in items if i["result"].get("settled") and _is_counted(i)]
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope": "五大联赛 + 欧冠",
        "note": "杯赛与联赛数据分开计算",
        "total": len(items),
        "stats": {
            "overall": hit_rate_stats(settled),
            "by_market": group_hit_rates(settled, "market"),
            "by_competition": group_hit_rates(settled, "competition"),
        },
        "recommendations": items,
    }

    write_json(C.REPORTS_DIR / "latest.json", report)
    write_json(C.DOCS_REPORTS_DIR / "latest.json", report)   # 供 GitHub Pages 看板读取
    if not demo:
        # 演示模式不写历史账本，避免把 mock 数据混进真实命中率统计
        _update_history(items, results_by_comp)
    _write_markdown(report)
    return report


def _write_markdown(report: dict) -> None:
    stats = report["stats"]["overall"]
    rate = f"{stats['rate']}%" if stats["rate"] is not None else "—"
    lines = [
        "# 足球水位分析报告",
        "",
        f"- 生成时间：{report['generated_at']}（UTC）",
        f"- 覆盖范围：{report['scope']}；{report['note']}",
        f"- 推荐条数：{report['total']}",
        f"- 命中率：{rate}（已结算 {stats['settled']} 场，命中 {stats['hit']} 场）",
        "",
    ]
    if not report["recommendations"]:
        lines.append("暂无推荐（可能缺少临场水位数据）。")
    else:
        lines.append("| 联赛/杯赛 | 比赛 | 盘口 | 推荐 | 推荐系数 | 信心 | 赛果 |")
        lines.append("|---|---|---|---|---|---|---|")
        for r in report["recommendations"]:
            tag = "🏆杯赛" if r["competition_class"] == "cup" else "联赛"
            res = r.get("result") or {}
            if res.get("settled"):
                score = f"{res['home_goals']}-{res['away_goals']}"
                result_cell = f"{res['outcome_cn']}（{score}）"
            else:
                result_cell = "待赛果"
            lines.append(
                f"| {r['competition_name']}({tag}) | {r['home']} vs {r['away']} "
                f"| {_MARKET_CN[r['market']]} | {r['pick']} | {r['score']} "
                f"| {_CONF_CN[r['confidence']]} | {result_cell} |"
            )

        lines += ["", "## 推荐理由", ""]
        for r in report["recommendations"]:
            res = r.get("result") or {}
            res_txt = f" → {res['outcome_cn']} {res.get('home_goals')}-{res.get('away_goals')}" \
                if res.get("settled") else ""
            lines.append(f"### {r['home']} vs {r['away']} — {r['pick']}（{_CONF_CN[r['confidence']]} / {r['score']} 分）{res_txt}")
            for reason in r["reasons"]:
                lines.append(f"- {reason}")
            lines.append("")

    _write_text(C.REPORTS_DIR / "report.md", "\n".join(lines) + "\n")


def _write_text(path, text: str) -> None:
    from ..storage import ensure_dir
    ensure_dir(path.parent)
    path.write_text(text, encoding="utf-8")
