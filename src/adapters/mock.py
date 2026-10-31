"""演示数据适配器（--demo 模式用）。

内置一份小型样例数据，无需任何 API key 即可跑通
"同步 -> 分析 -> 推荐" 全流程，用于验证系统是否正常。
数据是编造的，仅作演示。
"""
from __future__ import annotations

from datetime import datetime, timezone

from ..models import Competition, Fixture, Result, Lineup, OddsSnapshot, fixture_key
from .base import OddsClient, FixturesClient

# 开赛时间用相对"今天"的动态值，保证演示数据永远处于"未来"状态
_KICKOFFS = {
    "ars_che": "2026-11-01T19:30:00Z",
    "liv_mci": "2026-11-02T16:30:00Z",
    "rma_bay": "2026-11-03T20:00:00Z",
}


def _fk(comp: str, home: str, away: str, k: str) -> str:
    return fixture_key(comp, home, away, _KICKOFFS[k])


class MockOdds(OddsClient):
    name = "mock-odds"

    def fetch_odds(self, comp: Competition) -> list[OddsSnapshot]:
        if comp.key == "epl":
            return [
                # 阿森纳 vs 切尔西：主队让 0.75，临场主队降水（0.92 -> 0.84）-> 主队方向
                OddsSnapshot(_fk("epl", "Arsenal", "Chelsea", "ars_che"), "epl", "Arsenal", "Chelsea",
                             _KICKOFFS["ars_che"], "pinnacle", "2026-10-30T10:00:00Z",
                             -0.75, 0.92, 0.98, 2.5, 0.95, 0.95),
                OddsSnapshot(_fk("epl", "Arsenal", "Chelsea", "ars_che"), "epl", "Arsenal", "Chelsea",
                             _KICKOFFS["ars_che"], "pinnacle", "2026-10-31T14:00:00Z",
                             -0.75, 0.84, 1.06, 2.5, 0.86, 1.04),
                # 利物浦 vs 曼城：盘口 0.5 -> 0.25（降盘），主队升水 -> 客队方向
                OddsSnapshot(_fk("epl", "Liverpool", "Man City", "liv_mci"), "epl", "Liverpool", "Man City",
                             _KICKOFFS["liv_mci"], "pinnacle", "2026-10-30T10:00:00Z",
                             -0.5, 0.90, 1.00, 3.0, 0.92, 0.98),
                OddsSnapshot(_fk("epl", "Liverpool", "Man City", "liv_mci"), "epl", "Liverpool", "Man City",
                             _KICKOFFS["liv_mci"], "pinnacle", "2026-10-31T14:00:00Z",
                             -0.25, 1.05, 0.85, 3.0, 1.02, 0.88),
            ]
        if comp.key == "ucl":
            return [
                # 皇马 vs 拜仁（杯赛）：主队让 0.5，临场主队降水，大小球 2.5 -> 2.75 升盘 -> 大球
                OddsSnapshot(_fk("ucl", "Real Madrid", "Bayern Munich", "rma_bay"), "ucl", "Real Madrid", "Bayern Munich",
                             _KICKOFFS["rma_bay"], "pinnacle", "2026-10-31T10:00:00Z",
                             -0.5, 0.94, 0.96, 2.5, 0.90, 1.00),
                OddsSnapshot(_fk("ucl", "Real Madrid", "Bayern Munich", "rma_bay"), "ucl", "Real Madrid", "Bayern Munich",
                             _KICKOFFS["rma_bay"], "pinnacle", "2026-10-31T15:00:00Z",
                             -0.5, 0.82, 1.08, 2.75, 0.94, 0.96),
            ]
        return []


