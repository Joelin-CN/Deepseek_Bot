# 插件系统与原生 API（M4 已实现；M5 起含工具执行循环）

## 分层

- `src/companion/api`：原生插件公共接口（插件只从这里导入），API 版本 **1.1**。
- `src/companion/plugin_system`：插件宿主、清单校验、加载器、注册表、KV 与运行状态。
- `src/companion/core/tools`：工具注册表 + **统一执行服务**（M5：权限/校验/超时/取消/裁剪）。
- `src/companion/adapters/ego`：ego-lite 浏览器桥接（M5B：URL 边界、固定脚本、TaskSpace）。
- `src/companion/compat/astrbot`：AstrBot 插件语义适配（M7，未实现）。
- `src/astrbot`：已支持的兼容导入入口（M7，未实现）。

原生 API 版本与框架版本相互独立。1.1 变化：`register_tool` 新增
`required_capability` / `wants_context`；v2 工具处理器接收
`ToolExecutionContext`（含已校验参数与取消信号）；新增
`Capability.WEB_BROWSE`（仅主人）。

## 工具执行（M5 起可用）

插件登记的工具由统一执行服务（`core/tools/execution.py`）执行：

- **签名版本显式声明**：`register_tool(..., wants_context=False)` 为 M4
  签名（handler 只收参数 dict）；`wants_context=True` 为 v2：handler 收
  `ToolExecutionContext`（`run` 已验证上下文、`task_id`、`call_id`、
  `params` 已校验参数、`attach_cleanup()` 登记任务级清理）。框架不猜签名。
- **权限**：`required_capability`（如 `Capability.WEB_BROWSE`，当前仅主
  人）；None 表示基础对话能力。模型只看到当前身份允许且归属插件启用的
  工具；执行前再次校验注册状态与插件启停（防旧名单）。
- **参数**：JSON 解析（绝不 eval）+ 扁平 object Schema 校验（标量/数组
  属性 + required + enum；嵌套对象等不支持的结构在注册时拒绝）；原始
  参数 ≤32 KiB。未知工具/非法参数/权限拒绝都返回结构化错误结果。
- **执行**：单次超时与取消信号竞速（取消先到即中止并返回 cancelled）；
  异常转为 `tool_error` 不外抛；结果经结构保持式裁剪（≤
  `tools.max_tool_result_chars`），不截断序列化 JSON。
- **结果**：返回 `ToolExecutionResult.success/failure` 或任意 JSON 值
  （自动包装为成功）。浏览器等失败要返回 `failure`，让模型得到明确错
  误码而非含混成功。
- **名称映射**：注册表名（允许 `.`）映射为模型函数名（`.`→`__`），
  冲突时在构建名单阶段明确报错。
- **同一任务内重复的 tool_call_id 不重复执行**（结果复用、只回传一次）；
  相同参数不同调用 ID 是合法的新调用。

模型工具循环（应答器内实现）：请求（带可用工具）→ tool_calls → 逐个
校验执行 → 按对应 `tool_call_id` 回传 → 再请求，直到最终答复或达到限
制（`tools.*` 配置）。每轮计入 `max_input_tokens` 预算并逐轮记录用量；
超预算按完整工具轮次配对裁剪。工具轨迹不进入长期聊天历史；只有实际
送达的最终答复提交历史。受主机限=网页工具全局并发 1（忙时立即失败
`browser_busy`，不排队占用 worker）。

任务控制（仅主人，私聊）：`/task list`、`/task status <ID>`、
`/task cancel <ID>`（支持 ID 前缀）。控制命令在会话锁之外处理，长任务
持有锁时仍可取消；取消经 RunContext 事件传导到模型循环、工具执行与浏
览器清理。任务状态：RUNNING/COMPLETED/FAILED/CANCELLED/TIMEOUT。

## 网页任务插件（M5B，`plugins/ego_browser`）

经 ego-lite 执行公开网页任务（要求 `WEB_BROWSE`，当前仅主人）：
`web_search(query)`（Bing 固定搜索流程，结果经重定向参数解码出真实链
接）、`web_open(url)`（返回标题/实际 URL/正文摘录/链接）、
`web_finish()`（结束 TaskSpace；取消/超时/失败由清理钩子兜底）。

