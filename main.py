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
import os
import sys
from logging.handlers import RotatingFileHandler

from telegram import BotCommand, Update
from telegram.error import InvalidToken, NetworkError, TimedOut
from telegram.ext import Application, ApplicationBuilder

import bot as bot_module
import database as db
import llm
import scheduler
from config import PROJECT_ROOT, ConfigError, settings

logger = logging.getLogger(__name__)

# Ficheiro-sentinela: criá-lo pede ao bot que encerre ordenadamente. É como o
# painel de controlo o desliga — matar o processo à força saltaria o
# encerramento e perderia a memória de curto prazo por gravar.
#
# Ancorado na pasta do projeto, e não na de trabalho: o painel escreve-o lá, e
# se o bot fosse arrancado de outro sítio ficavam à espera um do outro.
STOP_FILE = PROJECT_ROOT / ".stop-assistente"

# De quantos em quantos segundos se relê a lista de acesso da base de dados,
# para apanhar as permissões dadas no painel de controlo (outro processo).
ACCESS_REFRESH_SECONDS = 10

# Comandos apresentados no menu do Telegram.
BOT_COMMANDS = [
    BotCommand("today", "Today's appointments"),
    BotCommand("agenda", "What's coming up"),
    BotCommand("notes", "Most recent notes"),
    BotCommand("reminders", "Alerts not yet fired"),
    BotCommand("forget", "Clear our recent chat"),
    BotCommand("who", "Your id and who has access"),
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
        ficheiro = RotatingFileHandler(
            settings.log_file,
            maxBytes=5_000_000,  # 5 MB por ficheiro
            backupCount=3,       # mantém 3 ficheiros antigos
            encoding="utf-8",
        )
        # O registo tem nomes, ids e (se LOG_MESSAGES estiver ligado) o texto
        # das conversas: legível só pelo dono, como a base de dados.
        try:
            os.chmod(settings.log_file, 0o600)
        except OSError:
            pass  # Windows: a protecção é a ACL da pasta do utilizador
        handlers.append(ficheiro)

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


async def watch_stop_file(application: Application) -> None:
    """Encerra o bot quando aparecer o ficheiro-sentinela.

    Em Windows não há forma fiável de enviar um sinal a um processo sem consola,
    por isso o painel de controlo pede a paragem criando um ficheiro. O
    encerramento passa a ser o mesmo do `Ctrl+C`: a memória é gravada.
    """
    while True:
        await asyncio.sleep(2)
        try:
            if STOP_FILE.exists():
                STOP_FILE.unlink(missing_ok=True)
                logger.info("Pedido de paragem recebido. A encerrar...")
                application.stop_running()
                return
        except asyncio.CancelledError:
            raise
        except OSError:
            logger.debug("Não foi possível verificar o ficheiro de paragem.", exc_info=True)


async def watch_access_list() -> None:
    """Relê periodicamente quem tem acesso, para apanhar alterações de fora.

    O painel de controlo do Windows escreve directamente na tabela `access`
    (é outro processo, não passa pelo `/allow`), e o porteiro trabalha a partir
    de uma cópia em memória. Sem isto, dar ou tirar uma permissão pelo painel
    só valia depois de reiniciar o bot.
    """
    anterior = set(bot_module.autorizados())
    while True:
        await asyncio.sleep(ACCESS_REFRESH_SECONDS)
        try:
            actual = await asyncio.to_thread(bot_module.refresh_access_cache)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — uma leitura falhada não pode parar o bot
            logger.debug("Não foi possível reler a lista de acesso.", exc_info=True)
            continue

        if actual != anterior:
            logger.info(
                "Lista de acesso alterada fora do Telegram: %d utilizador(es) "
                "(+%d, -%d).",
                len(actual),
                len(actual - anterior),
                len(anterior - actual),
            )
            anterior = actual


async def on_startup(application: Application) -> None:
    """Executado depois de o event loop arrancar, antes do polling."""
    loop = asyncio.get_running_loop()

    # O scheduler tem de saber quem continua autorizado: um lembrete é
    # agendado com antecedência e o acesso pode ter sido retirado entretanto.
    # Definido antes do `start()`, que já reagenda os lembretes pendentes.
    bot_module.refresh_access_cache()
    scheduler.set_access_check(bot_module.autorizados)
    scheduler.start(build_notifier(application, loop))

    # Um ficheiro deixado de uma execução anterior desligaria o bot logo a
    # seguir ao arranque.
    STOP_FILE.unlink(missing_ok=True)
    application.create_task(watch_stop_file(application))

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

    permitidos = bot_module.refresh_access_cache()
    if settings.allowed_user_ids:
        logger.info(
            "Acesso restrito a %d utilizador(es), pela lista do .env.",
            len(settings.allowed_user_ids),
        )
        return  # a lista do .env é fixa: não há nada para vigiar

    # Com a lista na base de dados, ela pode mudar por fora (painel de controlo).
    application.create_task(watch_access_list())

    if permitidos:
        logger.info("Acesso restrito a %d utilizador(es), pela base de dados.", len(permitidos))
        if not await asyncio.to_thread(db.has_owner):
            logger.warning(
                "Há utilizadores autorizados mas nenhum está marcado como dono: "
                "os comandos /allow e /revoke ficam indisponíveis. Marque um dono "
                "no painel de controlo («Utilizadores» → «Tornar dono»)."
            )
    else:
        # Sem lista, o bot fica mudo para toda a gente — é o comportamento
        # correcto. Ninguém é promovido a dono por escrever primeiro.
        logger.warning(
            "Ninguém está autorizado: o assistente vai ignorar todas as mensagens, "
            "sem responder. Autorize o seu id no painel de controlo "
            "(«Utilizadores») ou preencha ALLOWED_USER_IDS no .env."
        )


async def on_shutdown(application: Application) -> None:
    """Encerramento ordenado: guarda a memória, pára o scheduler, fecha a BD.

    A espera pelas tarefas do scheduler é intencional: com `wait=False` uma
    arrumação a meio continuava a correr enquanto a base de dados era fechada
    debaixo dela.
    """
    scheduler.shutdown(wait=True)
    scheduler.set_access_check(None)

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
