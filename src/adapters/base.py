"""数据源适配器接口。

需要新数据源（如皇冠/球探网水位抓取）时，实现下面任一接口即可，
其余模块完全不用改动。
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import Competition, Fixture, Result, OddsSnapshot, Lineup


class OddsClient(ABC):
    """赔率/水位来源。"""

    name = "base"

    @abstractmethod
    def fetch_odds(self, comp: Competition) -> list[OddsSnapshot]:
        """抓取某联赛/杯赛未来场次的赔率快照（含亚盘+大小球）。"""


class FixturesClient(ABC):
    """赛程 / 结果 / 阵容来源。"""

    name = "base"

    @abstractmethod
    def fetch_upcoming(self, comp: Competition, days: int) -> list[Fixture]:
        """未来 N 天内的赛程。"""

    @abstractmethod
    def fetch_results(self, comp: Competition, days_back: int) -> list[Result]:
        """过去 N 天的比赛结果（用于近况/交手）。"""

    @abstractmethod
    def fetch_lineup(self, comp: Competition, fixture: Fixture) -> Lineup | None:
        """某场比赛的首发阵容（临近开赛才有）。"""
