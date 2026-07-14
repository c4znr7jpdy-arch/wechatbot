from .base import BaseMusicPlayer
from .ncm import NetEaseMusic
from .ncm_nodejs import NetEaseMusicNodeJS
from .qishui import QishuiMusic
from .txqq import TXQQMusic
from .xingzhige import XingzhigeKuwoMusic

__all__ = [
    "NetEaseMusic",
    "NetEaseMusicNodeJS",
    "QishuiMusic",
    "XingzhigeKuwoMusic",
    "BaseMusicPlayer",
    "TXQQMusic",
]
