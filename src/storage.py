"""轻量 JSON 文件存储。数据以 JSON 形式提交回仓库，便于审计与 diff。"""
from __future__ import annotations

import json
import os
from pathlib import Path


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def read_json(path, default=None):
    p = Path(path)
    if not p.exists():
        return default
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def write_json(path, obj) -> None:
    p = Path(path)
    ensure_dir(p.parent)
    tmp = p.with_suffix(p.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, p)


def append_odds_snapshot(snapshot_dir: Path, snapshot_dict: dict) -> None:
    """把一次快照追加到某场比赛的水位历史文件里。"""
    key = snapshot_dict["fixture_key"]
    safe = key.replace("|", "_").replace("/", "_")
    path = snapshot_dir / f"{safe}.json"
    history = read_json(path, default=[]) or []
    history.append(snapshot_dict)
    write_json(path, history)