桥接机制（ego-browser CLI 0.6.1.13，skill 指南 v2.1.0，实测确认）：
Python 将**固定脚本模板 + JSON 参数头**写入 `ego-browser nodejs` 子进
程 stdin（参数是数据不是代码；脚本输出经 stderr 的 `__EGO_RESULT__`
JSON 行返回）；每次调用一个新进程，TaskSpace 按名跨进程复用；一个任
务一个空间，任务结束 `finish({keep:[]})`。

访问边界（如实说明做到的程度）：仅公网 HTTP/HTTPS；拒绝本机/私网/链
路本地/保留 IP、内部域名、无点主机名、非 HTTP 协议、URL 用户信息；
跳转后的实际地址同样过检（越界即报 `redirect_blocked`）。**DNS 重绑
定（域名解析到私网 IP）不在第一版校验范围**；TaskSpace 是任务组织机
制，不构成浏览器 Profile、登录态或网络隔离；不读取 Cookie/密钥/环境
变量，不提供任意 JS/命令/文件工具；页面内容作为工具数据返回，不提升
为指令。取消/超时会终止桥接子进程，但已发出的浏览器动作可能仍在执行
——清理用独立进程 finish，失败时如实报告"空间状态未知"。

## 插件目录与清单

一个插件 = 一个目录，包含 `manifest.json`（身份唯一来源）与入口 `.py` 文件：

```json
{
  "id": "builtin.hello",              // 小写段式标识（^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$，≤64 字符）
  "name": "问候插件",                  // ≤128 字符
  "version": "1.0.0",                 // 点分数字
  "api_version": "1.0",               // 要求的原生 API 版本
  "entry": "main:HelloPlugin",        // 模块文件名:类名，模块必须是目录内直接 .py 文件
  "description": "……",                // ≤1024 字符
  "config": {                         // 配置定义：类型 + 缺省值 + 说明
    "greeting": {"type": "string", "default": "你好", "description": "问候语前缀"}
  },
  "requires": []                      // 依赖的 pip 包名；只检查可导入，绝不自动安装
}
```

配置类型限定 `string/int/float/bool/list`，缺省值必须与声明类型一致。

发现位置：内置根 `plugins/`（仓库根）+ 配置 `plugins.extra_roots`（外部
插件目录，相对路径按仓库根解析）。内置插件与外部插件同等信任级别。

## 插件编写

```python
from companion.api import Plugin, PluginContext

class HelloPlugin(Plugin):
    async def on_enable(self, ctx: PluginContext) -> None:
        ctx.register_command("hello", self._hello, description="问候", usage="/hello")

    async def on_disable(self, ctx: PluginContext) -> None:
        ...  # 自愿清理；注册与任务由宿主强制撤销

    async def _hello(self, cmd) -> str:
        return f"你好，{cmd.run.actor.display_name}"
```

完整示例：`plugins/hello`、`plugins/counter`（内置），`examples/plugins/echo`
（最小可复制示例，经 `extra_roots` 加载）。

## 生命周期语义（加载 ≠ 启用）

状态：`DISABLED`（已加载未启用）→ `ENABLED`；加载或启用失败进入
`FAILED`/`DISABLED` 并保留错误原因，`/plugin list` 与日志可查。

- **加载**：校验清单 → 依赖可导入检查 → 按文件路径导入入口模块 →
  实例化。不执行插件逻辑、不注册资源。模块以合成名注册进
  `sys.modules`；**卸载不做模块级卸载，不支持热更新**。
- **启用**：合并校验配置 → 创建数据目录与 PluginContext → 调
  `on_enable`。异常则**回滚本次全部注册**（命令/处理器/工具/任务），
  插件停在 DISABLED 并记录原因。
- **停用**：先调 `on_disable`（异常只记日志），随后无论如何撤销该插
  件的全部注册并取消其后台任务（有界等待 5 秒，超时告警）。停用后注
  册表中无该插件条目，新消息不再派发；正在执行中的处理器调用自然结
  束。重复启用/停用幂等，不会重复注册。
- **退出**：进程退出时停用全部已启用插件，但不改写运行状态表。

## 启用状态与持久化规则

启动时的启用判定：**数据库运行状态行（`plugin_runtime_state`）优先；
无行则取配置 `plugins.instances.<id>.enabled`（缺省停用）**。

- 配置文件只决定首次启动；显式管理命令写入数据库后一直优先；
- 进程正常退出/崩溃不回写运行状态表；
- 对已停用插件执行 `/plugin disable` 也会记录"保持停用"意图，使配置
  默认启用的插件重启后仍保持停用。

## 消息派发（接入位置：会话锁内、记忆命令之前）

