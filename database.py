"""Camada de persistência (SQLite).

O bot (asyncio) e o scheduler (thread separada do APScheduler) acedem à mesma
base de dados. Para isso usamos uma única ligação com `check_same_thread=False`
protegida por um `threading.RLock`: todas as operações passam pelo mesmo lock,
o que serializa os acessos e evita corrupção ou erros de concorrência.

O modo WAL está activo para reduzir bloqueios entre leituras e escritas.

Todas as datas/horas são guardadas como texto ISO-8601 *com fuso horário*
(ex.: `2026-08-07T15:00:00+01:00`), o que permite comparações lexicográficas
correctas dentro do mesmo fuso e evita ambiguidades no horário de verão.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Iterator, Optional

from config import settings

logger = logging.getLogger(__name__)

# Ligação única partilhada + lock reentrante que a protege.
_lock = threading.RLock()
_connection: Optional[sqlite3.Connection] = None


# ---------------------------------------------------------------------------
# Infra-estrutura
# ---------------------------------------------------------------------------
def _connect() -> sqlite3.Connection:
    """Cria (ou devolve) a ligação partilhada à base de dados."""
    global _connection
    if _connection is None:
        _connection = sqlite3.connect(
            settings.database_path,
            check_same_thread=False,  # partilhada entre threads, protegida por _lock
            timeout=30.0,
            detect_types=0,
        )
        _connection.row_factory = sqlite3.Row
        _connection.execute("PRAGMA journal_mode=WAL")
        _connection.execute("PRAGMA foreign_keys=ON")
        _connection.execute("PRAGMA synchronous=NORMAL")
    return _connection


@contextmanager
def _cursor() -> Iterator[sqlite3.Cursor]:
    """Contexto que fornece um cursor com lock e commit/rollback automáticos."""
    with _lock:
        conn = _connect()
        cursor = conn.cursor()
        try:
            yield cursor
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cursor.close()


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}


def init_db() -> None:
    """Cria as tabelas e índices, caso ainda não existam (idempotente)."""
    with _cursor() as cur:
        cur.executescript(
            """
            -- Compromissos da agenda.
            CREATE TABLE IF NOT EXISTS events (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL,
                chat_id     INTEGER NOT NULL,
                description TEXT    NOT NULL,
                event_time  TEXT    NOT NULL,   -- ISO-8601 com fuso
                created_at  TEXT    NOT NULL
            );

            -- Notas de texto livre.
            CREATE TABLE IF NOT EXISTS notes (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER NOT NULL,
                content    TEXT    NOT NULL,
                created_at TEXT    NOT NULL
            );

            -- Resumos da conversa (memória de longo prazo).
            CREATE TABLE IF NOT EXISTS summaries (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER NOT NULL,
                summary    TEXT    NOT NULL,
                created_at TEXT    NOT NULL
            );

            -- Lembretes pendentes; é esta tabela (e não o APScheduler)
            -- a fonte de verdade, para sobreviver a reinícios.
            CREATE TABLE IF NOT EXISTS reminders (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id   INTEGER NOT NULL,
                chat_id   INTEGER NOT NULL,
                message   TEXT    NOT NULL,
                remind_at TEXT    NOT NULL,     -- ISO-8601 com fuso
                kind      TEXT    NOT NULL DEFAULT 'simple',  -- 'simple' | 'event'
                event_id  INTEGER,
                fired     INTEGER NOT NULL DEFAULT 0,
                created_at TEXT   NOT NULL,
                FOREIGN KEY (event_id) REFERENCES events (id) ON DELETE CASCADE
            );

            -- Preferências por utilizador (chave/valor).
            CREATE TABLE IF NOT EXISTS preferences (
                user_id INTEGER NOT NULL,
                key     TEXT    NOT NULL,
                value   TEXT    NOT NULL,
                PRIMARY KEY (user_id, key)
            );

            CREATE INDEX IF NOT EXISTS idx_events_user_time
                ON events (user_id, event_time);
            CREATE INDEX IF NOT EXISTS idx_notes_user_created
                ON notes (user_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_summaries_user_created
                ON summaries (user_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_reminders_pending
                ON reminders (fired, remind_at);
            """
        )
    logger.info("Base de dados pronta em %s", settings.database_path)


def close_db() -> None:
    """Fecha a ligação partilhada (usado no encerramento)."""
    global _connection
    with _lock:
        if _connection is not None:
            _connection.close()
            _connection = None


# ---------------------------------------------------------------------------
# Eventos
# ---------------------------------------------------------------------------
def create_event(user_id: int, chat_id: int, description: str, event_time: datetime) -> int:
    """Guarda um evento e devolve o respectivo id."""
    with _cursor() as cur:
        cur.execute(
            """
            INSERT INTO events (user_id, chat_id, description, event_time, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                user_id,
                chat_id,
                description.strip(),
                event_time.isoformat(),
                datetime.now(settings.tzinfo).isoformat(),
            ),
        )
        return int(cur.lastrowid)


def get_event(event_id: int) -> Optional[dict[str, Any]]:
    with _cursor() as cur:
        cur.execute("SELECT * FROM events WHERE id = ?", (event_id,))
        row = cur.fetchone()
        return _row_to_dict(row) if row else None


def search_events_by_text(user_id: int, text: str, limit: int = 20) -> list[dict[str, Any]]:
    """Procura eventos cuja descrição contenha `text` (sem distinção de maiúsculas)."""
    with _cursor() as cur:
        cur.execute(
            """
            SELECT * FROM events
            WHERE user_id = ? AND lower(description) LIKE ?
            ORDER BY event_time ASC
            LIMIT ?
            """,
            (user_id, f"%{text.lower().strip()}%", limit),
        )
        return [_row_to_dict(row) for row in cur.fetchall()]


def get_events_between(
    user_id: int, start: datetime, end: datetime, limit: int = 50
) -> list[dict[str, Any]]:
    """Devolve os eventos de um intervalo [start, end)."""
    with _cursor() as cur:
        cur.execute(
            """
            SELECT * FROM events
            WHERE user_id = ? AND event_time >= ? AND event_time < ?
            ORDER BY event_time ASC
            LIMIT ?
            """,
            (user_id, start.isoformat(), end.isoformat(), limit),
        )
        return [_row_to_dict(row) for row in cur.fetchall()]


def get_upcoming_events(user_id: int, now: datetime, limit: int = 20) -> list[dict[str, Any]]:
    """Devolve os próximos eventos a partir de `now`."""
    with _cursor() as cur:
        cur.execute(
            """
            SELECT * FROM events
            WHERE user_id = ? AND event_time >= ?
            ORDER BY event_time ASC
            LIMIT ?
            """,
            (user_id, now.isoformat(), limit),
        )
        return [_row_to_dict(row) for row in cur.fetchall()]


def delete_event(user_id: int, event_id: int) -> bool:
    """Apaga um evento do utilizador. Devolve True se algo foi apagado.

    Os lembretes associados desaparecem com ele (ON DELETE CASCADE); quem
    chamar deve cancelar também os jobs do scheduler.
    """
    with _cursor() as cur:
        cur.execute("DELETE FROM events WHERE id = ? AND user_id = ?", (event_id, user_id))
        return cur.rowcount > 0


def update_event(
    user_id: int,
    event_id: int,
    description: Optional[str] = None,
    event_time: Optional[datetime] = None,
) -> bool:
    """Altera a descrição e/ou a hora de um evento. True se algo mudou."""
    campos: list[str] = []
    valores: list[Any] = []
    if description is not None:
        campos.append("description = ?")
        valores.append(description.strip())
    if event_time is not None:
        campos.append("event_time = ?")
        valores.append(event_time.isoformat())
    if not campos:
        return False

    valores.extend([event_id, user_id])
    with _cursor() as cur:
        cur.execute(
            f"UPDATE events SET {', '.join(campos)} WHERE id = ? AND user_id = ?",
            valores,
        )
        return cur.rowcount > 0


def get_reminders_for_event(event_id: int) -> list[dict[str, Any]]:
    """Lembretes por disparar associados a um evento."""
    with _cursor() as cur:
        cur.execute(
            "SELECT * FROM reminders WHERE event_id = ? AND fired = 0", (event_id,)
        )
        return [_row_to_dict(row) for row in cur.fetchall()]


# ---------------------------------------------------------------------------
# Notas
# ---------------------------------------------------------------------------
def create_note(user_id: int, content: str) -> dict[str, Any]:
    """Guarda uma nota com data/hora e devolve-a."""
    created_at = datetime.now(settings.tzinfo).isoformat()
    with _cursor() as cur:
        cur.execute(
            "INSERT INTO notes (user_id, content, created_at) VALUES (?, ?, ?)",
            (user_id, content.strip(), created_at),
        )
        return {
            "id": int(cur.lastrowid),
            "user_id": user_id,
            "content": content.strip(),
            "created_at": created_at,
        }


def search_notes_by_text(user_id: int, text: str, limit: int = 20) -> list[dict[str, Any]]:
    with _cursor() as cur:
        cur.execute(
            """
            SELECT * FROM notes
            WHERE user_id = ? AND lower(content) LIKE ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (user_id, f"%{text.lower().strip()}%", limit),
        )
        return [_row_to_dict(row) for row in cur.fetchall()]


def list_notes(user_id: int, limit: int = 20) -> list[dict[str, Any]]:
    with _cursor() as cur:
        cur.execute(
            "SELECT * FROM notes WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
            (user_id, limit),
        )
        return [_row_to_dict(row) for row in cur.fetchall()]


def delete_note(user_id: int, note_id: int) -> bool:
    with _cursor() as cur:
        cur.execute("DELETE FROM notes WHERE id = ? AND user_id = ?", (note_id, user_id))
        return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Lembretes
# ---------------------------------------------------------------------------
def create_reminder(
    user_id: int,
    chat_id: int,
    message: str,
    remind_at: datetime,
    kind: str = "simple",
    event_id: Optional[int] = None,
) -> int:
    """Regista um lembrete pendente e devolve o respectivo id."""
    with _cursor() as cur:
        cur.execute(
            """
            INSERT INTO reminders (user_id, chat_id, message, remind_at, kind, event_id, fired, created_at)
            VALUES (?, ?, ?, ?, ?, ?, 0, ?)
            """,
            (
                user_id,
                chat_id,
                message.strip(),
                remind_at.isoformat(),
                kind,
                event_id,
                datetime.now(settings.tzinfo).isoformat(),
            ),
        )
        return int(cur.lastrowid)


def get_reminder(reminder_id: int) -> Optional[dict[str, Any]]:
    with _cursor() as cur:
        cur.execute("SELECT * FROM reminders WHERE id = ?", (reminder_id,))
        row = cur.fetchone()
        return _row_to_dict(row) if row else None


def get_pending_reminders() -> list[dict[str, Any]]:
    """Todos os lembretes por disparar (de todos os utilizadores)."""
    with _cursor() as cur:
        cur.execute("SELECT * FROM reminders WHERE fired = 0 ORDER BY remind_at ASC")
        return [_row_to_dict(row) for row in cur.fetchall()]


def get_user_reminders(user_id: int, limit: int = 20) -> list[dict[str, Any]]:
    """Lembretes pendentes de um utilizador."""
    with _cursor() as cur:
        cur.execute(
            """
            SELECT * FROM reminders
            WHERE user_id = ? AND fired = 0
            ORDER BY remind_at ASC
            LIMIT ?
            """,
            (user_id, limit),
        )
        return [_row_to_dict(row) for row in cur.fetchall()]


def mark_reminder_fired(reminder_id: int) -> None:
    with _cursor() as cur:
        cur.execute("UPDATE reminders SET fired = 1 WHERE id = ?", (reminder_id,))


def delete_reminder(user_id: int, reminder_id: int) -> bool:
    with _cursor() as cur:
        cur.execute("DELETE FROM reminders WHERE id = ? AND user_id = ?", (reminder_id, user_id))
        return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Resumos (memória de longo prazo)
# ---------------------------------------------------------------------------
def save_summary(user_id: int, summary: str) -> None:
    with _cursor() as cur:
        cur.execute(
            "INSERT INTO summaries (user_id, summary, created_at) VALUES (?, ?, ?)",
            (user_id, summary.strip(), datetime.now(settings.tzinfo).isoformat()),
        )


def get_latest_summary(user_id: int) -> Optional[str]:
    """Devolve o resumo mais recente do utilizador, ou None."""
    with _cursor() as cur:
        cur.execute(
            "SELECT summary FROM summaries WHERE user_id = ? ORDER BY id DESC LIMIT 1",
            (user_id,),
        )
        row = cur.fetchone()
        return row["summary"] if row else None


def prune_summaries(user_id: int, keep: int = 5) -> None:
    """Mantém apenas os `keep` resumos mais recentes (evita crescimento infinito)."""
    with _cursor() as cur:
        cur.execute(
            """
            DELETE FROM summaries
            WHERE user_id = ?
              AND id NOT IN (
                  SELECT id FROM summaries WHERE user_id = ? ORDER BY id DESC LIMIT ?
              )
            """,
            (user_id, user_id, keep),
        )


# ---------------------------------------------------------------------------
# Preferências
# ---------------------------------------------------------------------------
def set_preference(user_id: int, key: str, value: str) -> None:
    with _cursor() as cur:
        cur.execute(
            """
            INSERT INTO preferences (user_id, key, value) VALUES (?, ?, ?)
            ON CONFLICT (user_id, key) DO UPDATE SET value = excluded.value
            """,
            (user_id, key.strip(), value.strip()),
        )


def get_preference(user_id: int, key: str, default: Optional[str] = None) -> Optional[str]:
    with _cursor() as cur:
        cur.execute(
            "SELECT value FROM preferences WHERE user_id = ? AND key = ?",
            (user_id, key.strip()),
        )
        row = cur.fetchone()
        return row["value"] if row else default


def delete_preference(user_id: int, key: str) -> bool:
    with _cursor() as cur:
        cur.execute(
            "DELETE FROM preferences WHERE user_id = ? AND key = ?", (user_id, key.strip())
        )
        return cur.rowcount > 0


def get_preferences(user_id: int) -> dict[str, str]:
    with _cursor() as cur:
        cur.execute("SELECT key, value FROM preferences WHERE user_id = ?", (user_id,))
        return {row["key"]: row["value"] for row in cur.fetchall()}
