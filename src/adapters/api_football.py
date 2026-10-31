"""API-Football (api-sports.io) 适配器（赛程/结果/阵容来源）。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import requests

from ..models import Competition, Fixture, Result, Lineup, norm_team
from .base import FixturesClient

API = "https://v3.football.api-sports.io"

# 状态映射：API-Football 的状态码 -> 我们的 status
_STATUS = {
    "NS": "upcoming", "TBD": "upcoming", "PST": "upcoming",
    "1H": "live", "2H": "live", "HT": "live", "ET": "live", "BT": "live", "P": "live", "LIVE": "live",
    "FT": "finished", "AET": "finished", "PEN": "finished", "AWD": "finished", "WO": "finished",
}


def _season() -> int:
    """按当前日期推断赛季年：7 月后算新赛季。"""
    now = datetime.now(timezone.utc)
    return now.year if now.month >= 7 else now.year - 1


class ApiFootball(FixturesClient):
    name = "api-football"

    def __init__(self, api_key: str):
        self.api_key = api_key

    def _get(self, path: str, params: dict) -> dict:
        if not self.api_key:
            raise RuntimeError("缺少 API_FOOTBALL_KEY，无法同步赛程/结果/阵容")
        r = requests.get(
            f"{API}/{path}",
            headers={"x-apisports-key": self.api_key},
            params=params, timeout=30,
        )
        r.raise_for_status()
        return r.json()

    def fetch_upcoming(self, comp: Competition, days: int) -> list[Fixture]:
        now = datetime.now(timezone.utc)
        end = now + timedelta(days=days)
        data = self._get("fixtures", {
            "league": comp.api_football_id,
            "season": _season(),
            "from": now.strftime("%Y-%m-%d"),
            "to": end.strftime("%Y-%m-%d"),
            "timezone": "UTC",
        })
        return [self._to_fixture(comp, fx) for fx in data.get("response", [])]

    def fetch_results(self, comp: Competition, days_back: int) -> list[Result]:
        start = (datetime.now(timezone.utc) - timedelta(days=days_back)).strftime("%Y-%m-%d")
        data = self._get("fixtures", {
            "league": comp.api_football_id,
            "season": _season(),
            "from": start,
            "to": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "timezone": "UTC",
        })
        out = []
        for fx in data.get("response", []):
            if self._status(fx) != "finished":
                continue
            t = fx["teams"]
            g = fx["goals"]
            out.append(Result(
                competition=comp.key, cls=comp.cls,
                home=t["home"]["name"], away=t["away"]["name"],
                home_goals=g.get("home") or 0, away_goals=g.get("away") or 0,
                date=(fx["fixture"].get("date") or "")[:10],
            ))
        return out

    def fetch_lineup(self, comp: Competition, fixture: Fixture) -> Lineup | None:
        # fixture_key 形如 {comp}|{home}|{away}|{date}，这里从赛程文件里的原始 id 取不到，
        # 因此按 (主客队 + 日期) 反查 API-Football 的 fixture id。
        day = fixture.kickoff[:10]
        data = self._get("fixtures", {
            "league": comp.api_football_id,
            "season": _season(),
            "date": day,
            "timezone": "UTC",
        })
        for fx in data.get("response", []):
            lu = fx.get("lineups", [])
            if not lu:
                continue
            home = fx["teams"]["home"]["name"]
            away = fx["teams"]["away"]["name"]
            # 简单按队名匹配（lineup 阶段精度要求不高）
            home_lu = next((x for x in lu if x.get("team", {}).get("name") == home), None)
            away_lu = next((x for x in lu if x.get("team", {}).get("name") == away), None)
            if home_lu or away_lu:
                return Lineup(
                    fixture_key=fixture.key,
                    home_xi=[p["player"]["name"] for p in home_lu.get("startXI", [])] if home_lu else [],
                    away_xi=[p["player"]["name"] for p in away_lu.get("startXI", [])] if away_lu else [],
                    home_formation=home_lu.get("formation") if home_lu else None,
                    away_formation=away_lu.get("formation") if away_lu else None,
                )
        return None

    @staticmethod
    def _status(fx: dict) -> str:
        short = (fx.get("fixture", {}).get("status", {}) or {}).get("short", "")
        return _STATUS.get(short, "upcoming")

    @classmethod
    def _to_fixture(cls, comp: Competition, fx: dict) -> Fixture:
        t = fx["teams"]
        g = fx["goals"]
        kickoff = (fx["fixture"].get("date") or "")
        key = f"{comp.key}|{norm_team(t['home']['name'])}|{norm_team(t['away']['name'])}|{kickoff[:10]}"
        return Fixture(
            key=key, competition=comp.key,
            home=t["home"]["name"], away=t["away"]["name"],
            kickoff=kickoff, status=cls._status(fx),
            home_goals=g.get("home"), away_goals=g.get("away"),
        )
