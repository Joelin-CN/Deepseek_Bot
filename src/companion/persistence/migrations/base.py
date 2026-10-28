"""迁移条目类型。独立于注册表模块，避免各迁移文件反向导入造成循环。"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    statements: tuple[str, ...]
