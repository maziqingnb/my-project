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

# 7m 联赛 id（与 win007 的 sclassid 完全不同，需单独维护）。
# 覆盖 12 个联赛（五大联赛 + 葡/俄/土/丹/瑞典/希腊/波）。杯赛（欧冠/欧联/亚冠）
# 与巴甲/阿甲/MLS/国家队友谊赛尚未接入（7m id 未查全或队名映射量大），仍走 win007。
LEAGUE_IDS = {
    "epl": 92,
    "laliga": 85,
    "seriea": 34,
    "bundesliga": 39,
    "ligue1": 93,
    "primeira": 88,
    "rpl": 165,
    "superlig": 180,
    "denmark": 131,
    "sweden": 103,
    "greeksl": 193,
    "poland": 195,
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
    # --- 葡超 ---
    "伊斯托里爾": "埃斯托里尔", "卡薩比亞": "卡萨比亚", "國民隊": "葡萄牙国民",
    "基維辛迪": "吉维森特", "士砵亭": "葡萄牙体育", "布拉加": "布拉加",
    "摩里倫斯": "摩里伦斯", "法馬利卡奧": "法马利康", "波圖": "波尔图",
    "甘馬雷斯": "吉马良斯", "維塞烏": "维塞乌", "艾華卡": "艾华卡",
    "賓菲加": "本菲卡", "辛達卡拉": "圣克拉拉", "里奧阿維": "里奥阿维",
    "阿洛卡": "阿罗卡", "阿馬多拉": "阿马多拉", "馬里迪莫": "马里迪莫",
    # --- 俄超 ---
    "克拉斯諾達爾": "克拉斯诺达尔", "卡山魯賓": "喀山红宝石", "奧倫堡": "奥伦堡加索维克",
    "巴提卡": "加里宁格勒波罗的海", "法克爾": "沃罗涅日火炬", "羅斯杜夫": "罗斯托夫",
    "聖彼德斯堡": "圣彼得堡泽尼特", "艾卡馬特": "格罗兹尼特里克", "莫斯科中央陸軍": "莫斯科中央陆军",
    "莫斯科戴拿模": "莫斯科迪纳摩", "莫斯科斯巴達": "莫斯科斯巴达", "莫斯科火車頭": "莫斯科火车头",
    "莫斯科路迪拿": "罗迪纳莫斯科", "蘇維杜夫": "苏维埃之翼", "阿克倫": "阿克伦陶里亚蒂",
    "馬哈奇卡拉": "马哈奇卡拉",
    # --- 土超 ---
    "伊斯坦堡": "伊斯坦布尔巴萨克塞尔", "伊約普": "埃于普体育", "加拉塔沙雷": "加拉塔萨雷",
    "加辛塔": "加济安泰普大都会", "卡森柏沙": "卡斯帕萨", "比錫達斯": "贝西克塔斯",
    "特拉布宗": "特拉布宗体育", "真格拿拜列治": "根克勒比利吉", "科尼亞": "科尼亚体育",
    "科魯姆": "科鲁姆", "艾蘇林": "埃尔祖鲁姆体育", "葛迪比": "哥兹塔比",
    "薩姆森": "萨姆松体育", "費倫巴治": "费内巴切", "里澤": "里泽体育",
    "阿拉尼亞": "阿拉尼亚体育", "阿美德": "阿美德", "高卡爾利": "科贾埃利体育",
    # --- 丹超 ---
    "哥本哈根": "哥本哈根", "域堡": "维堡", "奧丹斯": "欧登塞", "寧比": "利恩比",
    "施克堡": "锡尔克堡", "桑德捷斯基": "桑德捷斯基", "洛斯查蘭特": "北西兰",
    "米迪蘭特": "中日德兰", "蘭達斯": "兰讷斯", "賀森斯": "霍森斯",
    "邦比": "布隆德比", "阿曉斯": "奥胡斯",
    # --- 瑞典超 ---
    "AIK蘇納": "索尔纳", "佐加頓斯": "尤尔加登", "加爾斯": "哥德堡盖斯",
    "卡馬亞": "卡尔马", "咸史泰斯": "哈尔姆斯塔德", "哈馬比": "哈马比",
    "哥登堡": "哥德堡", "天狼星": "天狼星", "奧基迪": "奥尔格里特",
    "米贊比": "米亚尔比", "艾夫斯堡": "埃尔夫斯堡", "華斯特羅斯": "韦斯特罗斯",
    "赫根": "赫根", "迪加科斯": "代格福什", "馬模": "马尔默",
    "龐馬普卡納": "布洛马波卡纳",
    # --- 希腊超 ---
    "AEK雅典": "AEK雅典", "OFI克雷迪": "OFI克里特", "PAOK沙朗歷基": "塞萨洛尼基",
    "伊拿克里斯": "伊拉克里斯", "利華戴亞高斯": "莱瓦贾科斯", "卓普利斯": "特里波利斯",
    "基菲西亞": "基菲萨", "奧林比亞高斯": "奥林匹亚科斯", "彭尼杜列高斯": "帕纳多里高斯",
    "彭拿典奈高斯": "帕纳辛纳科斯", "禾路斯NFC": "维洛斯NFC", "科拉馬塔": "卡拉玛达",
    "艾杜美圖斯": "阿特罗米托斯", "阿里斯": "阿里斯",
    # --- 波甲 ---
    "GKS卡托威斯": "GKS卡托威斯", "克拉科夫": "克拉科夫", "克拉科維亞": "克拉科维亚",
    "列治普斯納": "波兹南莱赫", "扎布熱礦工": "扎布热矿工", "拉多米克": "拉多麦科",
    "斯拉斯克": "史拉斯科", "歷基亞": "华沙莱吉亚", "沙斯辛": "什切青",
    "琴斯托霍瓦": "琴斯托霍瓦", "皮亞斯特": "皮亚斯特", "科羅納": "哥罗纳",
    "積基朗尼亞": "比亚韦斯托克雅盖隆", "維奇斯塔": "KS莫摩斯", "維澤夫洛茲": "维德祖罗兹",
    "莫托路賓": "莫托路宾", "華沙普洛克": "普沃茨克维斯瓦", "路賓": "卢宾扎格勒比",
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
