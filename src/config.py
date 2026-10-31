"""配置加载：config.yaml + 环境变量里的 API key。"""
from __future__ import annotations

import os
from pathlib import Path

import yaml

from .models import Competition

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "config.yaml"
DATA_DIR = ROOT / "data"
FIXTURES_DIR = DATA_DIR / "fixtures"
RESULTS_DIR = DATA_DIR / "results"
ODDS_DIR = DATA_DIR / "odds_history"
LINEUPS_DIR = DATA_DIR / "lineups"
REPORTS_DIR = DATA_DIR / "reports"
DOCS_REPORTS_DIR = ROOT / "docs" / "reports"


def load_config() -> dict:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    cfg["competitions"] = [Competition(**c) for c in cfg["competitions"]]
    cfg["api_keys"] = {
        "the_odds_api": os.getenv("THE_ODDS_API_KEY", "").strip(),
        "api_football": os.getenv("API_FOOTBALL_KEY", "").strip(),
    }
    return cfg


def get_competition(cfg: dict, key: str) -> Competition:
    for c in cfg["competitions"]:
        if c.key == key:
            return c
    raise KeyError(f"未配置的联赛: {key}")


def league_pool(cfg: dict) -> list[Competition]:
    """联赛口径（五大联赛）"""
    return [c for c in cfg["competitions"] if c.cls == "league"]


def cup_pool(cfg: dict) -> list[Competition]:
    """杯赛口径（欧冠等）"""
    return [c for c in cfg["competitions"] if c.cls == "cup"]
