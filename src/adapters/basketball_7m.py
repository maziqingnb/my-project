"""球探网篮球（bdata.7m.com.cn）赛程/结果适配器（NBA，免 key）。

数据源：`https://bdata.7m.com.cn/basketball_match_data/{basketball_id}/big/match_list.js`
（服务端返回 JS 数组，UTF-8），内嵌整季赛程与每节比分：
- `Start_time_arr` 开赛时间（"YYYY,MM,DD,HH,MM,SS"，北京时间 UTC+8）
- `TeamA_arr` / `TeamB_arr` 主/客队（繁体中文）
- `TeamA_score1_arr`~`score4_arr` + `TeamA_score_out_arr` 每节/加时得分
- `State_arr` 比赛状态：9=完场，11=加时完场（其余按未开赛/进行中处理）

队名通过 `team_name_map` 映射到 the-odds-api 英文名，保证与赔率侧对齐。
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from ..models import Competition, Fixture, Result, Lineup, fixture_key, norm_team
from .base import FixturesClient

_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
                   "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1"),
    "Referer": "https://bdata.7m.com.cn/",
}

_FINISHED_STATES = {"9", "11"}   # 9=完场 11=加时完场

# 7m 篮球 State_arr 状态码（已实测）：0=未开赛 9=完场 11=加时完场；5=延期/推迟
# （比分全 0 且开赛时间早已过去，例如波特兰vs雷霆 12:00 场延期后仍挂 state=5）。
# 「非 0/9/11 就当进行中」的旧逻辑会把延期的场次永远挂在「进行中」板块，故明确排除。
_NOT_LIVE_STATES = {"", "0", "5", "9", "11"}

# 进行中的比赛最多只能开赛这么久（NBA 单场最长 ~3.5 小时，留 4 小时余量）。叠加
# 时间窗判断，把「开赛时间早已过去但状态没更新（延期/腰斩）」的场次挡掉，避免
# 早上的比赛到晚上还赖在「实时」板块。
_LIVE_WINDOW_HOURS = 4


def _bj_to_utc(s: str) -> str:
    """'YYYY,MM,DD,HH,MM,SS'（北京时间 UTC+8）-> UTC ISO 字符串。"""
    parts = [int(x) for x in (s or "").split(",") if x.strip().lstrip("-").isdigit()]
    if len(parts) < 6:
        return ""
    try:
        dt = datetime(parts[0], parts[1], parts[2], parts[3], parts[4], parts[5],
                      tzinfo=timezone(timedelta(hours=8)))
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    except (ValueError, TypeError):
        return ""


class Basketball7mClient(FixturesClient):
    name = "basketball-7m"

    def __init__(self, url: str, team_name_map: dict | None = None):
        self.url = url
        self.team_name_map = team_name_map or {}
        self._cache: dict[str, list] | None = None

    # ------------------------------------------------------------------ 抓取
    def _fetch(self, url: str) -> str:
        if Path(url).exists():
            return Path(url).read_text(encoding="utf-8", errors="ignore")
        r = requests.get(url, headers=_HEADERS, timeout=30)
        r.raise_for_status()
        return r.content.decode("utf-8", "ignore")

    def _data(self) -> dict[str, list]:
        if self._cache is None:
            self._cache = self._parse(self._fetch(self.url))
        return self._cache

    @staticmethod
    def _arr(txt: str, name: str) -> list[str]:
        m = re.search(r"var\s+" + re.escape(name) + r"\s*=\s*\[(.*?)\];", txt, re.S)
        if not m:
            return []
        body = m.group(1)
        # 带引号的数组（队名 / 开赛时间 "YYYY,MM,DD,HH,MM,SS" 内嵌逗号）按引号提取；
        # 纯数字数组（比分 / 状态 / 编号）按逗号切分。
        if '"' in body:
            return re.findall(r'"([^"]*)"', body)
        return [x.strip() for x in body.split(",") if x.strip() != ""]

    def _parse(self, txt: str) -> dict[str, list]:
        return {k: self._arr(txt, k) for k in (
            "bh_arr", "Start_time_arr", "TeamA_arr", "TeamB_arr",
            "TeamA_score1_arr", "TeamA_score2_arr", "TeamA_score3_arr",
            "TeamA_score4_arr", "TeamA_score_out_arr",
            "TeamB_score1_arr", "TeamB_score2_arr", "TeamB_score3_arr",
            "TeamB_score4_arr", "TeamB_score_out_arr", "State_arr",
        )}

    def _map(self, name: str) -> str:
        return self.team_name_map.get(name, name)

    def _rows(self) -> list[dict]:
        d = self._data()
        n = len(d.get("bh_arr", []))
        rows = []
        for i in range(n):
            def g(key):
                arr = d.get(key, [])
                return arr[i] if i < len(arr) else ""
            state = g("State_arr")
            iso = _bj_to_utc(g("Start_time_arr"))
            def score(*keys):
                return sum(int(g(k)) for k in keys if g(k).lstrip("-").isdigit())
            rows.append({
                "state": state,
                "kickoff": iso,
                "home": self._map(g("TeamA_arr")),
                "away": self._map(g("TeamB_arr")),
                "home_goals": score("TeamA_score1_arr", "TeamA_score2_arr",
                                    "TeamA_score3_arr", "TeamA_score4_arr",
                                    "TeamA_score_out_arr"),
                "away_goals": score("TeamB_score1_arr", "TeamB_score2_arr",
                                    "TeamB_score3_arr", "TeamB_score4_arr",
                                    "TeamB_score_out_arr"),
            })
        return rows

    # ------------------------------------------------------------ 结果/赛程
    def fetch_results(self, comp: Competition, days_back: int) -> list[Result]:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days_back)).strftime("%Y-%m-%d")
        seen: set[str] = set()
        results = []
        for r in self._rows():
            if r["state"] not in _FINISHED_STATES:
                continue
            day = (r["kickoff"] or "")[:10]
            if not day or day < cutoff:
                continue
            key = (norm_team(r["home"]), norm_team(r["away"]), day)
            if key in seen:
                continue
            seen.add(key)
            results.append(Result(
                competition=comp.key, cls=comp.cls,
                home=r["home"], away=r["away"],
                home_goals=r["home_goals"], away_goals=r["away_goals"],
                date=day,
            ))
        results.sort(key=lambda r: r.date, reverse=True)
        return results

    def fetch_upcoming(self, comp: Competition, days: int) -> list[Fixture]:
        """NBA 未来赛程：从球探网篮球整季赛程里挑 State=0（未开赛）且在未来 N 天内的场次。"""
        now = datetime.now(timezone.utc)
        end = now + timedelta(days=days)
        seen: set[str] = set()
        fixtures = []
        for r in self._rows():
            if r["state"] != "0":          # 0=未开赛
                continue
            try:
                ko = datetime.fromisoformat((r["kickoff"] or "").replace("Z", "+00:00"))
            except (ValueError, TypeError):
                continue
            if ko.tzinfo is None:
                ko = ko.replace(tzinfo=timezone.utc)
            if not (now <= ko <= end):
                continue
            key = (norm_team(r["home"]), norm_team(r["away"]), (r["kickoff"] or "")[:10])
            if key in seen:
                continue
            seen.add(key)
            fixtures.append(Fixture(
                key=fixture_key(comp.key, r["home"], r["away"], r["kickoff"]),
                competition=comp.key, home=r["home"], away=r["away"],
                kickoff=r["kickoff"], status="upcoming",
            ))
        fixtures.sort(key=lambda f: f.kickoff)
        return fixtures

    def fetch_live(self, comp: Competition) -> list[Fixture]:
        """进行中的 NBA 比赛：状态为「真正进行中」且开赛时间在合理窗口内。

        旧逻辑「非 0/9/11 就当进行中」会把延期（state=5）的场次误判成进行中；
        现在先排除延期/完场/未开赛状态，再用「开赛已到且距开赛不超过 4 小时」兜底。
        """
        now = datetime.now(timezone.utc)
        fixtures = []
        for r in self._rows():
            if r["state"] in _NOT_LIVE_STATES:
                continue
            if not r["kickoff"]:
                continue
            try:
                ko = datetime.fromisoformat(r["kickoff"].replace("Z", "+00:00"))
            except (ValueError, TypeError):
                continue
            if ko.tzinfo is None:
                ko = ko.replace(tzinfo=timezone.utc)
            if ko > now:                              # 还没开赛
                continue
            if (now - ko) > timedelta(hours=_LIVE_WINDOW_HOURS):  # 早已结束/延期未更新
                continue
            fixtures.append(Fixture(
                key=fixture_key(comp.key, r["home"], r["away"], r["kickoff"]),
                competition=comp.key, home=r["home"], away=r["away"],
                kickoff=r["kickoff"], status="live",
                home_goals=r["home_goals"], away_goals=r["away_goals"],
            ))
        return fixtures

    def fetch_lineup(self, comp: Competition, fixture: Fixture) -> Lineup | None:
        return None
