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
from ..models import market_cn
from ..storage import write_json, read_json
from ..analysis.settle import (settle_dict, hit_rate_stats, group_hit_rates,
                               OUTCOME_CN)

_CONF_CN = {"strong": "强推", "watch": "可关注", "pass": "观望"}
_HISTORY_FIELDS = ("fixture_key", "competition", "home", "away", "kickoff",
                   "market", "pick", "side", "line", "score", "confidence",
                   "prematch", "sport", "signals", "reasons", "reliability")


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


def _has_kicked_off(kickoff, now: datetime | None = None) -> bool:
    """开赛时间是否已过（用于「首推冻结」只锁定已开赛场次）。

    未开赛的场次要随临场水位/盘口变动实时刷新（可关注 -> 强推），
    开赛后（或已开赛）才冻结首推口径，避免赛后近况/收盘盘把「强推」重算成「可关注」。
    """
    ko = _parse_ts(kickoff)
    if not ko:
        return False
    now = now or datetime.now(timezone.utc)
    return ko < now


def _lead_hours(kickoff, history):
    """最新一条水位快照距开赛还有多少小时（负数=已开赛），无数据返回 None。"""
    ko = _parse_ts(kickoff)
    if not ko or not history:
        return None
    last = None
    for h in history or []:
        dt = _parse_ts(h.get("ts") if isinstance(h, dict) else None)
        if dt and (last is None or dt > last):
            last = dt
    if not last:
        return None
    return (ko - last).total_seconds() / 3600.0


def _is_prematch(kickoff, history, hours: float) -> bool:
    """最新一条水位快照是否落在开赛前 hours 小时内（临场）。"""
    lead = _lead_hours(kickoff, history)
    if lead is None:
        return False
    return 0 <= lead <= hours


def _is_counted(item: dict) -> bool:
    """命中率只统计「强推」场次。

    可关注(watch)与观望(pass)都不算推荐、不参与命中率统计；「临场(prematch)」
    只作展示标签，不作为统计口径——否则开赛前 2h 内是否恰好抓到快照（调度巧合）
    会决定一场推荐是否计入命中，且旧账本里 prematch 为 null 的记录会被静默漏掉。
    """
    return item.get("confidence") == "strong"


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

    # 合并本次推荐：同一场比赛只保留「首次推荐」的口径。
    # 未开赛的场次随临场水位/盘口刷新（可关注 -> 强推）；开赛后冻结，之后每次 analyze
    # 重算只用来补赛果结算，不再覆盖推荐本身——否则赛后会因近况/交手、收盘盘变化
    # 把「强推」重算成「可关注」。
    now_dt = datetime.now(timezone.utc)
    for item in items:
        rid = item.get("fixture_key")
        kicked_off = _has_kicked_off(item.get("kickoff"), now_dt)
        if rid in by_id and kicked_off:
            continue            # 已开赛：锁定首推口径，跳过本次重算（结算在下方统一补）
        by_id[rid] = {**_record(item), "id": rid}

    # 对账本里所有记录重结算（赛果陆续到位后自动补上）
    records = []
    now = datetime.now(timezone.utc).isoformat()
    for rid, rec in by_id.items():
        results = results_by_comp.get(rec.get("competition"), [])
        # 归一化临场标记：旧账本可能存 null，统一成布尔。临场仅作展示，不参与命中统计。
        if rec.get("prematch") is None:
            rec["prematch"] = False
        # 旧账本可能没有 sport 字段（后加的），按联赛回填：NBA=篮球，其余=足球。
        # 否则这些记录会落进 by_sport 的「?」桶，足球命中率被漏计。
        if not rec.get("sport"):
            rec["sport"] = "basketball" if rec.get("competition") == "nba" else "football"
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
        "total": len(counted),              # 累计推荐 = 强推（可关注不计入）
        "stats": {
            "overall": hit_rate_stats(counted),
            "by_market": group_hit_rates(counted, "market"),
            "by_competition": group_hit_rates(counted, "competition"),
            "by_sport": group_hit_rates(counted, "sport"),   # 足球/篮球分口径命中率
        },
        "records": records,                 # 全量账本（供审计/合并，不参与统计）
    }

    write_json(C.REPORTS_DIR / "history.json", history)
    write_json(C.DOCS_REPORTS_DIR / "history.json", history)
    return history


def _frozen_map() -> dict:
    """读取历史账本，返回 {fixture_key: 记录}，用于锁定「首推口径」。

    同一场比赛第二次及以后进 analyze 时，用账本里首次上架锁定的
    score/confidence/pick/signals 覆盖本次重算值，让看板与历史都保留
    你实际参考的那个推荐，只把赛果结算留给后续重算。
    """
    hist = read_json(C.REPORTS_DIR / "history.json", default={"records": []}) or {}
    out = {}
    for rec in hist.get("records", []):
        rid = rec.get("id") or rec.get("fixture_key")
        if rid:
            out[rid] = rec
    return out


