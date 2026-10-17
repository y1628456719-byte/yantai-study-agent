"""站点适配器集合。"""
from .base import Record, Source
from .heu import HEU
from .jlu import JLU
from .neu import NEU

REGISTRY = {
    "jlu": JLU,
    "neu": NEU,
    "heu": HEU,
}

__all__ = ["Record", "Source", "JLU", "NEU", "HEU", "REGISTRY"]
