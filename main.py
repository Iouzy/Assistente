"""Ponto de entrada do assistente pessoal.

Responsabilidades:
  1. configurar o logging e validar a configuração;
  2. preparar a base de dados;
  3. construir a aplicação do Telegram e registar os handlers;
  4. arrancar o `BackgroundScheduler` já com a ponte thread → event loop
     que permite ao scheduler enviar mensagens no Telegram;
  5. correr o bot em modo *polling* até ser interrompido.

Uso:  python main.py
"""

from __future__ import annotations

import asyncio
import logging
import sys

from telegram import BotCommand, Update
from telegram.ext import Application, ApplicationBuilder

import bot as bot_module
import database as db
import scheduler
from config import ConfigError, settings

logger = logging.getLogger(__name__)

# Comandos apresentados no menu do Telegram.
BOT_COMMANDS = [
    BotCommand("hoje", "Compromissos de hoje"),
    BotCommand("agenda", "Próximos compromissos"),
    BotCommand("notas", "Notas mais recentes"),
    BotCommand("lembretes", "Lembretes por disparar"),
    BotCommand("esquecer", "Limpar a conversa recente"),
    BotCommand("ajuda", "Como usar o assistente"),
]


def setup_logging() -> None:
    """Configura o logging da aplicação."""
    logging.basicConfig(
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        level=getattr(logging, settings.log_level, logging.INFO),
        stream=sys.stdout,
    )
    # Estas bibliotecas são muito faladoras em DEBUG.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("telegram.ext.Application").setLevel(logging.INFO)
    logging.getLogger("apscheduler.executors.default").setLevel(logging.WARNING)


def build_notifier(application: Application, loop: asyncio.AbstractEventLoop):
    """Cria a ponte thread-safe usada pelo scheduler para enviar mensagens.

    O APScheduler corre numa thread própria e não pode aguardar corotinas
    directamente; `run_coroutine_threadsafe` agenda o envio no event loop do
    `python-telegram-bot` e devolve um future que aguardamos nessa thread.
    """

    def notify(chat_id: int, text: str) -> None:
        async def _send() -> None:
            await bot_module.send_text(application.bot, chat_id, text)

        try:
            future = asyncio.run_coroutine_threadsafe(_send(), loop)
            future.result(timeout=30)
        except Exception:  # noqa: BLE001 — falhar um envio não pode parar o scheduler
            logger.exception("Falha ao enviar a notificação para o chat %s.", chat_id)

    return notify


async def on_startup(application: Application) -> None:
    """Executado depois de o event loop arrancar, antes do polling."""
    loop = asyncio.get_running_loop()
    scheduler.start(build_notifier(application, loop))

    try:
        await application.bot.set_my_commands(BOT_COMMANDS)
    except Exception:  # noqa: BLE001 — o menu é acessório
        logger.warning("Não foi possível registar o menu de comandos.", exc_info=True)

    me = await application.bot.get_me()
    logger.info("Assistente online como @%s.", me.username)


async def on_shutdown(application: Application) -> None:
    """Encerramento ordenado: pára o scheduler e fecha a base de dados."""
    scheduler.shutdown(wait=False)
    db.close_db()
    logger.info("Assistente encerrado.")


def main() -> int:
    setup_logging()

    try:
        settings.validate()
    except ConfigError as exc:
        logging.getLogger(__name__).error("Configuração inválida: %s", exc)
        return 1

    db.init_db()

    application = (
        ApplicationBuilder()
        .token(settings.telegram_token)
        .post_init(on_startup)
        .post_shutdown(on_shutdown)
        .build()
    )
    bot_module.register_handlers(application)

    # No Python 3.14 `asyncio.get_event_loop()` deixou de criar um event loop
    # quando não existe nenhum, passando a levantar RuntimeError. Garantimos que
    # há um loop actual antes de entregar o controlo ao python-telegram-bot.
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())

    logger.info("A iniciar o assistente (modelo %s)...", settings.deepseek_model)
    # run_polling trata do ciclo de vida do event loop e do encerramento gracioso.
    application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
