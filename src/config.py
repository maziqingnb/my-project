"""配置加载：config.yaml + 环境变量里的 API key。"""
from __future__ import annotations

import os
from pathlib import Path

import yaml

from .models import Competition, set_team_name_map

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "config.yaml"
DATA_DIR = ROOT / "data"
FIXTURES_DIR = DATA_DIR / "fixtures"
RESULTS_DIR = DATA_DIR / "results"
ODDS_DIR = DATA_DIR / "odds_history"
LINEUPS_DIR = DATA_DIR / "lineups"
REPORTS_DIR = DATA_DIR / "reports"
DOCS_REPORTS_DIR = ROOT / "docs" / "reports"


def set_data_root(root) -> None:
    """把数据/报告目录根重定向到 root（demo 模式隔离用，避免污染真实数据）。

    所有模块都通过 `from .. import config as C` 引用 C.FIXTURES_DIR 等，
    运行时改这几个模块级全局变量，即可让后续读写全部落在新目录；
    真实 config.yaml 仍从 ROOT/config 读取，不受影响。
    """
    global DATA_DIR, FIXTURES_DIR, RESULTS_DIR, ODDS_DIR, LINEUPS_DIR, REPORTS_DIR, DOCS_REPORTS_DIR
    root = Path(root)
    DATA_DIR = root
    FIXTURES_DIR = root / "fixtures"
    RESULTS_DIR = root / "results"
    ODDS_DIR = root / "odds_history"
    LINEUPS_DIR = root / "lineups"
    REPORTS_DIR = root / "reports"
    DOCS_REPORTS_DIR = root / "docs_reports"   # demo 不写真实 docs/reports，隔离到本地


def load_config() -> dict:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    cfg["competitions"] = [Competition(**c) for c in cfg["competitions"]]
    cfg["api_keys"] = {
        "the_odds_api": os.getenv("THE_ODDS_API_KEY", "").strip(),
        "api_football": os.getenv("API_FOOTBALL_KEY", "").strip(),
    }
    # 注入队名映射，让 norm_team 在匹配时把中文队名折到英文规范名
    set_team_name_map(cfg.get("team_name_map") or {})
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
