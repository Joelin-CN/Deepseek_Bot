"""ego-lite 浏览器适配：URL 边界、固定脚本桥接与 TaskSpace 生命周期。"""

from companion.adapters.ego.bridge import EgoBrowser, EgoBridgeError
from companion.adapters.ego.urlguard import validate_public_url

__all__ = ["EgoBrowser", "EgoBridgeError", "validate_public_url"]
