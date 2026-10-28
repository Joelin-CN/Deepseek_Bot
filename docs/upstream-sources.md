# 上游来源与代码复用记录

当前只有架构参考和外部接入规划，尚未复制或引入第三方应用源码，也没有安装第三方插件。

| 项目 | 本项目中的用途 | 当前接入／复用状态 |
| --- | --- | --- |
| [AstrBot](https://github.com/AstrBotDevs/AstrBot) | 架构参考、插件兼容目标 | 文档与目录研究；没有复制源码 |
| [SnowLuma](https://github.com/SnowLuma/SnowLuma) | 外部 QQ 接入端 | 规划通过 OneBot 连接；未安装或登录 |
| [ego-lite](https://github.com/citrolabs/ego-lite) | 外部浏览器能力 | 规划通过本机 `ego-browser` 接入；未连接到本项目 |
| [DeepSeek API](https://api-docs.deepseek.com/) | 外部模型服务 | 未在本项目配置或调用 |

## AstrBot 许可说明

本轮规划核对的 AstrBot 仓库采用 AGPLv3，另附 EULA。非商业使用不是判断代码复用要求的唯一条件。后续复制、修改或分发具体代码时，应核对当时对应版本的许可并保留必要的来源和版权记录。

- [LICENSE](https://github.com/AstrBotDevs/AstrBot/blob/master/LICENSE)
- [EULA](https://github.com/AstrBotDevs/AstrBot/blob/master/EULA.md)

本项目自身的发布许可尚未选择。当前不创建默认 MIT 或其他许可证文件，以免把尚未决定的许可当成既定事实。

## 后续复用需要记录的内容

每项实际引入的代码记录：来源仓库、固定版本或提交、原始文件路径、许可、引入位置、修改内容及兼容检查结果。

每个第三方插件独立核对来源和许可，不能因为兼容 AstrBot 就默认使用相同许可证。
