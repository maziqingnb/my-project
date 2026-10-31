"""根据环境变量装配推送渠道并发送报告。

渠道（配置任一即启用，都可配）：
  WECOM_WEBHOOK   企业微信群机器人 webhook 地址
  SERVERCHAN_KEY  Server酱 SendKey
"""
from __future__ import annotations

import os

from .format import build_content, build_title
from .serverchan import ServerChanNotifier
from .wecom import WeComNotifier


def push_report(report: dict) -> None:
    channels = []
    wc = os.getenv("WECOM_WEBHOOK", "").strip()
    sc = os.getenv("SERVERCHAN_KEY", "").strip()
    if wc:
        channels.append(WeComNotifier(wc))
    if sc:
        channels.append(ServerChanNotifier(sc))

    if not channels:
        print("[通知] 未配置推送渠道（WECOM_WEBHOOK / SERVERCHAN_KEY），跳过推送")
        return

    title = build_title(report)
    content = build_content(report)
    for ch in channels:
        try:
            ch.send(title, content)
            print(f"[通知] 已推送至 {type(ch).__name__}")
        except Exception as e:  # 单个渠道失败不影响其它渠道
            print(f"[通知] {type(ch).__name__} 推送失败: {e}")