1. `/plugin …` 管理命令（仅主人，恒消费）；
2. 插件消息处理器（priority 升序，同优先级按注册顺序；消费即止；
   单个处理器异常记日志并跳过，不影响后续与其他插件）；
3. 插件命令 `/name args`（名称全局唯一；匹配即消费；处理器异常回复
   可定位的错误提示，不静默）；
4. 均未消费 → 记忆命令 → 模型应答（原有链路不变）。

命令语法：`/小写命令名 参数文本`。命令名规则
`^[a-z][a-z0-9_]*$`（≤32 字符），`plugin`/`plugins` 为宿主保留。
仅私聊消息进入插件阶段（群聊为 M6 范围）。

处理器返回值：`str`（消费并回复）、`HandlerResult.reply/consume/ignore`、
`None`（不消费）。命令已匹配但返回 `None` = 消费不回复，消息记录为
`consumed` 状态，不进入对话历史。

## PluginContext 能力面

- `config`：清单缺省值 + 配置文件覆盖（只读；未声明/类型不符/超限在
  启用时明确报错；单项 ≤16 KiB，总量 ≤64 KiB）。
- `logger`：`companion.plugin.<id>` 专属日志标识。
- `data_dir`：`<data>/plugins/data/<id>/`，宿主创建；插件 ID 经校验且
  目录解析后必须落在基目录内（防路径穿越）。
- `kv`：本插件命名空间（= 插件 ID，**由宿主绑定，接口无命名空间参
  数**）。键 1–128 字符（字母数字 `_.:-/`），值 JSON 序列化 ≤64 KiB；
  底层为 `plugin_kv` 表（V4 迁移），跨重启持久。插件 KV 是通用状态存
  储，与记忆服务（memories 表）互不相干，也不复用记忆业务服务。
- `register_command / register_message_handler / register_tool`：注册冲
  突抛 `RegistrationConflict`（启用失败回滚），保留名/非法名抛
  `RegistrationError`。
- `create_task`：后台任务归属本插件，异常被观察并记日志；停用/退出时
  统一取消。**当前插件 API 不提供主动发送 QQ 消息的能力**（回复只能
  通过处理器返回值；自主活动属 M8）。

## 工具登记（M4 范围）

插件可登记真实工具描述（名称全局唯一、JSON-Schema 风格参数、归属插
件）。`ToolRegistry` 提供登记/查询/按归属撤销；停用插件即撤销其全部工
具。**统一执行循环、参数校验、超时取消、权限与配额属 M5**，本轮不接
入 DeepSeek 请求。

## 身份与权限边界

- 处理器收到框架构造的 `RunContext`（已验证的发送者、会话、任务 ID、
  取消事件）；昵称/消息正文/模型参数不能改变身份或授予主人权限。
- 插件启停等管理仅限本地配置的 `bot.owner_qq`（私聊命令 `/plugin`）。
- 公共服务遵循当前执行上下文；不为后续工具预建复杂权限系统（M5 按
  需定义）。

## 管理命令（仅主人私聊）

```
/plugin list
/plugin status <插件ID>
/plugin enable <插件ID>     # 需要时先重新加载（例如修复后）
/plugin disable <插件ID>
```

非主人（含白名单成员）收到"仅限主人执行"拒绝回复。加载失败（无有效
清单 ID）的目录在 list 中显示目录名与错误，只能通过修复文件并重启后
生效。

## 信任模型与已知限制

- 同进程 Python 插件按**可信本机代码**运行：命名空间、权限声明与异
  常处理是框架管理机制，**不是操作系统安全隔离**；插件理论上可以阻
  塞事件循环或绕过 SDK 直接操作主机。不可信插件需要进程隔离设计
  （未规划在本阶段）。
- 不做插件市场、任意在线安装、热更新；`requires` 依赖缺失只报告不
  安装；不为迁就插件升级整套环境。
- 消息处理器无超时：阻塞的处理器会占住该会话（其他会话不受影响）。
- 卸载 = 撤销注册与释放资源；Python 模块对象保留在 `sys.modules`。
- AstrBot 兼容插件（M7）尚未支持，原生与兼容插件区分加载属后续工作。

## 相关测试

`tests/unit/test_plugin_manifest.py`、`test_plugin_loader.py`、
`test_plugin_host.py`、`test_plugin_kv.py`、`test_tool_registry.py`；
端到端：`tests/integration/test_plugin_e2e.py`（内置插件 + StubOneBot）。
