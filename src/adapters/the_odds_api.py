"""the-odds-api 适配器（赔率/水位来源）。

- spreads 盘口 -> 亚盘（让球），price 即十进制赔率（水位）
- totals 盘口  -> 大小球

免费档 500 次/月：每月全量同步约 6 个赛事 * 每天数次，够 MVP 用；
需要更高频率可升级付费，或改用皇冠/球探网抓取适配器。
"""
from __future__ import annotations

from datetime import datetime, timezone

import requests

from ..models import Competition, OddsSnapshot, norm_team
from .base import OddsClient

API = "https://api.the-odds-api.com/v4"


class TheOddsApi(OddsClient):
    name = "the-odds-api"

    def __init__(self, api_key: str, bookmakers: list[str], markets: list[str]):
        self.api_key = api_key
        self.bookmakers = bookmakers
        self.markets = markets

    def fetch_odds(self, comp: Competition) -> list[OddsSnapshot]:
        if not self.api_key:
            raise RuntimeError("缺少 THE_ODDS_API_KEY，无法同步赔率")
        params = {
            "apiKey": self.api_key,
            "regions": "eu",
            "markets": ",".join(self.markets),
            "bookmakers": ",".join(self.bookmakers),
            "oddsFormat": "decimal",
        }
        r = requests.get(f"{API}/sports/{comp.odds_sport_key}/odds", params=params, timeout=30)
        r.raise_for_status()
        return self._parse(comp, r.json())

    def _parse(self, comp: Competition, events: list) -> list[OddsSnapshot]:
        out: list[OddsSnapshot] = []
        ts = datetime.now(timezone.utc).isoformat()
        for ev in events:
            home = ev.get("home_team", "")
            away = ev.get("away_team", "")
            kickoff = ev.get("commence_time", "")
            fk = f"{comp.key}|{norm_team(home)}|{norm_team(away)}|{kickoff[:10]}"

            for book in ev.get("bookmakers", []):
                snap = OddsSnapshot(
                    fixture_key=fk, competition=comp.key,
                    home=home, away=away, kickoff=kickoff,
                    bookmaker=book.get("key", ""), ts=ts,
                )
                for m in book.get("markets", []):
                    self._fill_market(snap, m, home, away)
                out.append(snap)
        return out

    @staticmethod
    def _fill_market(snap: OddsSnapshot, market: dict, home: str, away: str) -> None:
        key = market.get("key")
        outcomes = market.get("outcomes", [])
        if key == "spreads":
            # 亚盘：找主队视角的让球数和双方水位
            for o in outcomes:
                if norm_team(o.get("name", "")) == norm_team(home):
                    snap.handicap_line = o.get("point")
                    snap.handicap_home = o.get("price")
                elif norm_team(o.get("name", "")) == norm_team(away):
                    snap.handicap_away = o.get("price")
        elif key == "totals":
            # 大小球：line 即大小盘口，Over/Under 为水位
            for o in outcomes:
                name = (o.get("name", "") or "").lower()
                snap.total_line = o.get("point")
                if name.startswith("over"):
                    snap.over = o.get("price")
                elif name.startswith("under"):
                    snap.under = o.get("price")
