"""统一的数据模型。

所有数据源（API / 抓取 / 演示数据）都先转换成这里的标准化结构，
后续同步、分析、渲染只依赖这些结构，与具体数据源解耦。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from typing import Optional


_TEAM_NAME_MAP: dict = {}      # 中文队名 -> 英文规范名（启动时由 set_team_name_map 注入）


def set_team_name_map(mapping: dict) -> None:
    """注入队名归一化映射，供 norm_team 在匹配前把中文队名折到英文规范名。

    数据源（球探网）队名时而中文、时而英文，历史快照与最新结果可能不一致；
    统一在匹配层折到英文，避免「沙尔克04」和「Schalke 04」匹配不上。
    """
    global _TEAM_NAME_MAP
    _TEAM_NAME_MAP = mapping or {}


def norm_team(name: str) -> str:
    """归一化队名，用于跨数据源匹配（the-odds-api / API-Football 写法可能不同）。"""
    if not name:
        return ""
    s = name.strip()
    s = _TEAM_NAME_MAP.get(s, s)          # 中文 -> 英文规范名（无映射则原样）
    s = s.lower()
    s = re.sub(r"\s*\(.*?\)", "", s)      # 去掉 (ITA) 之类后缀
    s = re.sub(r"[^a-z0-9一-鿿]", "", s)
    return s


def market_cn(market: str, sport: str = "football") -> str:
    """盘口中文名：足球叫「亚盘/大小球」，篮球叫「让分/总分」。"""
    if sport == "basketball":
        return {"handicap": "让分", "totals": "总分"}.get(market, market)
    return {"handicap": "亚盘", "totals": "大小球"}.get(market, market)


def fixture_key(competition: str, home: str, away: str, kickoff_iso: str) -> str:
    """比赛唯一键：联赛 + 主队 + 客队 + 开赛日期。

    用 (主客队 + 日期) 做关联，比用各家数据源的内部 id 更稳，
    因为不同源之间的 id 无法互认，而队名+日期在同一天内是唯一的。
    """
    day = (kickoff_iso or "")[:10]
    return f"{competition}|{norm_team(home)}|{norm_team(away)}|{day}"


@dataclass
class Competition:
    key: str
    name: str
    cls: str                        # "league" | "cup"
    odds_sport_key: Optional[str] = None
    api_football_id: Optional[int] = None
    win007_id: Optional[int] = None  # 球探网 sclassid（英超36 西甲31 德甲8 意甲34 法甲11）
    sport: str = "football"         # "football" | "basketball"（NBA 等，结算口径不同）
    basketball_id: Optional[int] = None  # 球探网篮球联赛 id（NBA=3）


@dataclass
class Fixture:
    key: str                        # fixture_key()
    competition: str
    home: str
    away: str
    kickoff: str                    # ISO 时间
    status: str = "upcoming"        # upcoming | live | finished
    home_goals: Optional[int] = None
    away_goals: Optional[int] = None


@dataclass
class Result:
    competition: str
    cls: str                        # 所属口径：league / cup
    home: str
    away: str
    home_goals: int
    away_goals: int
    date: str
    # 历史盘口（可选）：过往比赛的亚盘让球线（主队视角，负=主让）与大小球线。
    # 用于「分析结合过往比赛盘口」——看历史交锋/近况里盘口是否被打穿。
    handicap_line: Optional[float] = None
    total_line: Optional[float] = None


@dataclass
class OddsSnapshot:
    """一次赔率快照。同一场比赛多次同步会积累成水位历史。"""
    fixture_key: str
    competition: str
    home: str
    away: str
    kickoff: str
    bookmaker: str
    ts: str                         # 抓取时间（ISO）
    # 亚盘：line 从主队视角，负值=主队让球。price 为十进制赔率（即"水位"）
    handicap_line: Optional[float] = None
    handicap_home: Optional[float] = None
    handicap_away: Optional[float] = None
    # 大小球
    total_line: Optional[float] = None
    over: Optional[float] = None
    under: Optional[float] = None


@dataclass
class Lineup:
    fixture_key: str
    home_xi: list = field(default_factory=list)
    away_xi: list = field(default_factory=list)
    home_formation: Optional[str] = None
    away_formation: Optional[str] = None


@dataclass
class Recommendation:
    fixture_key: str
    competition: str
    home: str
    away: str
    kickoff: str
    market: str                     # "handicap" | "totals"
    pick: str                       # 给用户的推荐文案，如 "主队 -0.5（上盘）"
    side: str                       # home | away | over | under
    confidence: str                 # strong | watch | pass
    score: float                    # 0-100
    line: Optional[float] = None    # 结算用：亚盘=主队视角让球线，大小球=总进球线
    reasons: list = field(default_factory=list)
    signals: dict = field(default_factory=dict)
    history: list = field(default_factory=list)   # 水位快照时间序列（供走势图）
    reliability: float = 1.0        # 信号可靠性 0-1（水位缺失/盘口与水位背离时降低）


def to_dicts(objs) -> list:
    return [asdict(o) for o in objs]
