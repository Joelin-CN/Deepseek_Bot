# 项目工作约定

## 当前阶段与授权

用户已授权 M3–M9 续做阶段（交接任务书：`docs/agent-handoff-post-m2.md`）。M1（QQ 私聊固定应答）、M2（DeepSeek 实际应答）、M3（统一会话内核 + 基础长期记忆）、M3.1 修正轮（内存历史真正有界、忘记/修订清空短期上下文、模型输入总预算、送达才入史、跨重启恢复有界历史、单实例保护）、M4（原生插件系统与公共 API：`companion.api` + 插件宿主 + 工具注册表 + 内置插件 + `/plugin` 管理 + 插件 KV/运行状态持久化，迁移 v4）与 M5（统一工具执行循环 + DeepSeek tool_calls 闭环 + 任务管理/`/task` 控制路径 + ego-lite 网页工具插件 builtin.ego_browser：web_search/web_open/web_finish，仅主人、公开网页范围）已完成并验收。SnowLuma 位于 `E:\Softwares\SnowLuma`（仓库外）。群聊（M6）、AstrBot 兼容层（M7）、自主活动（M8）、长期运行管理（M9）未实现；群聊真实联调、入群与自主活动启用需用户逐项确认。默认不提交、不推送。

修改前阅读 `README.md`、`docs/architecture.md`、`docs/decisions/0001-project-baseline.md`、`docs/development-environment.md` 和 `docs/environment-audit.md`。涉及插件或记忆时，再阅读对应专题文档。

## 环境前置检查

- 严格按“调研路线要求 → 盘点本机 → 校验兼容与缺口 → 配置环境 → 执行环境验收 → 开始对应里程碑实现／联调”的顺序推进。
- Python 环境固定为 Conda `Deepseek_bot`，版本为 3.12.x；本机路径和实际版本见环境文档。
- 安装、测试和运行使用此环境。优先通过完整 Conda 路径执行 `conda run --no-capture-output -n Deepseek_bot`，不要假定工具 shell 保留激活状态。
- 不在 base、其他项目环境或全局 Python 中安装本项目依赖，不另建 `.venv`。如使用 uv，显式指向本环境解释器。
- 先验证实际解释器、pip 归属和依赖一致性，再安装业务依赖。检查对应里程碑所需的外部组件版本、端口、账号与鉴权，不把 Python 可用等同于联调可用。
- `docs/agent-handoff.md` 是供用户复制的交接模板；其中实现授权只有在用户实际发出该指令时生效。

## 已确认的项目边界

- 从空仓库构建独立框架，不自行改成 AstrBot fork 或宿主插件方案。
- 原生接口放在 `src/companion/api`，自有内核不依赖 `astrbot.*` 类型。
- AstrBot 语义转换放在 `src/companion/compat/astrbot`；`src/astrbot` 只承接已声明支持的导入入口。
- SnowLuma、DeepSeek、ego-lite 的接入细节放在对应适配器中。
- 记忆服务负责范围、来源和检索，通用持久化层负责数据库连接与迁移。
- 插件宿主与模型工具登记是不同职责，不能用工具注册替代完整插件生命周期。

## 文件与数据

- 不提交真实密钥、QQ 登录态、私人消息、数据库、日志或第三方下载内容。
- 根目录 `download/` 和 `temp/` 存放下载产物与临时文件；只跟踪各自的 `README.md`，不把运行产物加入 Git。
- `plugins/` 存放本项目维护的内置插件；`examples/plugins/` 存放开发示例；运行时安装的外部插件放在独立数据目录（配置 `plugins.extra_roots` 指向）。
- 不把 SnowLuma、QQ、ego-lite 的程序或源码复制进仓库。
- 复用第三方代码前核对来源与许可，并更新 `docs/upstream-sources.md`。
- 不为保留目录添加空 Python 模块或伪实现；当前采用 `.gitkeep`。

## 后续实现与验证

- 模块之间通过明确的内部数据格式和公共接口交互。
- 不支持的 AstrBot 接口要明确报告，不以静默空返回冒充兼容。
- 同进程 Python 插件按可信本机代码管理，不宣称已经被安全隔离。
- 默认检查使用合成消息和替身服务；真实账号联调遵循用户已授权的会话及操作范围。
- Windows 管理脚本只管理本项目创建的进程，避免影响日常 QQ 和浏览器。
- 报告完成前核对实际文件和检查结果；目录存在不等于能力可用。
