# 记忆服务与持久化

## 边界

`memory` 是框架服务，负责记忆的新增、检索、更新、删除、来源与范围管理；`persistence` 负责数据库连接、存储实现和迁移。

M3 已落地第一版：显式记忆（记住/查看/搜索/修改/忘记命令）、按会话隔离的范围、修订链、软删除与中文子串检索。自动提取、摘要与向量检索仍是扩展点，未实现。

## 数据分类

| 数据 | 当前实现 | 存放 |
| --- | --- | --- |
| 原始消息与会话记录 | messages 表（状态链 received→model_generated→sent_ok/send_failed/send_unknown） | `data/bot.db`（SQLite，WAL） |
| 短期对话上下文 | 每 SessionKey 的内存历史，条数+估算 token 双上限，重启即清 | 进程内存 |
| 长期记忆及来源/修订 | memories 表（scope、status、revision_of、source_message_id） | `data/bot.db` |
| 模型用量 | usage 表（逐次 token 记录） | `data/bot.db` |
| 插件 KV、浏览经历 | 未实现（M4/M5） | — |

## 范围与可见性

- scope 即 `SessionKey.as_scope()`（如 `qq/3666167977/private/844281600`）。所有记忆操作只作用于当前会话的 scope——跨范围访问在服务层结构上不可表达，不依赖提示词约束。
- 私聊记忆只进入对应私聊的模型上下文；群与群、群与私聊互不可见（群会话在 M6 启用）。
- 删除（忘记）为软删除（status=deleted）；修改为修订（旧行 superseded、新行 revision_of 链接）。检索、上下文注入只取 active，因此删除/修改即时生效（上下文无长驻缓存）。
- 注意数据分类：删除长期记忆后，**短期对话历史**（内存中最近问答，另一类数据）在同会话内仍保留此前的问答轮次，模型可能据此回答；重启或历史被裁剪后即消失。这是预期的分类行为，不是记忆删除失效（真实验收 2026-10-28 验证过完整链路）。
- 原始消息保留在 messages 表（含预览，不含全文）；备份单元为整个 `data/` 目录。

## 检索

v1 中文检索使用 SQLite `LIKE` 子串匹配（通配符转义），对显式记忆量级正确且不依赖分词。本机 SQLite 3.54.1 含 FTS3/4/5，量大后可升级 FTS5（trigram）或向量检索；未选定嵌入服务前不引入向量数据库、不默认聊天 API 提供 embedding。

## 服务接口

`MemoryService`（`src/companion/memory/service.py`）：remember / forget / revise / list_memories / search / context_slice，写操作要求 MEMORY_MANAGE 能力、读要求 MEMORY_READ（来自已验证平台身份与本地配置的角色）。命令路由在 `core/pipeline/commands.py`，仅私聊生效。

## 插件关系

框架提供记忆基础服务；记忆工具、提取或整理策略可以通过插件扩展。`plugins/memory_tools` 是预留的内置插件位置（M4 接入），目前没有可调用工具。

兼容插件存储接口和框架记忆接口是不同用途，不能把插件 KV 存储自动解释成长久记忆。