class MockFixtures(FixturesClient):
    name = "mock-fixtures"

    def fetch_upcoming(self, comp: Competition, days: int) -> list[Fixture]:
        rows = {
            "epl": [
                ("Arsenal", "Chelsea", "ars_che"),
                ("Liverpool", "Man City", "liv_mci"),
            ],
            "ucl": [
                ("Real Madrid", "Bayern Munich", "rma_bay"),
            ],
        }.get(comp.key, [])
        return [
            Fixture(_fk(comp.key, h, a, k), comp.key, h, a, _KICKOFFS[k], status="upcoming")
            for h, a, k in rows
        ]

    def fetch_results(self, comp: Competition, days_back: int) -> list[Result]:
        # (主队, 客队, 主进球, 客进球, 日期)
        data = {
            "epl": [
                ("Arsenal", "Newcastle", 2, 0, "2026-10-25"),
                ("Arsenal", "Tottenham", 1, 1, "2026-10-18"),
                ("Aston Villa", "Arsenal", 0, 2, "2026-10-11"),
                ("Arsenal", "Brighton", 3, 1, "2026-10-04"),
                ("Bournemouth", "Arsenal", 0, 1, "2026-09-27"),
                ("Chelsea", "Fulham", 1, 0, "2026-10-26"),
                ("Chelsea", "West Ham", 2, 1, "2026-10-19"),
                ("Everton", "Chelsea", 1, 1, "2026-10-12"),
                ("Chelsea", "Wolves", 4, 2, "2026-10-05"),
                ("Crystal Palace", "Chelsea", 0, 3, "2026-09-28"),
                ("Liverpool", "Aston Villa", 2, 1, "2026-10-25"),
                ("Liverpool", "Bournemouth", 3, 0, "2026-10-18"),
                ("Wolves", "Liverpool", 1, 2, "2026-10-11"),
                ("Liverpool", "Everton", 1, 0, "2026-10-04"),
                ("Brighton", "Liverpool", 1, 3, "2026-09-27"),
                ("Man City", "Newcastle", 2, 0, "2026-10-26"),
                ("Man City", "Tottenham", 1, 1, "2026-10-19"),
                ("Fulham", "Man City", 0, 2, "2026-10-12"),
                ("Man City", "West Ham", 3, 1, "2026-10-05"),
                ("Wolves", "Man City", 1, 1, "2026-09-28"),
            ],
            "ucl": [
                ("Real Madrid", "Inter", 2, 1, "2026-10-22"),
                ("RB Leipzig", "Real Madrid", 0, 1, "2026-10-08"),
                ("Real Madrid", "Benfica", 3, 0, "2026-10-01"),
                ("AC Milan", "Real Madrid", 1, 2, "2026-09-17"),
                ("Real Madrid", "Shakhtar", 4, 1, "2026-09-10"),
                ("Bayern Munich", "PSG", 2, 2, "2026-10-23"),
                ("Celtic", "Bayern Munich", 0, 3, "2026-10-09"),
                ("Bayern Munich", "Barcelona", 1, 1, "2026-10-02"),
                ("Benfica", "Bayern Munich", 1, 4, "2026-09-18"),
                ("Bayern Munich", "AC Milan", 2, 0, "2026-09-11"),
            ],
        }.get(comp.key, [])
        return [
            Result(competition=comp.key, cls=comp.cls, home=h, away=a,
                   home_goals=hg, away_goals=ag, date=d)
            for h, a, hg, ag, d in data
        ]

    def fetch_lineup(self, comp: Competition, fixture: Fixture) -> Lineup | None:
        if fixture.key == _fk("epl", "Arsenal", "Chelsea", "ars_che"):
            return Lineup(
                fixture_key=fixture.key,
                home_xi=["Raya", "White", "Saliba", "Gabriel", "Timber",
                         "Odegaard", "Rice", "Merino", "Saka", "Havertz", "Martinelli"],
                away_xi=["Sanchez", "Gusto", "Fofana", "Colwill", "Cucurella",
                         "Caicedo", "Fernandez", "Palmer", "Madueke", "Neto", "Jackson"],
                home_formation="4-3-3", away_formation="4-2-3-1",
            )
        return None
