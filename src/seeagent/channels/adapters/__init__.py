"""
IM 通道适配器

各平台的具体实现:
- 飞书
- 企业微信（智能机器人 — HTTP 回调）
- 企业微信（智能机器人 — WebSocket 长连接）
- 钉钉
- QQ 官方机器人
"""

from .dingtalk import DingTalkAdapter
from .feishu import FeishuAdapter
from .qq_official import QQBotAdapter
from .wework_bot import WeWorkBotAdapter
from .wework_ws import WeWorkWsAdapter

__all__ = [
    "FeishuAdapter",
    "WeWorkBotAdapter",
    "WeWorkWsAdapter",
    "DingTalkAdapter",
    "QQBotAdapter",
]
