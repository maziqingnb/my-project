"""主动分析：按球队汇总「当天 + 近期」比赛，并对每场给出亚盘 + 大小球双视角分析。

与推荐页（docs/reports/latest.json）的区别：
- 推荐页只在开赛前 publish_hours 内上架「强推」，且亚盘/大小球二选一；
- 这里对每支球队当天/近期的每一场都做分析（含「可关注/观望」），并把亚盘、
  大小球两个方向都摆出来，方便用户输入一支球队就能看到它今天怎么打、盘口怎么走。

输出 docs/reports/team_analysis.json，由 GitHub Pages 看板的「主动分析」页读取。
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta, timezone

from .. import config as C
from ..models import norm_team
from ..storage import read_json, write_json
from .recommender import recommend_handicap, recommend_totals

_BJ = timezone(timedelta(hours=8))
_LOOKAHEAD_DAYS = 7   # 只分析未来 N 天内的比赛（太远的既没盘口也没意义）
_BACKFILL_DAYS = 2    # 无盘口数据时，仍从赛程里补「今明两天」的占位，提示当天有球但暂无盘口


def _parse_ts(s):
    if not s:
        return None
    s = s.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _bj_date(dt: datetime) -> str:
    """比赛开赛时间对应的北京时间日期（用于「今天/明天」分组）。"""
    return dt.astimezone(_BJ).strftime("%Y-%m-%d")


def _rec_dict(rec) -> dict | None:
    """Recommendation -> dict；None 表示该盘口无数据/无信号。"""
    return asdict(rec) if rec is not None else None


def _reverse_aliases(cfg: dict) -> dict:
    """team_name_map 反查：英文规范名(归一化) -> 所有中文别名。

    球探网 snapshots/fixtures 里的队名已经过 _map 折成英文，但用户输入时
    往往打中文（阿森纳 / Arsenal 都可能），把中文别名挂到每支球队上，
    前端就能按中文或英文都搜到。
    """
    rev: dict[str, set] = {}
    for cn, en in (cfg.get("team_name_map") or {}).items():
        key = norm_team(en)
        if key:
            rev.setdefault(key, set()).add(cn)
    return rev


def _in_window(ko: datetime | None, now: datetime) -> bool:
    if not ko:
        return False
    # 开赛前 1 小时到未来 N 天：覆盖「今天剩余 + 未来一周」，已完场不参与赛前分析
    return now - timedelta(hours=1) <= ko <= now + timedelta(days=_LOOKAHEAD_DAYS)


def build_team_analysis(cfg: dict, results_by_comp: dict, odds_by_comp: dict,
                        now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    aliases = _reverse_aliases(cfg)

    # 先按「球队(归一化 key)」聚合并去重；每场同时归入主客两支球队。
    teams: dict[str, dict] = {}

    def _bucket(name: str) -> dict:
        key = norm_team(name)
        if not key:
            return None
        b = teams.get(key)
        if b is None:
            names = [name]
            for a in sorted(aliases.get(key, ())):
                if a not in names:
                    names.append(a)
            b = {"key": key, "names": names, "matches": []}
            teams[key] = b
        return b

    def _add(comp, home, away, kickoff, fixture_key, handicap_rec, totals_rec):
        ko = _parse_ts(kickoff)
        if not _in_window(ko, now):
            return
        match = {
            "fixture_key": fixture_key,
            "competition": comp.key,
            "competition_name": comp.name,
            "competition_class": comp.cls,
            "sport": getattr(comp, "sport", "football"),
            "home": home,
            "away": away,
            "kickoff": kickoff,
            "date": _bj_date(ko),
            "handicap": _rec_dict(handicap_rec),
            "totals": _rec_dict(totals_rec),
        }
        for name in (home, away):
            b = _bucket(name)
            if b is None:
                continue
            b["matches"].append(match)

    for comp in cfg["competitions"]:
        results = results_by_comp.get(comp.key, [])
        snaps = odds_by_comp.get(comp.key, [])
        by_fixture: dict[str, list] = {}
        for s in snaps:
            by_fixture.setdefault(s.fixture_key, []).append(s)

        for fk, snap_list in by_fixture.items():
            snap_list = sorted(snap_list, key=lambda s: s.ts)
            h = recommend_handicap(comp, cfg, results, snap_list)
            t = recommend_totals(comp, cfg, results, snap_list)
            _add(comp, snap_list[-1].home, snap_list[-1].away,
                 snap_list[-1].kickoff, fk, h, t)

        # 今天/明天有比赛但还没盘口快照的：从赛程补占位，让用户知道当天有球、只是暂无盘口
        for r in read_json(C.FIXTURES_DIR / f"{comp.key}.json", default=[]) or []:
            ko = _parse_ts(r.get("kickoff"))
            if not ko or not (now - timedelta(hours=1) <= ko <= now + timedelta(days=_BACKFILL_DAYS)):
                continue
            fk = r.get("key") or r.get("fixture_key")
            if fk in by_fixture:
                continue
            _add(comp, r.get("home"), r.get("away"), r.get("kickoff"), fk, None, None)

    # 收尾：每支球队的比赛按开赛时间排序
    for b in teams.values():
        b["matches"].sort(key=lambda m: (m["kickoff"] or ""))

    team_list = sorted(teams.values(), key=lambda b: b["key"])
    doc = {
        "generated_at": now.isoformat(),
        "date_today": _bj_date(now),
        "teams": team_list,
    }
    write_json(C.REPORTS_DIR / "team_analysis.json", doc)
    write_json(C.DOCS_REPORTS_DIR / "team_analysis.json", doc)   # 供 GitHub Pages 看板读取
    return doc
