"""根据环境变量装配推送渠道并发送报告。

渠道（配置任一即启用）：
  WECOM_WEBHOOK   企业微信群机器人 webhook 地址
  SERVERCHAN_KEY  Server酱 SendKey

赛前提醒：prematch_hours 指定 N 小时内开赛才推，且每场只推一次（用 notified_path 记已推送）。
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from ..storage import read_json, write_json
from .format import build_content, build_title
from .serverchan import ServerChanNotifier
from .wecom import WeComNotifier


def _channels():
    channels = []
    wc = os.getenv("WECOM_WEBHOOK", "").strip()
    sc = os.getenv("SERVERCHAN_KEY", "").strip()
    if wc:
        channels.append(WeComNotifier(wc))
    if sc:
        channels.append(ServerChanNotifier(sc))
    return channels


def _prematch_filter(report: dict, hours: float, notified_path: str) -> tuple[dict, list]:
    """返回 (过滤后的报告, 新推送的 fixture_key 列表)。"""
    now = datetime.now(timezone.utc)
    notified = set(read_json(notified_path, default=[]) or [])
    keep, new_keys = [], []
    for r in report["recommendations"]:
        fk = r.get("fixture_key")
        if fk in notified:
            continue
        try:
            ko = datetime.fromisoformat(r.get("kickoff", "").replace("Z", "+00:00")).replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            continue
        if now < ko <= now + timedelta(hours=hours):
            keep.append(r)
            new_keys.append(fk)
    return {**report, "recommendations": keep, "total": len(keep)}, new_keys


def push_report(report: dict, prematch_hours: float | None = None, notified_path: str | None = None) -> None:
    channels = _channels()
    if not channels:
        print("[通知] 未配置推送渠道（WECOM_WEBHOOK / SERVERCHAN_KEY），跳过推送")
        return

    new_keys: list = []
    if prematch_hours is not None:
        report, new_keys = _prematch_filter(report, prematch_hours, notified_path)
        if not report["recommendations"]:
            print("[通知] 无临近开赛的比赛，跳过")
            return

    title = build_title(report)
    content = build_content(report)
    for ch in channels:
        try:
            ch.send(title, content)
            print(f"[通知] 已推送至 {type(ch).__name__}（{report['total']} 条）")
        except Exception as e:  # 单个渠道失败不影响其它渠道
            print(f"[通知] {type(ch).__name__} 推送失败: {e}")

    if prematch_hours is not None and notified_path and new_keys:
        notified = set(read_json(notified_path, default=[]) or [])
        write_json(notified_path, sorted(notified | set(new_keys)))
