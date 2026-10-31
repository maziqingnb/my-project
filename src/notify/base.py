"""推送渠道接口。新增渠道（钉钉/TG/邮件等）实现 Notifier 即可。"""
from __future__ import annotations

from abc import ABC, abstractmethod


class Notifier(ABC):
    @abstractmethod
    def send(self, title: str, content: str) -> None:
        """推送一条消息。"""
