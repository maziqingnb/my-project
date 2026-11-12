"""雷速体育（m.leisu.com）实时比分适配器 —— 无头浏览器方案。

雷速数据有阿里云 WAF 反爬（设备指纹 + AES 签名 + 加密响应），纯 Python 无法复现，
故由 `leisu_scraper.js`（puppeteer-core）加载页面、让浏览器自动完成签名/解密后，
从 Vuex store 抠出比赛数据输出 JSON；本模块跑子进程解析并映射成标准 Fixture。

队名/联赛映射是「可迭代」的：雷速返回中文队名（name）+ 英文队名（name_en）+ 联赛
中文名（competition）。这里用 config.team_name_map 折中文队名、COMP_MAP 折联赛名，
未覆盖的队名/联赛保持原样（后续可扩表）。
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from ..models import Fixture, fixture_key

# 雷速联赛中文名 -> config 里的 competition key。雷速标题与 win007/config 名称略有差异，
# 这里按「包含关系」模糊匹配；未覆盖的联赛（雷速直播里大量冷门联赛）直接跳过。
COMP_MAP = {
    "英超": "epl", "英格兰超级": "epl",
    "西甲": "laliga", "西班牙甲级": "laliga",
    "意甲": "seriea", "意大利甲级": "seriea",
    "德甲": "bundesliga", "德国甲级": "bundesliga",
    "法甲": "ligue1", "法国甲级": "ligue1",
    "欧冠": "ucl", "欧洲冠军": "ucl",
    "欧联": "europa_league", "欧罗巴": "europa_league", "欧洲联赛": "europa_league",
    "亚冠精英": "afc_cl", "亚冠": "afc_cl",
    "亚冠二级": "afc_cl2",
    "葡超": "primeira", "葡萄牙超级": "primeira",
    "俄超": "rpl", "俄罗斯超级": "rpl",
    "土超": "superlig", "土耳其超级": "superlig",
    "巴甲": "brasileirao", "巴西甲级": "brasileirao",
    "阿甲": "argentina", "阿根廷甲级": "argentina",
    "美职联": "mls", "美国职业": "mls",
    "希腊超": "greeksl", "希腊超级": "greeksl",
    "丹麦超": "denmark", "丹麦超级": "denmark",
    "瑞典超": "sweden", "瑞典超级": "sweden",
    "波甲": "poland", "波兰超级": "poland",
    "友谊赛": "intl_friendly", "国际友谊": "intl_friendly",
}

# 雷速 status -> 本项目 status（1未开赛 2/3/4进行中 8/9/10/11完场 12取消）
_LIVE = {2, 3, 4}
_FINISHED = {8, 9, 10, 11}


def _run_scraper(browser_path: str | None, wait_ms: int) -> dict:
    node = "node"
    script = str(Path(__file__).resolve().parent / "leisu_scraper.js")
    env = {}
    if browser_path:
        env["LEISU_BROWSER"] = browser_path
    proc = subprocess.run([node, script, str(wait_ms)], capture_output=True,
                          text=True, encoding="utf-8", errors="replace",
                          timeout=120, env={**__import__("os").environ, **env})
    if proc.returncode != 0:
        raise RuntimeError(f"leisu_scraper 失败：{proc.stderr.strip()[:200]}")
    return json.loads(proc.stdout)


def fetch_live_matches(cfg: dict) -> list[Fixture]:
    """抓雷速进行中的比赛，映射成 Fixture。找不到浏览器/雷速时返回空列表（不崩整轮）。"""
    try:
        doc = _run_scraper(
            (cfg.get("sources") or {}).get("leisu_browser"),
            int((cfg.get("sync") or {}).get("leisu_wait_ms", 15000)),
        )
    except Exception as exc:
        print(f"[雷速] 抓取失败（{exc}）")
        return []

    name_map = cfg.get("team_name_map") or {}
    out: list[Fixture] = []
    for m in doc.get("matches", []):
        status = m.get("status")
        if status not in _LIVE:
            continue                      # 实时板块只关心进行中
        comp_key = None
        title = m.get("competition") or ""
        for kw, key in COMP_MAP.items():
            if kw in title:
                comp_key = key
                break
        if not comp_key:
            continue                      # 不在本项目联赛范围
        # 队名：优先中文名走 team_name_map（与 win007 口径一致），无映射再落英文名
        home = name_map.get(m.get("home") or "", m.get("home_en") or m.get("home") or "")
        away = name_map.get(m.get("away") or "", m.get("away_en") or m.get("away") or "")
        if not home or not away:
            continue
        ko_ts = m.get("kickoff") or 0
        kickoff = datetime.fromtimestamp(ko_ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        out.append(Fixture(
            key=fixture_key(comp_key, home, away, kickoff),
            competition=comp_key, home=home, away=away, kickoff=kickoff,
            status="live", home_goals=m.get("home_goals"), away_goals=m.get("away_goals"),
        ))
    return out
