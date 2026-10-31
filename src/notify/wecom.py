"""企业微信群机器人推送。

在群聊里添加机器人，拿到形如
  https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxxx 的地址，
放进环境变量 WECOM_WEBHOOK 即可。
"""
from __future__ import annotations

import requests

from .base import Notifier


class WeComNotifier(Notifier):
    def __init__(self, webhook: str):
        self.webhook = webhook

    def send(self, title: str, content: str) -> None:
        # 企业微信 markdown 不支持表格，直接用 content；title 并入首行
        payload = {
            "msgtype": "markdown",
            "markdown": {"content": content},
        }
        r = requests.post(self.webhook, json=payload, timeout=15)
        r.raise_for_status()
        data = r.json()
        if data.get("errcode") != 0:
            raise RuntimeError(f"企业微信推送失败: {data.get('errmsg')}")
