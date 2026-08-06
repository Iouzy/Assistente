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
from logging.handlers import RotatingFileHandler

from telegram import BotCommand, Update
from telegram.error import InvalidToken, NetworkError, TimedOut
from telegram.ext import Application, ApplicationBuilder

import bot as bot_module
import database as db
import llm
import scheduler
from config import ConfigError, settings

logger = logging.getLogger(__name__)

# Comandos apresentados no menu do Telegram.
BOT_COMMANDS = [
    BotCommand("today", "Today's appointments"),
    BotCommand("agenda", "What's coming up"),
    BotCommand("notes", "Most recent notes"),
    BotCommand("reminders", "Alerts not yet fired"),
    BotCommand("forget", "Clear our recent chat"),
    BotCommand("help", "How to use the assistant"),
]


def setup_logging() -> None:
    """Configura o logging para a consola e, opcionalmente, para ficheiro."""
    handlers: list[logging.Handler] = []

    # Com `pythonw.exe` (execução sem janela) o stdout não existe: nesse caso
    # o ficheiro de registo é a única saída possível.
    if sys.stdout is not None:
        handlers.append(logging.StreamHandler(sys.stdout))

    if settings.log_file:
        handlers.append(
            RotatingFileHandler(
                settings.log_file,
                maxBytes=5_000_000,  # 5 MB por ficheiro
                backupCount=3,       # mantém 3 ficheiros antigos
                encoding="utf-8",
            )
        )

    logging.basicConfig(
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        level=getattr(logging, settings.log_level, logging.INFO),
        handlers=handlers or [logging.NullHandler()],
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

    # Arruma periodicamente as conversas paradas: se o processo for morto sem
    # encerramento limpo (fechar a janela, falha de energia), o que já foi
    # resumido está a salvo na base de dados.
    if settings.idle_flush_minutes > 0:
        scheduler.schedule_recurring(llm.flush_idle, minutes=10, job_id="flush-idle")

    try:
        await application.bot.set_my_commands(BOT_COMMANDS)
    except Exception:  # noqa: BLE001 — o menu é acessório
        logger.warning("Não foi possível registar o menu de comandos.", exc_info=True)

    me = await application.bot.get_me()
    logger.info("Assistente online como @%s.", me.username)

    if settings.allowed_user_ids:
        logger.info("Acesso restrito a %d utilizador(es).", len(settings.allowed_user_ids))
    else:
        logger.warning(
            "ATENÇÃO: o bot está ABERTO — qualquer pessoa que descubra @%s pode "
            "falar com ele e gastar o saldo da API. Para o fechar, envie-lhe uma "
            "mensagem, procure o seu id nos registos ('Mensagem de ... (ID)') e "
            "ponha ALLOWED_USER_IDS=<id> no ficheiro .env.",
            me.username,
        )


async def on_shutdown(application: Application) -> None:
    """Encerramento ordenado: guarda a memória, pára o scheduler, fecha a BD."""
    scheduler.shutdown(wait=False)

    # Conversas que nunca atingiram o limite de resumo só existem em RAM.
    # Sem isto, desligar o bot apagava-as sem deixar rasto. O limite de tempo
    # evita que uma API lenta prenda o encerramento indefinidamente.
    try:
        guardadas = await asyncio.wait_for(asyncio.to_thread(llm.flush_all), timeout=60)
        if guardadas:
            logger.info("%d conversa(s) guardada(s) na memória de longo prazo.", guardadas)
    except asyncio.TimeoutError:
        logger.warning("A guardar a memória demorou demasiado; a encerrar na mesma.")
    except Exception:  # noqa: BLE001 — encerrar nunca pode falhar por isto
        logger.exception("Falha ao guardar a memória no encerramento.")

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

    # Os tempos-limite por omissão do python-telegram-bot são de 5 segundos, o
    # que é curto em ligações domésticas lentas ou com o tráfego do Telegram
    # filtrado: a ligação estabelece-se mas a resposta não chega a tempo.
    application = (
        ApplicationBuilder()
        .token(settings.telegram_token)
        .connect_timeout(settings.connect_timeout)
        .read_timeout(settings.read_timeout)
        .write_timeout(settings.read_timeout)
        .pool_timeout(settings.connect_timeout)
        # O long polling mantém o pedido aberto à espera de mensagens novas,
        # pelo que precisa de uma margem bem maior do que os pedidos normais.
        .get_updates_connect_timeout(settings.connect_timeout)
        .get_updates_read_timeout(settings.read_timeout + 30)
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
    try:
        # run_polling trata do ciclo de vida do event loop e do encerramento gracioso.
        application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)
    except InvalidToken:
        logger.error(
            "O Telegram recusou o TELEGRAM_TOKEN.\n"
            "  Abra o ficheiro .env e confirme que a linha do token:\n"
            "    * tem o formato TELEGRAM_TOKEN=1234567890:AAH...\n"
            "    * está toda numa única linha, sem espaços nem aspas\n"
            "    * inclui o número antes dos dois pontos\n"
            "  Se precisar de um token novo, envie /token ao @BotFather."
        )
        return 1
    except TimedOut:
        logger.error(
            "O Telegram não respondeu a tempo (%.0fs para ligar, %.0fs para ler).\n"
            "  Possíveis causas:\n"
            "    * ligação lenta ou instável — suba CONNECT_TIMEOUT e READ_TIMEOUT no .env\n"
            "    * antivírus ou firewall a bloquear o Python\n"
            "    * o operador de rede a filtrar o tráfego do Telegram\n"
            "  Para testar a ligação: curl -m 20 https://api.telegram.org",
            settings.connect_timeout,
            settings.read_timeout,
        )
        return 1
    except NetworkError:
        logger.error(
            "Não foi possível contactar o Telegram. Verifique a ligação à "
            "Internet (ou se uma firewall está a bloquear api.telegram.org)."
        )
        return 1
    except KeyboardInterrupt:
        logger.info("Interrompido pelo utilizador.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
