# Python 与 Conda 环境约定

## 固定约束

- Conda 环境名称：`Deepseek_bot`，大小写按此名称使用。
- Python 基线：`3.12.x`。
- 基础环境定义：[environment.yml](../environment.yml)。
- Conda 负责 Python 与基础运行环境；应用依赖在此环境内管理。
- 安装、测试、启动、调试和联调使用同一个环境。
- 不使用 `base`、其他项目环境或全局 Python 安装本项目依赖。
- 不另建 `.venv`，不使用默认会自动创建其他环境的 `uv run` 或 `uv sync`。如以后引入 uv，必须显式指向本环境解释器。
- 使用 `python -m pip`、`python -m pytest` 等方式，让工具与解释器归属明确。
- 不修改系统 PATH、系统 `PYTHONPATH` 或 `PYTHONHOME` 来代替项目环境管理。

这里的 Conda 环境与业务环境变量是不同概念。`CONDA_PREFIX` 等由 Conda 激活过程管理；API Key 等业务密钥由后续配置机制读取，不写入环境定义或项目说明文档。

## 本机已创建并验证的环境

检查日期：2026-10-28。

| 项目 | 本机值 |
| --- | --- |
| Conda 可执行文件 | `E:\Softwares\Anaconda\Scripts\conda.exe` |
| 环境目录 | `E:\Softwares\Anaconda\envs\Deepseek_bot` |
| Python 解释器 | `E:\Softwares\Anaconda\envs\Deepseek_bot\python.exe` |
| 实际 Python 版本 | `3.12.16` |
| 实际 pip 版本 | `26.2.2` |

已验证解释器路径、环境前缀、Python 版本、pip 归属、SSL 和 SQLite 基础模块，以及 `pip check`。本机 PowerShell 临时激活也已验证。

这些绝对路径用于本机操作和交接，不应硬编码进应用业务逻辑。其他机器重新探测 Conda 安装位置。

## 推荐：不依赖激活状态执行命令

当前普通命令行未配置 Conda PATH。Agent 优先通过完整 Conda 路径执行，避免不同工具调用使用新的 shell 后丢失激活状态。

在 PowerShell 中：

```powershell
Set-Location 'E:\Deepseek Bot'
& 'E:\Softwares\Anaconda\Scripts\conda.exe' run --no-capture-output -n Deepseek_bot python --version
& 'E:\Softwares\Anaconda\Scripts\conda.exe' run --no-capture-output -n Deepseek_bot python -m pip --version
& 'E:\Softwares\Anaconda\Scripts\conda.exe' run --no-capture-output -n Deepseek_bot python -m pip check
```

后续运行应用或测试时，使用同样的 `conda run --no-capture-output -n Deepseek_bot` 前缀。

## 当前 PowerShell 会话中激活

```powershell
. 'E:\Softwares\Anaconda\shell\condabin\conda-hook.ps1'
conda activate Deepseek_bot
Set-Location 'E:\Deepseek Bot'
python --version
python -c "import sys; print(sys.executable)"
```

此激活仅影响当前 shell。无需执行全局 `conda init`；新开的工具 shell 不应假定已经激活。

## 重建与更新边界

现有环境已经创建，不要重复创建、删除或覆盖。只有在新机器或确认目标环境不存在时，才使用 `conda env create -f environment.yml`。

环境定义使用本机已配置的 Conda 频道，没有修改全局频道配置，也没有锁定所有包的具体构建。应用依赖和更完整的锁定方式在实现阶段确定；不要把本文件当作完整应用依赖清单。

## 应用依赖（2026-10-28 安装并验证）

应用依赖由仓库根的 `pyproject.toml` 定义（src 布局，包名 `companion`），`environment.yml` 仍只定义基础环境。在仓库根执行：

```powershell
& 'E:\Softwares\Anaconda\envs\Deepseek_bot\python.exe' -m pip install -e ".[dev]"
```

| 分组 | 依赖 | 安装版本 |
| --- | --- | --- |
| 运行 | websockets（OneBot 正向 WS 客户端） | 17.3 |
| 运行 | httpx（DeepSeek HTTP） | 0.28.1 |
| 运行 | pydantic / pydantic-settings（配置校验、.env） | 2.14.1 / 2.16.1 |
| dev | pytest / pytest-asyncio | 9.2.0 / 1.4.1 |

`.env` 另含 `SNOWLUMA_WEBUI_PASSWORD`（SnowLuma WebUI 预设密码，至少 8 字符，仅本地；由 `scripts\windows\start_snowluma.ps1` 经 `SNOWLUMA_WEBUI_BOOTSTRAP_PASSWORD` 播种，只在 `E:\Softwares\SnowLuma\config\webui.json` 不存在时生效，播种后跨重启稳定）。

测试：`python -m pytest -q`（331 项全部通过；含替身 WS/模型/浏览器集成测试）。升级依赖后先跑全部测试再联调。

## 必须遵循的推进顺序

先完成对应里程碑的环境调研、盘点、配置和验证，再进入该里程碑的实现或真实联调。步骤和当前缺口见 [环境调查与验证记录](environment-audit.md)。
