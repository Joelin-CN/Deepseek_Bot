"""插件公共 API 异常类型。

宿主在加载、注册、生命周期和 KV 访问各环节抛出这些异常；插件可以捕
获它们做自己的失败处理，但不应吞掉后假装成功。异常消息面向使用者，
可直接进入日志与管理回执。
"""

from __future__ import annotations


class PluginError(Exception):
    """插件相关错误基类。"""


class PluginLoadError(PluginError):
    """插件无法加载：清单缺失/非法、入口错误、依赖缺失等。"""


class PluginAPIVersionError(PluginLoadError):
    """插件要求的原生 API 版本与当前框架不兼容。"""


class PluginStateError(PluginError):
    """生命周期误用，例如对未加载的插件启用。"""


class RegistrationError(PluginError):
    """注册被拒绝：保留名、非法名称、上下文已失效等。"""


class RegistrationConflict(RegistrationError):
    """注册冲突：命令或工具名称已被其他插件占用。"""


class KVError(PluginError):
    """插件 KV 访问错误基类。"""


class KVKeyError(KVError):
    """键非法：为空、过长或包含不允许的字符。"""


class KVTypeError(KVError):
    """值无法 JSON 序列化。"""


class KVValueTooLarge(KVError):
    """值序列化后超过大小上限。"""
