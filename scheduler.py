"""Agendamento e reagendamento de lembretes (APScheduler).

Princípio de desenho: **a base de dados é a fonte de verdade**, não o
APScheduler. Cada lembrete é primeiro gravado na tabela `reminders` e só depois
agendado. No arranque, `restore_pending_reminders()` reconstrói todos os jobs a
partir da base de dados, pelo que nada se perde num reinício do bot.

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


def restore_pending_reminders() -> int:
    """Recria os jobs de todos os lembretes por disparar.

    Regras para lembretes cuja hora já passou (bot offline):
      * dentro da janela de tolerância → disparam já, com aviso de atraso;
      * fora dessa janela → são marcados como disparados e ignorados.

    Devolve o número de lembretes reagendados.
    """
    now = datetime.now(settings.tzinfo)
    grace = timedelta(minutes=settings.late_reminder_grace_minutes)
    restored = 0

    for reminder in db.get_pending_reminders():
        # Lembretes de quem perdeu o acesso enquanto o bot esteve desligado
        # não voltam a ser agendados.
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
                "Lembrete #%s demasiado antigo (%s); marcado como disparado.",
                reminder["id"],
                remind_at.isoformat(),
            )
            db.mark_reminder_fired(reminder["id"])

    logger.info("%d lembrete(s) pendente(s) reagendado(s).", restored)
    return restored


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
