"""Agendamento e reagendamento de lembretes (APScheduler).

Princípio de desenho: **a base de dados é a fonte de verdade**, não o
APScheduler. Cada lembrete é primeiro gravado na tabela `reminders` e só depois
agendado. No arranque, `restore_pending_reminders()` reconstrói todos os jobs a
partir da base de dados, pelo que nada se perde num reinício do bot.

Esse princípio só valia no arranque, o que abria dois buracos numa máquina que
suspende (um portátil, que é onde isto corre):

* O `misfire_grace_time` faz o APScheduler **descartar** um job cuja hora
  passou há mais do que essa margem. Como é um `DateTrigger`, o job morre aí:
  o `fired` fica a `0` na base de dados e o lembrete nunca mais dispara, com o
  bot a correr e convencido de que está tudo bem.
* Um lembrete fora da janela de tolerância era marcado como disparado **sem
  nunca ter sido enviado** — desaparecia em silêncio, e o utilizador nunca
  ficava a saber que tinha existido.

Por isso a reconciliação (`reconcile_reminders`) passou a correr também de
poucos em poucos minutos, e o que não chegou a horas é **comunicado** em vez
de apagado. Perder um aviso porque o computador estava desligado é aceitável;
perdê-lo sem o dizer não é.

O `BackgroundScheduler` corre numa thread própria, separada do event loop do
`python-telegram-bot`. Como o envio de mensagens no Telegram é assíncrono, este
módulo não envia nada directamente: chama um *notifier* — uma função
thread-safe fornecida pelo `main.py` — que faz a ponte para o event loop.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Callable, Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger

import database as db
from config import settings

logger = logging.getLogger(__name__)

# Assinatura do notificador: (chat_id, texto) -> None. Tem de ser thread-safe.
Notifier = Callable[[int, str], None]

# Devolve o conjunto de utilizadores autorizados. É injectada pelo `main.py`
# para o scheduler não ter de importar o `bot` (que importa o `scheduler`).
AccessCheck = Callable[[], set]

_scheduler: Optional[BackgroundScheduler] = None
_notifier: Optional[Notifier] = None
_access_check: Optional[AccessCheck] = None

# De quantos em quantos minutos se compara a base de dados com os jobs vivos.
# É o que apanha um job descartado pelo APScheduler (máquina suspensa, processo
# ocupado além do `misfire_grace_time`) enquanto o bot continua a correr.
RECONCILE_MINUTES = 5

# Contador para dar um id único a cada job de aviso (podem coexistir dois se
# uma reconciliação apanhar falhados enquanto o aviso anterior espera).
_contador_avisos = 0


def set_access_check(verificacao: Optional[AccessCheck]) -> None:
    """Define como o scheduler confirma que o destinatário ainda tem acesso."""
    global _access_check
    _access_check = verificacao


def _pode_receber(user_id: int) -> bool:
    """Confirma que o destinatário continua autorizado.

    Um lembrete é agendado com horas ou dias de antecedência; entretanto o
    acesso pode ter sido retirado. Sem esta verificação, tirar a permissão a
    alguém não calava o bot — ele continuava a mandar-lhe mensagens.
    """
    if _access_check is None:
        return True
    try:
        return user_id in _access_check()
    except Exception:  # noqa: BLE001 — na dúvida, não enviar
        logger.exception("Não foi possível confirmar o acesso do utilizador %s.", user_id)
        return False


def _job_id(reminder_id: int) -> str:
    return f"reminder-{reminder_id}"


def _tem_job(reminder_id: int) -> bool:
    """True se o lembrete ainda tem um job vivo no scheduler."""
    if _scheduler is None:
        return False
    try:
        return _scheduler.get_job(_job_id(reminder_id)) is not None
    except Exception:  # noqa: BLE001 — na dúvida, tratamos como se não tivesse
        return False


# ---------------------------------------------------------------------------
# Ciclo de vida
# ---------------------------------------------------------------------------
def start(notifier: Notifier) -> BackgroundScheduler:
    """Arranca o scheduler em segundo plano e restaura os lembretes pendentes."""
    global _scheduler, _notifier

    _notifier = notifier
    if _scheduler is None:
        _scheduler = BackgroundScheduler(
            timezone=settings.tzinfo,
            job_defaults={
                # Se o processo estiver ocupado, ainda vale a pena disparar.
                "misfire_grace_time": 300,
                "coalesce": True,
                "max_instances": 1,
            },
        )
    if not _scheduler.running:
        _scheduler.start()
        logger.info("Scheduler iniciado (fuso %s).", settings.timezone)

    restore_pending_reminders()

    # A partir daqui, a base de dados continua a ser a fonte de verdade: de
    # poucos em poucos minutos volta a comparar-se com os jobs vivos, para
    # apanhar os que o APScheduler descartou por terem passado da hora
    # (máquina suspensa, processo ocupado). Sem isto, um lembrete assim ficava
    # perdido até ao reinício seguinte.
    schedule_recurring(
        reconcile_reminders, minutes=RECONCILE_MINUTES, job_id="reconcile-reminders"
    )
    return _scheduler


def shutdown(wait: bool = False) -> None:
    """Encerra o scheduler, se estiver activo."""
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        _scheduler.shutdown(wait=wait)
        logger.info("Scheduler encerrado.")
    _scheduler = None


def is_running() -> bool:
    return _scheduler is not None and _scheduler.running


# ---------------------------------------------------------------------------
# Agendamento
# ---------------------------------------------------------------------------
def schedule_reminder(reminder_id: int, remind_at: datetime, late: bool = False) -> bool:
    """Agenda (ou reagenda) o disparo de um lembrete já gravado na base de dados.

    Devolve True se o job foi criado. `late` marca lembretes recuperados depois
    da hora — a mensagem enviada leva um aviso de atraso.
    """
    if _scheduler is None:
        logger.error("Tentativa de agendar o lembrete %s sem scheduler activo.", reminder_id)
        return False

    _scheduler.add_job(
        _fire_reminder,
        trigger=DateTrigger(run_date=remind_at, timezone=settings.tzinfo),
        args=[reminder_id, late],
        id=_job_id(reminder_id),
        replace_existing=True,
        name=f"Lembrete #{reminder_id}",
    )
    logger.info("Lembrete #%s agendado para %s.", reminder_id, remind_at.isoformat())
    return True


def schedule_recurring(func: Callable[[], Any], minutes: int, job_id: str) -> bool:
    """Agenda uma tarefa periódica (usada para arrumar conversas paradas)."""
    if _scheduler is None or minutes <= 0:
        return False

    _scheduler.add_job(
        func,
        trigger=IntervalTrigger(minutes=minutes),
        id=job_id,
        replace_existing=True,
        name=job_id,
    )
    logger.info("Tarefa periódica %r agendada (cada %d min).", job_id, minutes)
    return True


def cancel_reminder(reminder_id: int) -> bool:
    """Remove o job de um lembrete (não apaga o registo na base de dados)."""
    if _scheduler is None:
        return False
    try:
        _scheduler.remove_job(_job_id(reminder_id))
        return True
    except Exception:  # JobLookupError e afins — o job pode já ter disparado
        return False


def reconcile_reminders() -> int:
    """Compara a base de dados com os jobs vivos e repõe o que faltar.

    Corre no arranque e depois de poucos em poucos minutos. Para cada lembrete
    por disparar que **não** tenha job vivo:
      * hora ainda por vir → é (re)agendado;
      * já passou, dentro da janela de tolerância → dispara já, com aviso de
        atraso;
      * já passou, fora dessa janela → é marcado como disparado **e o
        utilizador é avisado** de que falhou (ver `_agendar_aviso_falhados`).

    Lembretes que já têm job vivo não são tocados: reagendá-los reiniciaria a
    contagem a cada passagem.

    Devolve o número de lembretes (re)agendados.
    """
    now = datetime.now(settings.tzinfo)
    grace = timedelta(minutes=settings.late_reminder_grace_minutes)
    restored = 0
    falhados: list[dict] = []

    for reminder in db.get_pending_reminders():
        if _tem_job(reminder["id"]):
            continue

        # Lembretes de quem perdeu o acesso enquanto o bot esteve desligado
        # não voltam a ser agendados — nem geram aviso de falha, que seria
        # falar com quem já não devia ser contactado.
        if not _pode_receber(int(reminder["user_id"])):
            logger.info(
                "Lembrete #%s ignorado: o utilizador %s já não tem acesso.",
                reminder["id"],
                reminder["user_id"],
            )
            db.mark_reminder_fired(reminder["id"])
            continue

        try:
            remind_at = datetime.fromisoformat(reminder["remind_at"])
        except ValueError:
            logger.warning(
                "Lembrete #%s tem uma data inválida (%r); a ignorar.",
                reminder["id"],
                reminder["remind_at"],
            )
            db.mark_reminder_fired(reminder["id"])
            continue

        if remind_at.tzinfo is None:  # tolerância a dados antigos sem fuso
            remind_at = remind_at.replace(tzinfo=settings.tzinfo)

        if remind_at > now:
            if schedule_reminder(reminder["id"], remind_at):
                restored += 1
        elif now - remind_at <= grace:
            # Atrasado mas ainda relevante: dispara daqui a alguns segundos,
            # já com o bot totalmente iniciado.
            if schedule_reminder(reminder["id"], now + timedelta(seconds=10), late=True):
                restored += 1
        else:
            logger.info(
                "Lembrete #%s demasiado antigo (%s); a avisar que falhou.",
                reminder["id"],
                remind_at.isoformat(),
            )
            falhados.append(reminder)
            db.mark_reminder_fired(reminder["id"])

    _agendar_aviso_falhados(falhados)

    if restored or falhados:
        logger.info(
            "%d lembrete(s) reagendado(s), %d falhado(s).", restored, len(falhados)
        )
    return restored


def restore_pending_reminders() -> int:
    """Reconciliação feita no arranque — onde nenhum job existe ainda."""
    return reconcile_reminders()


# ---------------------------------------------------------------------------
# Aviso dos que falharam
# ---------------------------------------------------------------------------
def _quando(valor: object) -> str:
    """Data de um lembrete em texto curto, para a lista dos que falharam."""
    try:
        momento = datetime.fromisoformat(str(valor))
    except (TypeError, ValueError):
        return str(valor)
    if momento.tzinfo is None:
        momento = momento.replace(tzinfo=settings.tzinfo)
    return momento.strftime("%d/%m às %H:%M")


def _formatar_falhados(reminders: list[dict]) -> str:
    """Mensagem única a listar os lembretes que passaram da hora sem disparar."""
    linhas = "\n".join(f"• {r['message']} — {_quando(r['remind_at'])}" for r in reminders)
    if len(reminders) == 1:
        cabeca = "⏰ *Um aviso não chegou a horas*"
        explicacao = "O assistente não estava a correr quando chegou a hora:"
    else:
        cabeca = f"⏰ *{len(reminders)} avisos não chegaram a horas*"
        explicacao = "O assistente não estava a correr quando chegou a hora deles:"
    return f"{cabeca}\n\n{explicacao}\n\n{linhas}"


def _agendar_aviso_falhados(falhados: list[dict]) -> None:
    """Agenda o envio do resumo dos lembretes falhados, agrupado por pessoa.

    O envio é feito por um job, e não aqui, por uma razão concreta: no arranque
    esta função é chamada de dentro do event loop (o `post_init` do
    python-telegram-bot). O notificador faz `run_coroutine_threadsafe` seguido
    de `future.result()` — chamá-lo a partir do próprio loop bloquearia à
    espera de si mesmo. Na thread do scheduler, segundos depois, não há
    problema nenhum.
    """
    global _contador_avisos
    if not falhados or _scheduler is None:
        return

    por_utilizador: dict[int, list[dict]] = {}
    for reminder in falhados:
        por_utilizador.setdefault(int(reminder["user_id"]), []).append(reminder)

    mensagens = [(destino, _formatar_falhados(lista)) for destino, lista in por_utilizador.items()]

    _contador_avisos += 1
    _scheduler.add_job(
        _enviar_aviso_falhados,
        trigger=DateTrigger(
            run_date=datetime.now(settings.tzinfo) + timedelta(seconds=10),
            timezone=settings.tzinfo,
        ),
        args=[mensagens],
        id=f"falhados-{_contador_avisos}",
        name="Aviso de lembretes falhados",
    )


def _enviar_aviso_falhados(mensagens: list[tuple[int, str]]) -> None:
    """Executado pela thread do scheduler: entrega os resumos de falha."""
    if _notifier is None:
        logger.error("Sem notificador configurado; aviso de falhas não enviado.")
        return
    for destino, texto in mensagens:
        try:
            if not _pode_receber(destino):
                continue
            _notifier(destino, texto)
            logger.info("Aviso de lembretes falhados enviado ao utilizador %s.", destino)
        except Exception:
            # Falhar o aviso de uma pessoa não pode impedir o das outras.
            logger.exception("Falha ao avisar o utilizador %s dos lembretes perdidos.", destino)


# ---------------------------------------------------------------------------
# Disparo
# ---------------------------------------------------------------------------
def _format_reminder(reminder: dict, late: bool) -> str:
    """Constrói o texto enviado ao utilizador quando o lembrete dispara."""
    message = reminder["message"]

    if reminder["kind"] == "event":
        body = f"⏰ *Lembrete de compromisso*\n\n{message}"
    else:
        body = f"⏰ *Lembrete*\n\n{message}"

    if late:
        body += "\n\n_(Este lembrete chegou atrasado — o assistente esteve offline.)_"
    return body


def _fire_reminder(reminder_id: int, late: bool = False) -> None:
    """Executado pela thread do scheduler quando chega a hora do lembrete."""
    try:
        reminder = db.get_reminder(reminder_id)
        if reminder is None:
            logger.warning("Lembrete #%s já não existe; nada a enviar.", reminder_id)
            return
        if reminder["fired"]:
            logger.debug("Lembrete #%s já tinha sido enviado.", reminder_id)
            return

        if not _pode_receber(int(reminder["user_id"])):
            logger.info(
                "Lembrete #%s não enviado: o utilizador %s já não tem acesso.",
                reminder_id,
                reminder["user_id"],
            )
            db.mark_reminder_fired(reminder_id)
            return

        if _notifier is None:
            logger.error("Sem notificador configurado; lembrete #%s não enviado.", reminder_id)
            return

        # Entregamos ao **utilizador**, não ao `chat_id` gravado com o lembrete.
        # Em conversa privada são o mesmo número, mas os registos criados antes
        # de os handlers passarem a ser só privados guardaram o chat_id de um
        # grupo — e continuariam a ser entregues lá, à frente de toda a gente.
        destino = int(reminder["user_id"])
        if destino != int(reminder["chat_id"]):
            logger.warning(
                "Lembrete #%s tinha sido criado no chat %s; entregue em privado ao %s.",
                reminder_id,
                reminder["chat_id"],
                destino,
            )
        _notifier(destino, _format_reminder(reminder, late))
        db.mark_reminder_fired(reminder_id)
        logger.info("Lembrete #%s enviado ao utilizador %s.", reminder_id, destino)
    except Exception:
        # Nunca deixar uma excepção escapar para a thread do APScheduler.
        logger.exception("Falha ao disparar o lembrete #%s.", reminder_id)
