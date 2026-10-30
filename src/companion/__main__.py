"""Entry point: ``python -m companion``."""

from __future__ import annotations

import asyncio
import atexit
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

from companion import __version__
from companion.config import ConfigError, load_settings
from companion.core.lifecycle import BotRunner, InstanceAlreadyRunning, SingleInstanceGuard

logger = logging.getLogger("companion")


def setup_logging(level: str) -> None:
    Path("logs").mkdir(exist_ok=True)
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(sys.stderr),
            logging.FileHandler(Path("logs") / "bot.log", encoding="utf-8"),
        ],
    )


def main() -> int:
    load_dotenv()
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"配置错误：{exc}", file=sys.stderr)
        return 2
    assert settings.data_dir_resolved is not None

    guard = SingleInstanceGuard(settings.data_dir_resolved / "companion.lock")
    try:
        guard.acquire()
    except InstanceAlreadyRunning as exc:
        print(
            f"已有机器人实例正在运行（PID {exc.pid}），本次启动取消。\n"
            f"如确认旧实例已停止，可删除锁文件 {guard.lock_path} 后重试。",
            file=sys.stderr,
        )
        return 3
    atexit.register(guard.release)

    setup_logging(settings.bot.log_level)
    logger.info(
        "companion %s 启动：reply_mode=%s self_id=%s 授权发送者=%s data=%s",
        __version__,
        settings.bot.reply_mode,
        settings.onebot.self_id,
        settings.bot.allowed_private_senders,
        settings.data_dir_resolved,
    )

    async def _run() -> None:
        await BotRunner(settings).run()

    try:
        asyncio.run(_run())
    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.info("收到中断信号")
        return 0
    except Exception:
        logger.exception("运行失败")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
