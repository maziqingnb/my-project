"""7m 足球（data.7m.com.cn）全季赛果适配器（免 key）。

数据源：`https://data.7m.com.cn/matches_data/{league_id}/big/fixture.js`
（服务端返回 JS 数组，UTF-8），内嵌整季赛程+赛果，比 win007 移动版赛程页
（只回近 ~1 周）完整得多，用来补齐近况/交手的样本量。

数组结构（并行，按下标对齐）：
- `Time_Arr`   开赛时间 "YYYY,MM,DD,HH,MM,SS"（北京时间 UTC+8）
- `TeamA_Arr`/`TeamB_Arr`  主/客队名（繁体港译，如「阿仙奴」=Arsenal）
- `Scores_Arr` 比分 "3-0(2-0)"（全场(半场)）；未赛/取消为空串或 "-" 等
- `d_Team_Arr` 队码->[码,繁体名,简体名(不全)]

注意：
- 队名是繁体港译，需 TEAM_MAP_7M 映射到英文，才能与 win007 简体队名在 norm_team 层对齐。
- fixture.js 只有比分，没有亚盘/大小球历史盘口线；历史盘口仍由 win007 结果提供，
  同步层会把两者按 (主队,客队,日期) 合并（win007 的盘口线保留）。
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
    "Referer": "https://live.7m.com.cn/",
}

# 7m 联赛 id（与 win007 的 sclassid 完全不同，需单独维护）。只覆盖五大联赛，
# 其余联赛仍走 win007 赛程页（近 ~1 周）。
LEAGUE_IDS = {
    "epl": 92,
    "laliga": 85,
    "seriea": 34,
    "bundesliga": 39,
    "ligue1": 93,
}

# 7m 繁体港译队名 -> 英文规范名（与 config team_name_map 的英文值对齐）。
TEAM_MAP_7M = {
    # --- 英超 ---
    "阿士東維拉": "Aston Villa", "阿仙奴": "Arsenal", "愛華頓": "Everton",
    "白禮頓": "Brighton", "般尼茅夫": "Bournemouth", "賓福特": "Brentford",
    "車路士": "Chelsea", "富咸": "Fulham", "高雲地利": "Coventry City",
    "侯城": "Hull City", "利物浦": "Liverpool", "列斯聯": "Leeds United",
    "曼城": "Manchester City", "曼聯": "Manchester United", "紐卡素": "Newcastle United",
    "諾定咸森林": "Nottingham Forest", "熱刺": "Tottenham Hotspur",
    "水晶宮": "Crystal Palace", "新特蘭": "Sunderland", "葉士域治": "Ipswich Town",
    # --- 西甲 ---
    "艾爾切": "Elche", "艾拉維斯": "Alaves", "愛斯賓奴": "Espanyol",
    "奧沙辛拿": "Osasuna", "巴塞隆拿": "Barcelona", "貝迪斯": "Real Betis",
    "畢爾包": "Athletic Bilbao", "華歷簡奴": "Rayo Vallecano", "華倫西亞": "Valencia",
    "皇家馬德里": "Real Madrid", "皇家蘇斯達": "Real Sociedad", "加泰": "Getafe",
    "拉科魯尼亞": "Deportivo La Coruna", "利雲特": "Levante", "馬德里體育會": "Atletico Madrid",
    "馬拉加": "Malaga", "切爾達": "Celta Vigo", "桑坦德": "Racing Santander",
    "維拉利爾": "Villarreal", "西維爾": "Sevilla",
    # --- 意甲 ---
    "AC米蘭": "AC Milan", "阿特蘭大": "Atalanta", "博洛尼亞": "Bologna",
    "費倫天拿": "Fiorentina", "費辛隆尼": "Frosinone", "國際米蘭": "Inter Milan",
    "卡利亞里": "Cagliari", "科木": "Como", "拉素": "Lazio", "萊切": "Lecce",
    "羅馬": "Roma", "蒙沙": "Monza", "拿玻里": "Napoli", "帕爾馬": "Parma",
    "熱拿亞": "Genoa", "莎索羅": "Sassuolo", "拖連奴": "Torino", "威尼斯": "Venezia",
    "烏甸尼斯": "Udinese", "祖雲達斯": "Juventus",
    # --- 德甲 ---
    "RB萊比錫": "RB Leipzig", "艾華斯堡": "Elversberg", "奧格斯堡": "Augsburg",
    "柏德博恩": "Paderborn", "柏林聯": "Union Berlin", "拜仁慕尼黑": "Bayern Munich",
    "多蒙特": "Borussia Dortmund", "法蘭克福": "Eintracht Frankfurt", "費雷堡": "Freiburg",
    "漢堡": "Hamburger SV", "賀芬咸": "Hoffenheim", "科隆": "FC Koln",
    "利華古遜": "Bayer Leverkusen", "緬恩斯": "Mainz", "慕遜加柏": "Borussia Monchengladbach",
    "史浩克零四": "Schalke 04", "史特加": "Stuttgart", "雲達不萊梅": "Werder Bremen",
    # --- 法甲 ---
    "昂熱": "Angers", "巴黎FC": "Paris FC", "巴黎聖日門": "Paris Saint-Germain",
    "比斯特": "Brest", "朗斯": "Lens", "勒哈費爾": "Le Havre", "勒芒": "Le Mans",
    "雷恩": "Rennes", "里昂": "Lyon", "利爾": "Lille", "羅連安特": "Lorient",
    "馬賽": "Marseille", "摩納哥": "Monaco", "奈斯": "Nice", "歐塞爾": "Auxerre",
    "史特拉斯堡": "Strasbourg", "特魯瓦": "Troyes", "圖魯茲": "Toulouse",
}

_SCORE_RE = re.compile(r"(\d+)\s*-\s*(\d+)")


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


class Football7mClient(FixturesClient):
    name = "football-7m"

    def __init__(self, team_map: dict | None = None, league_ids: dict | None = None):
        self.team_map = team_map or TEAM_MAP_7M
        self.league_ids = league_ids or LEAGUE_IDS
        self._cache: dict[int, dict[str, list]] = {}

    # ------------------------------------------------------------------ 抓取
    def _fetch(self, url: str) -> str:
        if Path(url).exists():
            return Path(url).read_text(encoding="utf-8", errors="ignore")
        r = requests.get(url, headers=_HEADERS, timeout=30)
        r.raise_for_status()
        return r.content.decode("utf-8", "ignore")

    def _fixture_data(self, league_id: int) -> dict[str, list]:
        if league_id not in self._cache:
            txt = self._fetch(
                f"https://data.7m.com.cn/matches_data/{league_id}/big/fixture.js")
            self._cache[league_id] = self._parse(txt)
        return self._cache[league_id]

    @staticmethod
    def _arr(txt: str, name: str) -> list[str]:
        m = re.search(r"var\s+" + re.escape(name) + r"\s*=\s*\[(.*?)\];", txt, re.S)
        if not m:
            return []
        body = m.group(1)
        if '"' in body:
            return re.findall(r'"([^"]*)"', body)
        return [x.strip() for x in body.split(",") if x.strip() != ""]

    @staticmethod
    def _parse(txt: str) -> dict[str, list]:
        return {k: Football7mClient._arr(txt, k) for k in (
            "Time_Arr", "TeamA_Arr", "TeamB_Arr", "Scores_Arr")}

    def _map(self, name: str) -> str:
        return self.team_map.get(name, name)

    def _rows(self, comp: Competition) -> list[dict]:
        lid = self.league_ids.get(comp.key)
        if lid is None:
            return []
        d = self._fixture_data(lid)
        n = min(len(d["Time_Arr"]), len(d["TeamA_Arr"]),
                len(d["TeamB_Arr"]), len(d["Scores_Arr"]))
        rows = []
        for i in range(n):
            score = d["Scores_Arr"][i].strip()
            m = _SCORE_RE.search(score)
            if not m:                      # 未赛/推迟/取消（比分空或非数字）
                continue
            rows.append({
                "kickoff": _bj_to_utc(d["Time_Arr"][i]),
                "home": self._map(d["TeamA_Arr"][i]),
                "away": self._map(d["TeamB_Arr"][i]),
                "home_goals": int(m.group(1)),
                "away_goals": int(m.group(2)),
            })
        return rows

    # ------------------------------------------------------------ 结果/赛程
    def fetch_results(self, comp: Competition, days_back: int) -> list[Result]:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days_back)).strftime("%Y-%m-%d")
        seen: set[str] = set()
        results = []
        for r in self._rows(comp):
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
        """未来赛程：7m fixture.js 里比分尚为空、且开赛时间在未来的场次。"""
        now = datetime.now(timezone.utc)
        end = now + timedelta(days=days)
        seen: set[str] = set()
        fixtures = []
        for r in self._rows(comp):
            if not r["kickoff"]:
                continue
            try:
                ko = datetime.fromisoformat(r["kickoff"].replace("Z", "+00:00"))
            except (ValueError, TypeError):
                continue
            if ko.tzinfo is None:
                ko = ko.replace(tzinfo=timezone.utc)
            if not (now <= ko <= end):
                continue
            key = (norm_team(r["home"]), norm_team(r["away"]), r["kickoff"][:10])
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
        return []

    def fetch_lineup(self, comp: Competition, fixture: Fixture) -> Lineup | None:
        return None
