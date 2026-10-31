"""Server酱（ServerChan）推送：https://sct.ftqq.com

扫码登录后拿到 SendKey，放进环境变量 SERVERCHAN_KEY 即可，
消息会推到绑定的微信（服务号/公众号消息）。
"""
from __future__ import annotations

import requests

from .base import Notifier


class ServerChanNotifier(Notifier):
    def __init__(self, key: str):
        self.key = key

    def send(self, title: str, content: str) -> None:
        r = requests.post(
            f"https://sctapi.ftqq.com/{self.key}.send",
            data={"title": title[:32], "desp": content},
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        if data.get("code") != 0:
            raise RuntimeError(f"Server酱推送失败: {data.get('message')}")
