你现在接手我的本地 Agent 框架项目，请协助我从现有仓库骨架推进到“独立 QQ 账号能够通过 DeepSeek 回复我的私聊消息”，完成环境调查、验证、配置、实现、测试和真实联调。

请持续推进实际工作，不要只给建议或示例代码。需要我扫码、填写本机密钥或提供必要信息时再提问，每次优先问当前最关键的问题。

## 一、先读取项目状态

- 本地仓库：`E:\Deepseek Bot`。
- 远端：[Joelin-CN/Deepseek_Bot](https://github.com/Joelin-CN/Deepseek_Bot)。
- 主要分支为 `main`，初始提交为 `297452e`。之后的 download/temp、环境文档等修改可能尚未提交，先检查实际 Git 状态并保留它们。
- 仓库已有骨架和基础 Conda 环境，没有应用实现、应用依赖或数据库。
- 已有 Node、ego-browser 和运行中的 QQ；SnowLuma 尚未安装。全部状态需重新核对。

先阅读 `AGENTS.md`、`README.md`、`docs/architecture.md`、`docs/development-environment.md`、`docs/environment-audit.md`、`docs/compatibility.md`、`docs/runbook.md` 和 `docs/decisions/`。

我现在授权本阶段进入实现：可以编写项目代码、在指定环境安装必要依赖、准备 SnowLuma 并运行测试与联调。此前“仅骨架”的阶段记录不是永久禁止实现；但必须遵守下面的环境前置流程。

## 二、必须先调查和验证环境

按以下顺序执行，按里程碑分别验收：

1. **调研路线要求**：阅读当前官方源码、文档和发布资料，确认操作系统、Python、QQ、SnowLuma、OneBot、模型 API 和候选依赖的要求。
2. **盘点本机**：检查安装位置、版本、解释器、Conda 环境、QQ 实例、端口和现有配置。先复用合适安装，不重复搭建。
3. **校验兼容与缺口**：给出“要求／本机现状／检查结果／处理方式”的简表。重点确认 SnowLuma 对 QQ 构建的支持，以及 Python 依赖的 Windows、版本和异步接口兼容性。
4. **配置环境**：只处理当前里程碑需要的缺口，使用指定 Conda 环境。外部程序和登录数据放在源码仓库之外。
5. **执行环境验收**：实际验证解释器、pip、依赖一致性、必要端口与组件连接，记录结果。存在不兼容项就先解决，不带着未经核对的环境开始实现。
6. **再实现和联调**：M1 环境通过后推进固定应答；M2 前再补模型配置、鉴权和新增依赖的验证。

M1 不依赖 DeepSeek 密钥，不能把 M2 的缺失配置误当成无法完成 M1。环境可用、单元测试通过和真实 QQ 应答成功是不同的验收项。

## 三、Python 环境是固定约束

- Conda 环境名称：`Deepseek_bot`。
- Python 基线：3.12.x，创建时实际为 3.12.16。
- 本机 Conda：`E:\Softwares\Anaconda\Scripts\conda.exe`。
- 本机环境目录：`E:\Softwares\Anaconda\envs\Deepseek_bot`。
- 本机解释器：`E:\Softwares\Anaconda\envs\Deepseek_bot\python.exe`。
- 基础环境已创建并验证，先检查后复用，不要重复创建、删除或覆盖。
- 安装、测试、启动和调试都使用此环境；不使用 base、全局 Python 或其他项目环境，不另建 `.venv`。
- 不默认使用 `uv run`／`uv sync` 创建另一环境。如引入 uv，显式指定上述解释器。
- 使用 `python -m pip`、`python -m pytest` 等方式；每次工具调用明确指向环境，不假定前一个 shell 的激活状态仍然存在。

优先通过完整 Conda 路径执行，例如：

```powershell
& 'E:\Softwares\Anaconda\Scripts\conda.exe' run --no-capture-output -n Deepseek_bot python --version
& 'E:\Softwares\Anaconda\Scripts\conda.exe' run --no-capture-output -n Deepseek_bot python -m pip --version
& 'E:\Softwares\Anaconda\Scripts\conda.exe' run --no-capture-output -n Deepseek_bot python -m pip check
```

若需当前 PowerShell 会话激活，按 `docs/development-environment.md` 中已验证的方式操作。不要为本项目修改全局 PATH、PYTHONPATH、PYTHONHOME 或其他项目环境。

`environment.yml` 仅定义基础环境，不是完整应用依赖清单。实际应用依赖需要选择、记录并安装到同一环境；密钥不写入 environment.yml 或受 Git 跟踪的文档。

## 四、独立框架路线与 AstrBot 参考

我们从空仓库开发自己的框架，保留自己的主程序、原生插件 API、AstrBot 插件兼容层和记忆服务。

- QQ：SnowLuma＋独立机器人 QQ 账号。
- 模型：DeepSeek 官方 API。
- 浏览器：后续使用 ego-lite，本阶段不实现。
- 插件兼容、长期记忆、自主活动和管理面板在后续里程碑处理。

请实际阅读 [AstrBot](https://github.com/AstrBotDevs/AstrBot) 当前源码，参考启动、组件初始化、平台连接、事件分发、消息标准化、流水线、会话、模型响应、消息回传、异常和退出清理。

形成自己的本地流程：“配置校验 → 初始化 → 平台连接 → 事件接收 → 身份检查与去重 → 会话执行 → 模型调用 → 回复发送 → 记录与退出清理”。

平台细节放在 `adapters/onebot`，模型细节放在 `adapters/deepseek`，执行流程放在对应 core 模块，不把所有逻辑写进一个临时脚本。

我们不改成 AstrBot fork 或 AstrBot 内部插件。参考架构和复制代码分别处理：复用具体代码前核对版本、LICENSE、EULA，并记录来源和修改。AstrBot 当前采用 AGPLv3，不能只依据“非商业”判断；不擅自给本项目选择发布许可。

## 五、先 M1，再 M2

### M1：QQ 私聊固定应答

我用指定测试 QQ 给机器人发消息，她准确回复一次“收到，QQ 链路已连通。”

- 只接收指定发送者的私聊，过滤群消息、自身消息、无关事件及未授权发送者。
- 去重，使用结构化消息段发送文本，核对动作回包和发送结果。
- 处理断线、超时和退出。
- WebSocket 事件和动作回包分别分发，避免处理消息时阻塞唯一接收循环，导致等待回包死锁。
- 先做关键筛选和协议测试，再执行真实私聊验收。

### M2：DeepSeek 实际应答

M1 真实验收通过后，再替换为 DeepSeek 生成的回复。

- 核对当前模型与请求方式，地址、模型和超时可配置。
- 引导我在本机填写 API Key，不要求我在聊天中贴完整密钥。
- 验证超时、鉴权失败、额度或服务异常的处理，记录必要用量，不输出密钥。
- 如加入短期上下文，保持有界，并说明它不等于长期记忆服务。

## 六、SnowLuma 与本机准备

参考：[SnowLuma](https://github.com/SnowLuma/SnowLuma)、[Windows 部署](https://snowluma.github.io/zh/docs/guide/deploy/windows)、[OneBot 配置](https://snowluma.github.io/zh/docs/guide/configuration)、[DeepSeek 文档](https://api-docs.deepseek.com/)。

- 优先 Windows 原生完整发行包；以实际发布页为准，不硬套旧教程文件名。
- 已有 QQ 正在运行，先确定机器人账号和实例，显式核对自动加载设置，避免接入其他账号。
- 把登录界面和运行环境准备好后，再请我完成手机扫码或验证码。
- 优先本机正向 WebSocket：SnowLuma 作服务端，程序作客户端，选择适合事件和动作调用的模式。
- 地址、路径、端口和 Token 以实际配置为准；限定本机访问并启用鉴权。
- 不批量结束 QQ、浏览器或其他同名进程。

## 七、实现、文档与版本管理

先列出本阶段具体文件改动和验收顺序，再持续推进；已确定的架构与环境不反复询问。

- 默认检查用合成事件和替身服务，真实外部操作限定在我指定的账号、会话和任务范围内。
- 不提前实现完整插件系统、向量数据库、浏览器或自主活动，不引入当前不需要的微服务。
- 更新环境记录、README、运行说明和上游来源，让文档与实际结果一致。
- 保留已有未提交改动；密钥、登录态、日志和产物不进入 Git。
- 默认不提交、不推送，结束时列出改动。
- 若需要浏览器，优先使用本机 ego-lite／ego-browser，并读取其当前使用文档。

## 八、最终验收

必须分别确认：环境前置验证通过；程序能按文档启动；机器人账号在线；收到指定私聊；固定应答在 QQ 中收到一次；DeepSeek 实际回复在 QQ 中收到；过滤和去重有效；留下可复现的启动、检查和停止步骤。

不要把 WebSocket 连上、API 返回成功或单元测试通过单独当作全流程完成。

最后报告实际验证结果、环境和解释器、再次启动方式、改动文件及剩余问题。缺少我的扫码或配置时，明确当前停在哪一步，并继续完成不依赖该信息的准备工作。
