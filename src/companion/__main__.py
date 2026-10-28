"""Entry point: ``python -m companion``."""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

from companion import __version__
from companion.config import ConfigError, load_settings
from companion.core.lifecycle import BotRunner

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

    setup_logging(settings.bot.log_level)
    logger.info(
        "companion %s 启动：reply_mode=%s self_id=%s 授权发送者=%s",
        __version__,
        settings.bot.reply_mode,
        settings.onebot.self_id,
        settings.bot.allowed_private_senders,
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
