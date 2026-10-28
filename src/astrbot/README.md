# AstrBot 兼容导入入口

这里将承接已经明确支持的 `astrbot.*` 导入路径，实际语义转换集中在 `src/companion/compat/astrbot`。

当前 `api/` 和 `core/` 都只是目录，不是 AstrBot 源码副本，也没有可导入接口。

兼容层应调用原生公共接口。自有内核不得反过来依赖这里的兼容类型。

接口基线与支持清单参见 [兼容范围](../../docs/compatibility.md)。
