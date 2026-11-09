"""ESPN 公开比分 API 适配器（免费、无需 key）：补齐 win007/7m 覆盖不到的
杯赛 + 南美 + MLS + 国家队 的近况/交手样本。

数据源：`https://site.api.espn.com/apis/site/v2/sports/soccer/{slug}/scoreboard`
- `?dates=YYYYMMDD` 返回**单日**赛果（不支持日期范围，只能逐日抓）。
- 队名是英文（如 "Real Madrid"、"Fenerbahce"）。五大联赛球队英文名与 config
  team_name_map 的英文值对齐，norm_team 后能直接匹配 win007 的中文名；
  非五大球队（费内巴切、矿工、萨格勒布迪纳摩等）英文名与 win007 中文名对不上，
  需 ESPN_TEAM_MAP（英文→win007 简体）折到同一规范名。

范围（ESPN_LEAGUES）：欧冠/欧联/亚冠精英/巴甲/阿甲/MLS/国家队友谊赛——正是
7m 全季覆盖不到、win007 只回近 1 周的缺口。五大联赛等 12 个已有 7m 全季，
不在本适配器范围（fetch_results 直接返回空）。

注意：ESPN_TEAM_MAP 目前是「唯一比分匹配」自动推导 + 少量手工的静态表，
覆盖约 3-4 成杯赛球队（受 win007 近 1 周窗口限制）。未映射球队的英文名会与
win007 中文名失配，近况/交手对这类球队仍偏稀疏；可后续扩表或加动态推导。
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import requests

from ..models import Competition, Fixture, Result, Lineup
from .base import FixturesClient

_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

# 配置联赛 key -> ESPN soccer slug（只列 win007/7m 覆盖不到的缺口联赛）
ESPN_LEAGUES = {
    "ucl": "uefa.champions",
    "europa_league": "uefa.europa",
    "afc_cl": "afc.champions",      # 亚冠精英（ESPN 只区分顶级亚冠）
    "brasileirao": "bra.1",
    "argentina": "arg.1",
    "mls": "usa.1",
    "intl_friendly": "fifa.friendly",
}

# ESPN 英文名 -> win007 简体规范名（或 config 的英文规范名）。五大联赛球队的
# 英文名已与 config 对齐，无需在此列；这里只列「非五大」球队和英文写法差异。
# 由「唯一比分匹配」对 win007 近 1 周结果自动推导（全部人工核验过）。
ESPN_TEAM_MAP = {
    # --- 欧冠 / 欧联（欧洲非五大 + 英文写法差异） ---
    "AS Roma": "Roma",
    "Atlético Madrid": "Atletico Madrid",
    "Internazionale": "Inter Milan",
    "VfB Stuttgart": "Stuttgart",
    "Sporting CP": "葡萄牙体育",
    "AFC Bournemouth": "Bournemouth",
    "Stade Rennais": "Rennes",
    "Bodo/Glimt": "博德闪耀",
    "Fenerbahce": "费内巴切",
    "Feyenoord Rotterdam": "费耶诺德",
    "Galatasaray": "加拉塔萨雷",
    "LASK Linz": "LASK林茨",
    "Shakhtar Donetsk": "顿涅茨克矿工",
    "Slovan Bratislava": "布拉迪斯拉发",
    "Anderlecht": "安德莱赫特",
    "Jagiellonia Bialystok": "比亚韦斯托克雅盖隆",
    "Levski Sofia": "索非亚列夫斯基",
    "NK Celje": "采列",
    "Olympiacos": "奥林匹亚科斯",
    "RB Salzburg": "萨尔茨堡",
    "SK Sturm Graz": "格拉茨风暴",
    "Sparta Prague": "布拉格斯巴达",
    # --- 亚冠精英 ---
    "Al Ahli": "吉达国民",
    "Al Ain": "艾因",
    "Al Ittihad": "吉达联合",
    "Al Nassr": "利雅得胜利",
    "Al Qadsiah": "卡达西亚",
    "Al Sadd": "萨德",
    "Al Wasl": "迪拜青年国民",
    "Al-Gharafa": "加拉法",
    "Al-Quwa Al-Jawiya": "巴格达空军",
    "Al-Shamal": "艾沙姆",
    "Beijing Guoan": "北京国安",
    "Buriram United": "武里南联",
    "Cong An Ha Noi": "河内公安",
    "Esteghlal FC": "德黑兰独立",
    "Gamba Osaka": "大阪钢巴",
    "Kashima Antlers": "鹿岛鹿角",
    "Kyoto Sanga": "京都不死鸟",
    "Neftchi Fergana": "费尔干纳石油工人",
    "Newcastle Jets": "纽卡斯尔喷气机",
    "Pakhtakor": "塔什干棉农",
    "Pohang Steelers": "浦项制铁",
    "Port FC": "狮子港",
    "Ratchaburi FC": "拉查布里府",
    "Shanghai Port FC": "上海海港",
    "Tractor": "大不里士拖拉机",
    "Vissel Kobe": "神户胜利船",
    # --- 巴甲 ---
    "Athletico-PR": "巴拉纳竞技",
    "Atlético-MG": "米内罗竞技",
    "Bahia": "巴伊亚",
    "Botafogo": "博塔弗戈",
    "Chapecoense": "沙佩科恩斯",
    "Corinthians": "科林蒂安",
    "Coritiba": "科里蒂巴",
    "Cruzeiro": "克鲁塞罗",
    "Flamengo": "弗拉门戈",
    "Fluminense": "弗鲁米嫩塞",
    "Grêmio": "格雷米奥",
    "Internacional": "巴西国际",
    "Mirassol": "米拉索",
    "Palmeiras": "帕尔梅拉斯",
    "Red Bull Bragantino": "布拉干蒂诺RB",
    "Remo": "瑞模贝雷",
    "Santos": "桑托斯",
    "São Paulo": "圣保罗",
    "Vasco da Gama": "瓦斯科达伽马",
    "Vitória": "维多利亚",
    # --- 阿甲 ---
    "Aldosivi": "阿尔多斯维",
    "Argentinos Juniors": "阿根廷青年人",
    "Atlético Tucumán": "图库曼竞技",
    "Banfield": "班菲尔德",
    "Barracas Central": "巴拉卡斯中央队",
    "Belgrano (Córdoba)": "贝尔格拉诺",
    "Boca Juniors": "博卡青年",
    "Central Córdoba (Santiago del Estero)": "科尔多瓦中央SDE",
    "Defensa y Justicia": "防卫者",
    "Deportivo Riestra": "利斯特雷",
    "Estudiantes de Río Cuarto": "里奥夸尔托学生队",
    "Gimnasia La Plata": "拉普拉塔体操",
    "Huracán": "飓风队",
    "Independiente": "阿根廷独立",
    "Independiente Rivadavia": "门多萨独立",
    "Instituto (Córdoba)": "科尔多瓦学院",
    "Lanús": "拉努斯",
    "Newell's Old Boys": "纽维尔老男孩",
    "Platense": "普拉腾斯",
    "River Plate": "河床",
    "Rosario Central": "罗萨里奥中央",
    "San Lorenzo": "圣洛伦索",
    # --- MLS ---
    "Atlanta United FC": "亚特兰大联",
    "Austin FC": "奥斯汀FC",
    "Charlotte FC": "夏洛特FC",
    "Chicago Fire FC": "芝加哥火焰",
    "Columbus Crew": "哥伦布机员",
    "D.C. United": "华盛顿联",
    "Houston Dynamo FC": "休斯敦迪纳摩",
    "Inter Miami CF": "迈阿密国际",
    "LAFC": "洛杉矶FC",
    "Minnesota United FC": "明尼苏达联",
    "Nashville SC": "纳什威尔SC",
    "New England Revolution": "新英格兰革命",
    "New York City FC": "纽约城",
    "Orlando City SC": "奥兰多城",
    "Philadelphia Union": "费城联合",
    "Portland Timbers": "波特兰伐木者",
    "Red Bull New York": "纽约红牛",
    "Seattle Sounders FC": "西雅图海湾人",
    "Toronto FC": "多伦多FC",
    "Vancouver Whitecaps": "温哥华白浪",
}

_FINISHED = {"STATUS_FULL_TIME", "STATUS_FINAL", "STATUS_POSTPONED"}


class EspnClient(FixturesClient):
    name = "espn"

    def __init__(self, team_map: dict | None = None, leagues: dict | None = None):
        self.team_map = team_map or ESPN_TEAM_MAP
        self.leagues = leagues or ESPN_LEAGUES

    def _scoreboard(self, slug: str, day: str) -> list[dict]:
        url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/{slug}/scoreboard?dates={day}"
        try:
            r = requests.get(url, headers=_HEADERS, timeout=30)
            if r.status_code != 200:
                return []
            return r.json().get("events", []) or []
        except requests.RequestException:
            return []

    def _map(self, name: str) -> str:
        return self.team_map.get(name, name)

    def fetch_results(self, comp: Competition, days_back: int) -> list[Result]:
        slug = self.leagues.get(comp.key)
        if not slug:
            return []
        days = [(datetime.now(timezone.utc) - timedelta(days=i)).strftime("%Y%m%d")
                for i in range(max(0, days_back))]
        events: list[dict] = []
        # ESPN 只支持单日查询，逐日并发抓（有比赛的日期才有事件，空日期返回空列表）
        with ThreadPoolExecutor(max_workers=6) as ex:
            for evs in ex.map(lambda d: self._scoreboard(slug, d), days):
                events.extend(evs)

        results: list[Result] = []
        seen: set[str] = set()
        for e in events:
            c = (e.get("competitions") or [{}])[0]
            if (c.get("status", {}).get("type", {}).get("name") or "") not in _FINISHED:
                continue
            home = away = None
            hg = ag = None
            for t in c.get("competitors", []):
                score = t.get("score")
                if score is None or score == "":
                    continue
                name = t.get("team", {}).get("displayName") or ""
                if t.get("homeAway") == "home":
                    home, hg = self._map(name), int(float(score))
                else:
                    away, ag = self._map(name), int(float(score))
            if not home or not away or hg is None or ag is None:
                continue
            day = (e.get("date") or "")[:10]
            key = (home, away, day)
            if key in seen:
                continue
            seen.add(key)
            results.append(Result(competition=comp.key, cls=comp.cls,
                                  home=home, away=away, home_goals=hg, away_goals=ag,
                                  date=day))
        results.sort(key=lambda r: r.date, reverse=True)
        return results

    def fetch_upcoming(self, comp: Competition, days: int) -> list[Fixture]:
        return []

    def fetch_live(self, comp: Competition) -> list[Fixture]:
        return []

    def fetch_lineup(self, comp: Competition, fixture: Fixture) -> Lineup | None:
        return None
