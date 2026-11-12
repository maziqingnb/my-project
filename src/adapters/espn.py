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

from ..models import Competition, Fixture, Result, Lineup, fixture_key
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
    # --- 国家队友谊赛（ESPN 英文 -> win007 简体；让 ESPN 全季结果与 win007 中文对齐，
    #     否则国家队近况/交手只能靠 win007 近 1 周、样本太稀疏） ---
    "Algeria": "阿尔及利亚",
    "Andorra": "安道尔",
    "Anguilla": "安圭拉",
    "Argentina": "阿根廷",
    "Australia": "澳大利亚",
    "Azerbaijan": "阿塞拜疆",
    "Bahrain": "巴林",
    "Benin": "贝宁",
    "Bolivia": "玻利维亚",
    "Botswana": "博茨瓦纳",
    "Brazil": "巴西",
    "Burkina Faso": "布基纳法索",
    "Cameroon": "喀麦隆",
    "Canada": "加拿大",
    "Chile": "智利",
    "China": "中国",
    "Chinese Taipei": "中国台湾",
    "Colombia": "哥伦比亚",
    "Comoros": "科摩罗",
    "Congo DR": "刚果民主共和国",
    "Cook Islands": "库克群岛",
    "Costa Rica": "哥斯达黎加",
    "Dominica": "多米尼克",
    "Ecuador": "厄瓜多尔",
    "Egypt": "埃及",
    "Eswatini": "斯威士兰",
    "Fiji": "斐济",
    "Gibraltar": "直布罗陀",
    "Honduras": "洪都拉斯",
    "India": "印度",
    "Indonesia": "印度尼西亚",
    "Iran": "伊朗",
    "Iraq": "伊拉克",
    "Ivory Coast": "科特迪瓦",
    "Japan": "日本",
    "Jordan": "约旦",
    "Kenya": "肯尼亚",
    "Kuwait": "科威特",
    "Kyrgyz Republic": "吉尔吉斯斯坦",
    "Lebanon": "黎巴嫩",
    "Liechtenstein": "列支敦士登",
    "Lithuania": "立陶宛",
    "Malaysia": "马来西亚",
    "Maldives": "马尔代夫",
    "Mali": "马里",
    "Malta": "马耳他",
    "Mauritius": "毛里求斯",
    "Mexico": "墨西哥",
    "Morocco": "摩洛哥",
    "Mozambique": "莫桑比克",
    "Namibia": "纳米比亚",
    "New Caledonia": "新喀里多尼亚",
    "New Zealand": "新西兰",
    "Niger": "尼日尔",
    "Nigeria": "尼日利亚",
    "Palestine": "巴勒斯坦",
    "Panama": "巴拿马",
    "Papua New Guinea": "巴布亚新几内亚",
    "Paraguay": "巴拉圭",
    "Peru": "秘鲁",
    "Qatar": "卡塔尔",
    "Russia": "俄罗斯",
    "Rwanda": "卢旺达",
    "Sao Tome and Principe": "圣多美和普林西比",
    "Saudi Arabia": "沙特阿拉伯",
    "Senegal": "塞内加尔",
    "Seychelles": "塞舌尔",
    "Singapore": "新加坡",
    "Solomon Islands": "所罗门群岛",
    "South Africa": "南非",
    "South Korea": "韩国",
    "Sri Lanka": "斯里兰卡",
    "Suriname": "苏里南",
    "Swaziland": "斯威士兰",
    "Syria": "叙利亚",
    "Tahiti": "塔希提群岛",
    "Tajikistan": "塔吉克斯坦",
    "Tonga": "汤加",
    "Uganda": "乌干达",
    "United Arab Emirates": "阿联酋",
    "United States": "美国",
    "Uruguay": "乌拉圭",
    "Uzbekistan": "乌兹别克斯坦",
    "Vanuatu": "瓦努阿图",
    "Venezuela": "委内瑞拉",
    "Vietnam": "越南",
    "Yemen": "也门",
}

# 实时比分的联赛覆盖（比 ESPN_LEAGUES 更全）：ESPN 的 scoreboard 更新到秒级，远快于
# win007 移动版赛程页（实测 win007 比分滞后数分钟，雷速 4-0 时它可能还 1-0）。这里把
# 五大联赛 + 全部杯赛 + 次级联赛的 slug 都列上，只有 ESPN 没有的（亚冠二级、波甲）
# 才回退 win007。波兰超（波甲）ESPN slug 已失效（HTTP 400），故不在此表。
ESPN_LIVE_LEAGUES = {
    "epl": "eng.1", "laliga": "esp.1", "seriea": "ita.1",
    "bundesliga": "ger.1", "ligue1": "fra.1",
    "ucl": "uefa.champions", "europa_league": "uefa.europa", "afc_cl": "afc.champions",
    "intl_friendly": "fifa.friendly",
    "primeira": "por.1", "rpl": "rus.1", "superlig": "tur.1",
    "brasileirao": "bra.1", "argentina": "arg.1", "mls": "usa.1",
    "greeksl": "gre.1", "denmark": "den.1", "sweden": "swe.1",
}

