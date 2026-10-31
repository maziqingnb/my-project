"""皇冠(Crown)盘口抓取适配器。

背景与注意：
- 皇冠盘没有公开稳定的零售站点，通常通过 比分网(球探网/澳客等)、皇冠代理页
  或数据商的付费 API 获取。页面反爬严格、结构常改版。
- 本适配器实现 OddsClient 接口，从"可配置的 HTML 页面"解析每场比赛的
  亚盘(让球盘口+上下盘水位) 与 大小球(盘口+大小水位)，转成统一 OddsSnapshot。
- 配套 samples/crown_sample.html 用于离线验证解析逻辑；接真实数据时，
  只需把 sources.crown_url 指向你的实际页面，并按下方 COLUMNS 微调列顺序。
- 更稳定、合规的做法是接付费皇冠数据 API，接口同样套 OddsClient，分析层零改动。

表格列约定（本适配器默认；接真实站点时按需改 COLUMNS 的索引）：
  主队 | 客队 | 开赛时间 | 让球盘口(主队视角,负=主让) | 主队水位 | 客队水位 | 大小球盘口 | 大球水位 | 小球水位
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

import requests

from ..models import Competition, OddsSnapshot, fixture_key
from .base import OddsClient

# 各字段在第几个 <td>（0 起）。真实站点的列顺序大概率不同，改这里即可。
COLUMNS = dict(
    home=0, away=1, kickoff=2,
    handicap=3, home_water=4, away_water=5,
    total=6, over=7, under=8,
)

_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"),
    "Referer": "https://www.google.com/",
}


def _clean(cell: str) -> str:
    s = re.sub(r"<[^>]+>", "", cell)
    s = s.replace("&nbsp;", " ").replace("&amp;", "&")
    return s.strip()


def _num(s: str):
    if s is None:
        return None
    s = s.replace(",", "").strip()
    if s in ("", "-", "--", "无", "封"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _iso(kickoff: str) -> str:
    """'2026-11-01 19:30' -> '2026-11-01T19:30:00'"""
    s = kickoff.strip().replace(" ", "T")
    if len(s) == 16:
        s += ":00"
    return s


class CrownScraper(OddsClient):
    name = "crown"

    def __init__(self, url: str, team_name_map: dict | None = None, columns: dict | None = None):
        self.url = url
        # 皇冠/比分网常用中文队名 -> API-Football 英文队名，用于和历史战绩对齐
        self.team_name_map = team_name_map or {}
        # 列顺序可从 config.yaml 的 scrapers.crown.columns 覆盖，改配置不用改代码
        self.columns = columns or COLUMNS

    def fetch_odds(self, comp: Competition) -> list[OddsSnapshot]:
        html = self._load()
        return self._parse(html, comp)

    def _load(self) -> str:
        if not self.url:
            raise RuntimeError("未配置皇冠盘地址（config.yaml 的 sources.crown_url）")
        # 支持本地文件，方便离线调试解析逻辑
        if Path(self.url).exists():
            return Path(self.url).read_text(encoding="utf-8", errors="ignore")
        r = requests.get(self.url, headers=_HEADERS, timeout=30)
        r.raise_for_status()
        r.encoding = r.apparent_encoding or "utf-8"
        return r.text

    def _parse(self, html: str, comp: Competition) -> list[OddsSnapshot]:
        ts = datetime.now(timezone.utc).isoformat()
        out: list[OddsSnapshot] = []

        for row in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S | re.I):
            cells = [_clean(c) for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.S | re.I)]
            if len(cells) <= max(self.columns.values()):
                continue

            home = self._map(cells[self.columns["home"]])
            away = self._map(cells[self.columns["away"]])
            kickoff = _iso(cells[self.columns["kickoff"]])
            if not home or not away:
                continue

            out.append(OddsSnapshot(
                fixture_key=fixture_key(comp.key, home, away, kickoff),
                competition=comp.key, home=home, away=away, kickoff=kickoff,
                bookmaker="crown", ts=ts,
                handicap_line=_num(cells[self.columns["handicap"]]),
                handicap_home=_num(cells[self.columns["home_water"]]),
                handicap_away=_num(cells[self.columns["away_water"]]),
                total_line=_num(cells[self.columns["total"]]),
                over=_num(cells[self.columns["over"]]),
                under=_num(cells[self.columns["under"]]),
            ))
        return out

    def _map(self, name: str) -> str:
        return self.team_name_map.get(name, name)
