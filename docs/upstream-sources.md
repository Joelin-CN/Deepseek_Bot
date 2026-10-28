# 上游来源与代码复用记录

当前只有架构参考和外部接入规划，尚未复制或引入第三方应用源码，也没有安装第三方插件。

| 项目 | 本项目中的用途 | 当前接入／复用状态 |
| --- | --- | --- |
| [AstrBot](https://github.com/AstrBotDevs/AstrBot) | 架构参考、插件兼容目标 | 文档与目录研究；没有复制源码 |
| [SnowLuma](https://github.com/SnowLuma/SnowLuma) | 外部 QQ 接入端 | **已安装 v2.2.1 完整版**（`E:\Softwares\SnowLuma`，仓库之外），经正向 WS OneBot v11 接入并完成真实验收；未复制其源码进仓库 |
| [ego-lite](https://github.com/citrolabs/ego-lite) | 外部浏览器能力 | 规划通过本机 `ego-browser` 接入；未连接到本项目 |
| [DeepSeek API](https://api-docs.deepseek.com/) | 外部模型服务 | **已配置并实际调用**（httpx 直连官方 API，无 SDK）；未复制代码 |

## SnowLuma 许可说明

SnowLuma 采用 **SnowLuma Source-Available Non-Commercial License v1.0**（非 OSI 开源许可）：允许个人非商业自托管、私下修改；禁止商业用途（出售、付费托管/代搭、并入付费产品）；再分发与公开发布修改版受额外限制；原生组件（`native/*.node`）禁止逆向。本项目为本机个人自用，符合上述许可；不把其源码或发行包复制进仓库，也不在项目中再分发。

## AstrBot 许可说明

本轮规划核对的 AstrBot 仓库采用 AGPLv3，另附 EULA。非商业使用不是判断代码复用要求的唯一条件。后续复制、修改或分发具体代码时，应核对当时对应版本的许可并保留必要的来源和版权记录。

- [LICENSE](https://github.com/AstrBotDevs/AstrBot/blob/master/LICENSE)
- [EULA](https://github.com/AstrBotDevs/AstrBot/blob/master/EULA.md)

本项目自身的发布许可尚未选择。当前不创建默认 MIT 或其他许可证文件，以免把尚未决定的许可当成既定事实。

## 后续复用需要记录的内容

每项实际引入的代码记录：来源仓库、固定版本或提交、原始文件路径、许可、引入位置、修改内容及兼容检查结果。

每个第三方插件独立核对来源和许可，不能因为兼容 AstrBot 就默认使用相同许可证。