# ESPN 英文队名 -> config team_name_map 的英文规范名（即 win007 映射后的队名）。
# 只列与 config 值写法不同的五大联赛球队；已在 ESPN_TEAM_MAP 里的（AS Roma、
# Atlético Madrid、Internazionale、VfB Stuttgart、Stade Rennais、AFC Bournemouth、
# Sporting CP）不重复。这样 ESPN 实时比分的队名能跟推荐（win007）对齐，settle 匹配得上。
ESPN_LIVE_TEAM_MAP = {
    # 英超
    "Brighton & Hove Albion": "Brighton",
    # 西甲
    "Alavés": "Alaves", "Athletic Club": "Athletic Bilbao",
    "Deportivo": "Deportivo La Coruna", "Málaga": "Malaga",
    # 德甲
    "1. FC Union Berlin": "Union Berlin", "Borussia Mönchengladbach": "Borussia Monchengladbach",
    "FC Augsburg": "Augsburg", "FC Cologne": "FC Koln", "Hamburg SV": "Hamburger SV",
    "SC Freiburg": "Freiburg", "SC Paderborn 07": "Paderborn",
    "SV Elversberg": "Elversberg", "TSG Hoffenheim": "Hoffenheim",
    # 法甲
    "AJ Auxerre": "Auxerre", "AS Monaco": "Monaco", "Le Havre AC": "Le Havre",
}

_FINISHED = {"STATUS_FULL_TIME", "STATUS_FINAL", "STATUS_POSTPONED"}


def _norm_iso(iso: str) -> str:
    """ESPN 日期形如 '2026-11-08T14:00Z' -> 统一成 win007 的秒级 '2026-11-08T14:00:00'，
    便于 fixture_key 只用日期前缀就能对齐。"""
    s = (iso or "").strip().replace("Z", "")
    if len(s) == 16:            # YYYY-MM-DDTHH:MM -> 补秒
        s += ":00"
    return s


def _espn_score(score) -> int | None:
    """ESPN 比分可能是字符串 '2'、数字 2、或空/None（未开赛/未进球）。"""
    if score is None or score == "":
        return None
    try:
        return int(float(score))
    except (ValueError, TypeError):
        return None


class EspnClient(FixturesClient):
    name = "espn"

    def __init__(self, team_map: dict | None = None, leagues: dict | None = None,
                 live_leagues: dict | None = None):
        self.team_map = {**ESPN_TEAM_MAP, **ESPN_LIVE_TEAM_MAP, **(team_map or {})}
        self.leagues = leagues or ESPN_LEAGUES
        self.live_leagues = live_leagues or ESPN_LIVE_LEAGUES

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

    def fetch_results(self, comp: Competition, days_back: int, leagues: dict | None = None) -> list[Result]:
        # leagues 允许调用方传入不同的联赛->slug 映射（如实时结算用 ESPN_LIVE_LEAGUES 覆盖
        # 全部 18 联赛），默认仍用 ESPN_LEAGUES（只补 7m 覆盖不到的 7 个杯赛/南美/MLS）。
        slug = (leagues or self.leagues).get(comp.key)
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
        """进行中的比赛（含实时比分），来自 ESPN scoreboard（秒级更新，快于 win007）。

        抓「UTC 今天 + 昨天」两页：晚场跨零点（UTC 视角）仍进行中的场次会挂在昨天
        那页。按 event id 去重。状态用 `status.type.state == "in"` 判定（pre=未开赛、
        in=进行中、post=已结束），比按 name 白名单更稳（半场/暂停等都是 in）。
        """
        slug = self.live_leagues.get(comp.key)
        if not slug:
            return []
        now = datetime.now(timezone.utc)
        days = [(now - timedelta(days=i)).strftime("%Y%m%d") for i in range(2)]
        seen: set[str] = set()
        fixtures: list[Fixture] = []
        for day in days:
            for e in self._scoreboard(slug, day):
                c = (e.get("competitions") or [{}])[0]
                state = (c.get("status", {}).get("type", {}) or {}).get("state")
                if state != "in":
                    continue
                eid = e.get("id")
                if eid is not None and eid in seen:
                    continue
                if eid is not None:
                    seen.add(eid)
                home = away = None
                hg = ag = None
                for t in c.get("competitors", []):
                    name = (t.get("team", {}) or {}).get("displayName") or ""
                    if t.get("homeAway") == "home":
                        home, hg = self._map(name), _espn_score(t.get("score"))
                    else:
                        away, ag = self._map(name), _espn_score(t.get("score"))
                if not home or not away:
                    continue
                ko = _norm_iso(e.get("date") or "")
                fixtures.append(Fixture(
                    key=fixture_key(comp.key, home, away, ko),
                    competition=comp.key, home=home, away=away,
                    kickoff=ko, status="live",
                    home_goals=hg, away_goals=ag,
                ))
        fixtures.sort(key=lambda f: f.kickoff)
        return fixtures

    def fetch_lineup(self, comp: Competition, fixture: Fixture) -> Lineup | None:
        return None
