"""球探网移动版 m.ewin007.com 统一客户端（赔率 + 赛程 + 结果），已实测可用。

数据来源（均为服务端渲染，普通 HTTP GET 即可）：
1) 赛程/比分：https://m.ewin007.com/Schedule.htm?date=YYYY-MM-DD
   内嵌 `sclassDataStr`（联赛表）与 `scheduleDataStr`（比赛表，`!`分场、`^`分字段）。
   字段： [1]sclassid [3]matchTime(YYYYMMDDHHMMSS) [5]主队 [6]客队 [7]主分 [8]客分
          [23]主水 [24]让球盘口 [25]客水 [27]大小球盘口
2) 赔率详情（大小球水位 + 初盘）：https://m.ewin007.com/Analy/Analysis/{scheduleid}.htm
   内嵌 `var freshJsonData = {...}`，其中 sbOdds.sbOdds[] 是各盘口条目：
   kind=ASIAN(亚盘)/OU(大小球)/EURO(欧赔)，companyId=3 为皇冠(Crown)。
   每条含 firstHomeOdds/firstDrawOdds/firstAwayOdds(初盘) + homeOdds/drawOdds/awayOdds(即时)。

已知 win007 联赛 ID：英超36 西甲31 德甲8 意甲34 法甲11（欧冠待欧冠比赛日确认）。

限制（如实说明）：
  - 阵容需分析页另一部分数据，未接入（fetch_lineup 返回 None）；
  - 队名是中文，需 team_name_map 映射到英文，否则近况/交手匹配不上；
  - 让球盘口方向若反了，改 config 的 handicap_sign。
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from ..models import Competition, Fixture, Result, Lineup, OddsSnapshot, fixture_key
from .base import OddsClient, FixturesClient

_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
                   "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1"),
    "Referer": "https://m.ewin007.com/",
}


def _num(s):
    if s is None:
        return None
    s = s.strip()
    if s in ("", "-", "--", "无", "封"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _int(s):
    if s is None or s.strip() == "":
        return None
    try:
        return int(s.strip())
    except ValueError:
        return None


def _iso(match_time: str) -> str:
    """'20261031103000' -> '2026-10-31T10:30:00'"""
    t = (match_time or "").strip()
    if len(t) >= 12:
        return f"{t[:4]}-{t[4:6]}-{t[6:8]}T{t[8:10]}:{t[10:12]}:00"
    return t


def _dt(iso: str):
    """解析为带 UTC 时区的 datetime，失败返回 None。"""
    try:
        return datetime.fromisoformat(iso).replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


class Win007Client(OddsClient, FixturesClient):
    name = "win007"

    def __init__(self, base_url: str, analysis_url: str, league_map: dict,
                 team_name_map: dict | None = None, handicap_sign: int = -1,
                 days: int = 1, results_days: int = 30):
        self.base_url = base_url
        self.analysis_url = analysis_url
        self.league_map = league_map
        self.team_name_map = team_name_map or {}
        self.handicap_sign = handicap_sign
        self.days = days
        self.results_days = results_days
        self._schedule_cache: dict[str, list[dict]] = {}
        self._analysis_cache: dict[str, dict | None] = {}

    # ------------------------------------------------------------------ 抓取
    def _fetch(self, url: str) -> str:
        if Path(url).exists():
            return Path(url).read_text(encoding="utf-8", errors="ignore")
        r = requests.get(url, headers=_HEADERS, timeout=30)
        r.raise_for_status()
        r.encoding = r.apparent_encoding or "utf-8"
        return r.text

    def _schedule(self, day: str) -> list[dict]:
        if day in self._schedule_cache:
            return self._schedule_cache[day]
        html = self._fetch(self.base_url.replace("{date}", day))
        matches = self._parse_schedule(html)
        self._schedule_cache[day] = matches
        return matches

    def _parse_schedule(self, html: str) -> list[dict]:
        m = re.search(r'var scheduleDataStr = "(.*?)";', html, re.S)
        if not m:
            return []
        out: list[dict] = []
        for match in m.group(1).split("!"):
            p = match.split("^")
            if len(p) < 28:
                continue
            comp_key = self.league_map.get(p[1])
            if not comp_key:
                continue
            home = self._map(p[5])
            away = self._map(p[6])
            if not home or not away:
                continue
            handicap = _num(p[24])
            out.append({
                "scheduleid": p[0], "comp_key": comp_key,
                "home": home, "away": away, "kickoff": _iso(p[3]),
                "home_goals": _int(p[7]), "away_goals": _int(p[8]),
                "handicap_line": (self.handicap_sign * handicap) if handicap is not None else None,
                "handicap_home": _num(p[23]), "handicap_away": _num(p[25]),
                "total_line": _num(p[27]),
            })
        return out

    def _analysis(self, scheduleid: str) -> dict | None:
        if scheduleid in self._analysis_cache:
            return self._analysis_cache[scheduleid]
        try:
            html = self._fetch(self.analysis_url.replace("{id}", scheduleid))
            odds = self._parse_analysis(html)
        except Exception:
            odds = None
        self._analysis_cache[scheduleid] = odds
        return odds

    def _parse_analysis(self, html: str) -> dict | None:
        m = re.search(r"var\s+freshJsonData\s*=\s*", html)
        if not m:
            return None
        start = html.find("{", m.end())
        if start < 0:
            return None
        depth, end = 0, start
        for end in range(start, len(html)):
            if html[end] == "{":
                depth += 1
            elif html[end] == "}":
                depth -= 1
                if depth == 0:
                    break
        try:
            data = json.loads(html[start:end + 1])
        except Exception:
            return None

        result: dict = {}
        for e in (data.get("sbOdds") or {}).get("sbOdds") or []:
            odds = e.get("odds") or {}
            if odds.get("companyId") != 3:      # 3 = 皇冠(Crown)
                continue
            kind = e.get("kind")
            if kind == "ASIAN":                  # 亚盘：home=主水 draw=盘口 away=客水
                result["asian"] = {
                    "first_line": odds.get("firstDrawOdds"), "first_home": odds.get("firstHomeOdds"),
                    "first_away": odds.get("firstAwayOdds"),
                    "line": odds.get("drawOdds"), "home": odds.get("homeOdds"), "away": odds.get("awayOdds"),
                }
            elif kind == "OU":                   # 大小球：home=大水 draw=盘口 away=小水
                result["ou"] = {
                    "first_line": odds.get("firstDrawOdds"), "first_over": odds.get("firstHomeOdds"),
                    "first_under": odds.get("firstAwayOdds"),
                    "line": odds.get("drawOdds"), "over": odds.get("homeOdds"), "under": odds.get("awayOdds"),
                }
        return result or None

    # ---------------------------------------------------------------- 赔率
    def fetch_odds(self, comp: Competition) -> list[OddsSnapshot]:
        snaps: list[OddsSnapshot] = []
        now = datetime.now(timezone.utc)
        for m in self._matches(days=self.days):
            if m["comp_key"] != comp.key:
                continue
            ko = _dt(m["kickoff"])
            if not ko or ko < now:                # 只取未开赛场次
                continue
            ana = self._analysis(m["scheduleid"])
            snaps.extend(self._snapshots(comp, m, ana))
        return snaps

    def _snapshots(self, comp: Competition, m: dict, ana: dict | None) -> list[OddsSnapshot]:
        now = datetime.now(timezone.utc)
        first_ts = ((_dt(m["kickoff"]) or now) - timedelta(days=1)).isoformat()

        if not ana or ("asian" not in ana and "ou" not in ana):
            # 兜底：仅赛程页的即时亚盘 + 大小球盘口（无大小球水位）
            return [OddsSnapshot(
                fixture_key=fixture_key(comp.key, m["home"], m["away"], m["kickoff"]),
                competition=comp.key, home=m["home"], away=m["away"], kickoff=m["kickoff"],
                bookmaker="crown", ts=now.isoformat(),
                handicap_line=m["handicap_line"], handicap_home=m["handicap_home"],
                handicap_away=m["handicap_away"], total_line=m["total_line"],
            )]

        a = ana.get("asian")
        o = ana.get("ou")
        fk = fixture_key(comp.key, m["home"], m["away"], m["kickoff"])
        base = dict(fixture_key=fk, competition=comp.key, home=m["home"], away=m["away"],
                    kickoff=m["kickoff"], bookmaker="crown")

        def snap(ts, a_line, a_home, a_away, o_line, o_over, o_under):
            return OddsSnapshot(
                ts=ts,
                handicap_line=(self.handicap_sign * a_line) if a_line is not None else m["handicap_line"],
                handicap_home=a_home if a_home is not None else m["handicap_home"],
                handicap_away=a_away if a_away is not None else m["handicap_away"],
                total_line=o_line if o_line is not None else m["total_line"],
                over=o_over, under=o_under, **base,
            )

        out = [snap(first_ts,
                    a["first_line"] if a else None, a["first_home"] if a else None, a["first_away"] if a else None,
                    o["first_line"] if o else None, o["first_over"] if o else None, o["first_under"] if o else None),
               snap(now.isoformat(),
                    a["line"] if a else None, a["home"] if a else None, a["away"] if a else None,
                    o["line"] if o else None, o["over"] if o else None, o["under"] if o else None)]
        return out

    # ------------------------------------------------------------ 赛程/结果
    def _matches(self, days: int) -> list[dict]:
        out: list[dict] = []
        today = datetime.now(timezone.utc)
        for d in range(days):
            day = (today + timedelta(days=d)).strftime("%Y-%m-%d")
            out.extend(self._schedule(day))
        return out

    def fetch_upcoming(self, comp: Competition, days: int) -> list[Fixture]:
        now = datetime.now(timezone.utc)
        fixtures = []
        for m in self._matches(days):
            if m["comp_key"] != comp.key:
                continue
            ko = _dt(m["kickoff"])
            if ko and ko < now:
                continue
            fixtures.append(Fixture(
                key=fixture_key(comp.key, m["home"], m["away"], m["kickoff"]),
                competition=comp.key, home=m["home"], away=m["away"],
                kickoff=m["kickoff"], status="upcoming",
            ))
        return fixtures

    def fetch_results(self, comp: Competition, days_back: int) -> list[Result]:
        today = datetime.now(timezone.utc)
        seen: set[str] = set()
        results = []
        for d in range(days_back, 0, -1):        # 从最久到最近
            day = (today - timedelta(days=d)).strftime("%Y-%m-%d")
            for m in self._schedule(day):
                if m["comp_key"] != comp.key:
                    continue
                if m["home_goals"] is None or m["away_goals"] is None:
                    continue
                if m["scheduleid"] in seen:
                    continue
                seen.add(m["scheduleid"])
                results.append(Result(
                    competition=comp.key, cls=comp.cls,
                    home=m["home"], away=m["away"],
                    home_goals=m["home_goals"], away_goals=m["away_goals"],
                    date=m["kickoff"][:10],
                ))
        return results

    def fetch_lineup(self, comp: Competition, fixture: Fixture) -> Lineup | None:
        return None          # 阵容在分析页另一部分，暂未接入

    def _map(self, name: str) -> str:
        return self.team_name_map.get(name, name)