def render_report(cfg: dict, recs, results_by_comp: dict | None = None,
                  demo: bool = False) -> dict:
    comp_map = {c.key: c for c in cfg["competitions"]}
    results_by_comp = results_by_comp or {}
    analysis_cfg = cfg.get("analysis", {}) or {}
    prematch_hours = float(analysis_cfg.get("prematch_hours", 2))
    publish_hours = float(analysis_cfg.get("publish_hours", 6))
    frozen_by_id = {} if demo else _frozen_map()
    now = datetime.now(timezone.utc)

    items = []
    for r in recs:
        c = comp_map[r.competition]
        item = {
            **asdict(r),
            "competition_name": c.name,
            "competition_class": c.cls,
            "sport": c.sport,
        }
        item["lead_hours"] = _lead_hours(item.get("kickoff"), item.get("history"))
        item["prematch"] = _is_prematch(item.get("kickoff"), item.get("history"),
                                        prematch_hours)
        # 已开赛的场次：锁定首推口径，本次只重算赛果结算（lead_hours 仍按最新快照算，
        # 用于决定是否还留在看板，不冻结）。
        # 未开赛的场次：不冻结，让「可关注」随临场水位升到「强推」上架。
        frozen = frozen_by_id.get(r.fixture_key)
        if frozen and _has_kicked_off(item.get("kickoff"), now):
            for k in _HISTORY_FIELDS:
                if k != "fixture_key" and k in frozen:
                    item[k] = frozen[k]
        _settle_item(item, results_by_comp)
        items.append(item)

    # 只在「开赛前 publish_hours 内 + 已开赛（进行中）」的场次出推荐；更早的场次不进看板/账本。
    # lead_hours <= publish_hours 允许负数（已开赛），让进行中的比赛继续留在看板上，
    # 避免 NBA 等跨时区赛事因开赛后水位仍在更新（lead_hours 变负）而从看板消失。
    # 演示模式（mock 数据）跳过此过滤，保证样例始终能跑通全流程。
    if not demo:
        items = [i for i in items
                 if i.get("lead_hours") is not None and i["lead_hours"] <= publish_hours]

    order = {"strong": 0, "watch": 1, "pass": 2}
    items.sort(key=lambda x: (order[x["confidence"]], -x["score"]))

    # 看板与命中率只保留「强推」；可关注/观望仅进历史账本备查，不占推荐位、不计命中率
    strong_items = [i for i in items if i["confidence"] == "strong"]

    # 结算统计：只统计强推，并把 result 里的 outcome 拍平到顶层供统计读取
    settled = [{**i, **i["result"]}
               for i in strong_items if i["result"].get("settled")]

    # 看板「推荐」标签只展示待进行/进行中的强推；已完场（已结算）的场次归历史账本，
    # 否则已完场旧比赛的 lead_hours 恒 <= publish_hours，会一直赖在推荐页。
    panel_items = [i for i in strong_items if not i["result"].get("settled")]
    n_league = sum(1 for c in cfg["competitions"] if c.cls == "league")
    n_cup = sum(1 for c in cfg["competitions"] if c.cls == "cup")
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope": f"{n_league} 个联赛 + {n_cup} 个杯赛",
        "note": "杯赛与联赛数据分开计算；仅开赛前 6h 内出推荐；命中率只统计强推",
        "total": len(panel_items),
        "stats": {
            "overall": hit_rate_stats(settled),
            "by_market": group_hit_rates(settled, "market"),
            "by_competition": group_hit_rates(settled, "competition"),
            "by_sport": group_hit_rates(settled, "sport"),
        },
        "recommendations": panel_items,
    }

    write_json(C.REPORTS_DIR / "latest.json", report)
    write_json(C.DOCS_REPORTS_DIR / "latest.json", report)   # 供 GitHub Pages 看板读取
    if not demo:
        # 演示模式不写历史账本，避免把 mock 数据混进真实命中率统计
        _update_history(items, results_by_comp)
    _write_markdown(report)
    return report


def build_schedule(cfg: dict) -> dict:
    """把「未开赛」的赛程汇总到 docs/reports/schedule.json 供看板提前展示。

    只展示赛程（球队/开赛时间），不含推荐——推荐仍在开赛前 publish_hours 内才出。
    """
    now = datetime.now(timezone.utc)
    matches = []
    for comp in cfg["competitions"]:
        raw = read_json(C.FIXTURES_DIR / f"{comp.key}.json", default=[]) or []
        for r in raw:
            ko = _parse_ts(r.get("kickoff"))
            if not ko or ko < now:
                continue
            matches.append({
                "fixture_key": r.get("key"),
                "competition": comp.key,
                "competition_name": comp.name,
                "competition_class": comp.cls,
                "sport": comp.sport,
                "home": r.get("home"),
                "away": r.get("away"),
                "kickoff": r.get("kickoff"),
            })
    matches.sort(key=lambda m: (m["kickoff"] or ""))
    doc = {"generated_at": now.isoformat(), "matches": matches}
    write_json(C.DOCS_REPORTS_DIR / "schedule.json", doc)
    write_json(C.REPORTS_DIR / "schedule.json", doc)
    return doc


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
                f"| {market_cn(r['market'], r.get('sport', 'football'))} | {r['pick']} | {r['score']} "
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
