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
import os
import pathlib
import sqlite3
import threading
from contextlib import contextmanager
from datetime import date, datetime
from typing import Any, Iterator, Optional

import safety
from config import settings

logger = logging.getLogger(__name__)

# Ligação única partilhada + lock reentrante que a protege.
_lock = threading.RLock()
_connection: Optional[sqlite3.Connection] = None

# Depois de `close_db()` a ligação não volta a abrir sozinha. Sem isto, uma
# tarefa do scheduler que ainda estivesse a correr durante o encerramento
# reabria a base de dados a seguir a ela ter sido fechada.
_closed = False


class DatabaseClosed(RuntimeError):
    """Levantada quando se tenta usar a base de dados depois do encerramento."""


def _restringir_permissoes(caminho: str) -> None:
    """Deixa o ficheiro legível só pelo dono (0600), quando o SO o permite.

    A base de dados tem a agenda, as notas e os resumos das conversas; por
    omissão era criada a 0644, ou seja, legível por qualquer conta da máquina.
    Em Windows o `chmod` do Python não faz nada de útil — lá a protecção é a
    ACL da pasta do utilizador —, por isso a falha é ignorada em silêncio.
    """
    for sufixo in ("", "-wal", "-shm"):
        ficheiro = pathlib.Path(caminho + sufixo)
        try:
            if ficheiro.exists():
                os.chmod(ficheiro, 0o600)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Infra-estrutura
# ---------------------------------------------------------------------------
def _connect() -> sqlite3.Connection:
    """Cria (ou devolve) a ligação partilhada à base de dados."""
    global _connection
    if _closed:
        raise DatabaseClosed("A base de dados já foi fechada.")
    if _connection is None:
        # Cria o ficheiro já com as permissões certas, em vez de o criar aberto
        # e apertar a seguir (entre as duas coisas havia uma janela de leitura).
        caminho = pathlib.Path(settings.database_path)
        caminho.parent.mkdir(parents=True, exist_ok=True)
        if not caminho.exists():
            os.close(os.open(caminho, os.O_CREAT | os.O_WRONLY, 0o600))
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
        # O modo WAL cria os ficheiros -wal e -shm: também eles têm conteúdo.
        _restringir_permissoes(settings.database_path)
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

            -- Pequenos acontecimentos, arrumados pelo dia em que aconteceram.
            --
            -- Separado das notas por causa da data: uma nota guarda *quando
            -- foi escrita*, e aqui o que interessa é *quando aconteceu* — se
            -- ao domingo se contar uma ida ao dentista na quinta, a linha do
            -- tempo tem de a pôr na quinta.
            --
            -- `happened_on` é um dia (YYYY-MM-DD), sem hora nem fuso. Além de
            -- ser o que se pretende, evita a comparação de textos ISO com
            -- deslocamento, que troca a ordem na hora repetida do fim do
            -- horário de verão.
            CREATE TABLE IF NOT EXISTS moments (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL,
                content     TEXT    NOT NULL,
                happened_on TEXT    NOT NULL,   -- YYYY-MM-DD (dia local)
                created_at  TEXT    NOT NULL
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

            -- Quem pode falar com o bot. Se estiver vazia e não houver
            -- ALLOWED_USER_IDS no .env, o bot não responde a ninguém: os ids
            -- são acrescentados de propósito, pelo painel ou pelo dono.
            CREATE TABLE IF NOT EXISTS access (
                user_id    INTEGER PRIMARY KEY,
                label      TEXT    NOT NULL DEFAULT '',
                is_owner   INTEGER NOT NULL DEFAULT 0,
                granted_at TEXT    NOT NULL
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
            CREATE INDEX IF NOT EXISTS idx_moments_user_day
                ON moments (user_id, happened_on);
            CREATE INDEX IF NOT EXISTS idx_summaries_user_created
                ON summaries (user_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_reminders_pending
                ON reminders (fired, remind_at);
            """
        )
    logger.info("Base de dados pronta em %s", settings.database_path)


def close_db() -> None:
    """Fecha a ligação partilhada (usado no encerramento).

    A partir daqui qualquer acesso levanta `DatabaseClosed`, em vez de reabrir
    a base de dados às escondidas — quem chegar atrasado tem de falhar de
    forma visível, não escrever num ficheiro que já ninguém vai fechar.
    """
    global _connection, _closed
    with _lock:
        _closed = True
        if _connection is not None:
            _connection.close()
            _connection = None


def reopen_db() -> None:
    """Reabre a base de dados depois de um `close_db()` (usado nos testes)."""
    global _closed
    with _lock:
        _closed = False


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


def get_reminders_for_event(user_id: int, event_id: int) -> list[dict[str, Any]]:
    """Lembretes por disparar de um evento, restritos ao dono dos dados.

    O `user_id` não é decorativo: sem ele, saber o id de um evento bastava
    para mexer nos lembretes de outra pessoa.
    """
    with _cursor() as cur:
        cur.execute(
            "SELECT * FROM reminders WHERE event_id = ? AND user_id = ? AND fired = 0",
            (event_id, user_id),
        )
        return [_row_to_dict(row) for row in cur.fetchall()]


def event_belongs_to(user_id: int, event_id: int) -> bool:
    """True se o evento existir e for mesmo desta pessoa."""
    with _cursor() as cur:
        cur.execute(
            "SELECT 1 FROM events WHERE id = ? AND user_id = ?", (event_id, user_id)
        )
        return cur.fetchone() is not None


def reminder_belongs_to(user_id: int, reminder_id: int) -> bool:
    """True se o lembrete existir e for mesmo desta pessoa."""
    with _cursor() as cur:
        cur.execute(
            "SELECT 1 FROM reminders WHERE id = ? AND user_id = ?", (reminder_id, user_id)
        )
        return cur.fetchone() is not None


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
# Linha do tempo (acontecimentos passados)
# ---------------------------------------------------------------------------
def create_moment(user_id: int, content: str, happened_on: date) -> dict[str, Any]:
    """Regista um acontecimento no dia em que aconteceu."""
    created_at = datetime.now(settings.tzinfo).isoformat()
    dia = happened_on.isoformat()
    with _cursor() as cur:
        cur.execute(
            "INSERT INTO moments (user_id, content, happened_on, created_at) VALUES (?, ?, ?, ?)",
            (user_id, content.strip(), dia, created_at),
        )
        return {
            "id": int(cur.lastrowid),
            "user_id": user_id,
            "content": content.strip(),
            "happened_on": dia,
            "created_at": created_at,
        }


def get_moments_between(
    user_id: int, start_day: date, end_day: date, limit: int = 100
) -> list[dict[str, Any]]:
    """Acontecimentos entre dois dias, ambos incluídos, do mais recente ao mais antigo."""
    with _cursor() as cur:
        cur.execute(
            """
            SELECT * FROM moments
            WHERE user_id = ? AND happened_on >= ? AND happened_on <= ?
            ORDER BY happened_on DESC, id ASC
            LIMIT ?
            """,
            (user_id, start_day.isoformat(), end_day.isoformat(), limit),
        )
        return [_row_to_dict(row) for row in cur.fetchall()]


def list_moments(user_id: int, limit: int = 30) -> list[dict[str, Any]]:
    """Os acontecimentos mais recentes, do mais recente ao mais antigo."""
    with _cursor() as cur:
        cur.execute(
            """
            SELECT * FROM moments WHERE user_id = ?
            ORDER BY happened_on DESC, id ASC LIMIT ?
            """,
            (user_id, limit),
        )
        return [_row_to_dict(row) for row in cur.fetchall()]


def search_moments_by_text(user_id: int, text: str, limit: int = 30) -> list[dict[str, Any]]:
    with _cursor() as cur:
        cur.execute(
            """
            SELECT * FROM moments
            WHERE user_id = ? AND lower(content) LIKE ?
            ORDER BY happened_on DESC, id ASC
            LIMIT ?
            """,
            (user_id, f"%{text.lower().strip()}%", limit),
        )
        return [_row_to_dict(row) for row in cur.fetchall()]


def update_moment(
    user_id: int,
    moment_id: int,
    content: Optional[str] = None,
    happened_on: Optional[date] = None,
) -> bool:
    """Corrige o texto e/ou o dia de um acontecimento. True se algo mudou."""
    campos: list[str] = []
    valores: list[Any] = []
    if content is not None:
        campos.append("content = ?")
        valores.append(content.strip())
    if happened_on is not None:
        campos.append("happened_on = ?")
        valores.append(happened_on.isoformat())
    if not campos:
        return False

    valores.extend([moment_id, user_id])
    with _cursor() as cur:
        cur.execute(
            f"UPDATE moments SET {', '.join(campos)} WHERE id = ? AND user_id = ?",
            valores,
        )
        return cur.rowcount > 0


def moment_belongs_to(user_id: int, moment_id: int) -> bool:
    """True se o acontecimento existir e for mesmo desta pessoa."""
    with _cursor() as cur:
        cur.execute("SELECT 1 FROM moments WHERE id = ? AND user_id = ?", (moment_id, user_id))
        return cur.fetchone() is not None


def delete_moment(user_id: int, moment_id: int) -> bool:
    with _cursor() as cur:
        cur.execute("DELETE FROM moments WHERE id = ? AND user_id = ?", (moment_id, user_id))
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


def delete_summaries(user_id: int) -> int:
    """Apaga toda a memória de longo prazo de um utilizador. Devolve quantos."""
    with _cursor() as cur:
        cur.execute("DELETE FROM summaries WHERE user_id = ?", (user_id,))
        return cur.rowcount


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
# Controlo de acesso
# ---------------------------------------------------------------------------
def grant_access(user_id: int, label: str = "", is_owner: bool = False) -> None:
    """Autoriza um utilizador a falar com o bot.

    A etiqueta é escrita por quem convida e é mostrada a outras pessoas no
    `/who`, por isso é cortada aqui — o escape de Markdown é feito na
    apresentação, em `bot.py`.
    """
    with _cursor() as cur:
        cur.execute(
            """
            INSERT INTO access (user_id, label, is_owner, granted_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT (user_id) DO UPDATE SET label = excluded.label
            """,
            (user_id, safety.limitar(label, safety.MAX_ETIQUETA), 1 if is_owner else 0,
             datetime.now(settings.tzinfo).isoformat()),
        )


def revoke_access(user_id: int) -> bool:
    """Retira a autorização a um utilizador. O dono não pode ser retirado.

    Os lembretes por disparar dessa pessoa são marcados como disparados: tirar
    o acesso tem de calar o bot para ela, e não apenas impedi-la de escrever.
    Quem chamar deve cancelar também os jobs no scheduler.
    """
    with _cursor() as cur:
        cur.execute("DELETE FROM access WHERE user_id = ? AND is_owner = 0", (user_id,))
        if cur.rowcount == 0:
            return False
        cur.execute("UPDATE reminders SET fired = 1 WHERE user_id = ? AND fired = 0", (user_id,))
        return True


def is_owner(user_id: int) -> bool:
    """True se este utilizador for o dono registado na base de dados."""
    with _cursor() as cur:
        cur.execute("SELECT is_owner FROM access WHERE user_id = ?", (user_id,))
        row = cur.fetchone()
        return bool(row and row["is_owner"])


def has_owner() -> bool:
    """True se já houver um dono definido."""
    with _cursor() as cur:
        cur.execute("SELECT 1 FROM access WHERE is_owner = 1 LIMIT 1")
        return cur.fetchone() is not None


def pending_reminder_ids(user_id: int) -> list[int]:
    """Ids dos lembretes por disparar de um utilizador (para cancelar os jobs)."""
    with _cursor() as cur:
        cur.execute("SELECT id FROM reminders WHERE user_id = ? AND fired = 0", (user_id,))
        return [int(row["id"]) for row in cur.fetchall()]


def list_access() -> list[dict[str, Any]]:
    """Todos os utilizadores autorizados, o dono primeiro."""
    with _cursor() as cur:
        cur.execute("SELECT * FROM access ORDER BY is_owner DESC, granted_at ASC")
        return [_row_to_dict(row) for row in cur.fetchall()]


def allowed_user_ids() -> set[int]:
    with _cursor() as cur:
        cur.execute("SELECT user_id FROM access")
        return {row["user_id"] for row in cur.fetchall()}


# ---------------------------------------------------------------------------
# Preferências
# ---------------------------------------------------------------------------
class TooManyPreferences(RuntimeError):
    """Levantada quando se tenta passar do número máximo de preferências."""


def count_preferences(user_id: int) -> int:
    with _cursor() as cur:
        cur.execute("SELECT COUNT(*) AS total FROM preferences WHERE user_id = ?", (user_id,))
        return int(cur.fetchone()["total"])


def set_preference(user_id: int, key: str, value: str) -> None:
    """Guarda uma preferência, respeitando os limites de número e tamanho.

    As preferências entram no prompt de *todas* as chamadas à API. Sem tecto,
    uma conversa podia enchê-las até o pedido ficar enorme — e caro — para
    sempre, porque elas ficam gravadas.
    """
    key = key.strip()[: settings.max_preference_length]
    value = value.strip()[: settings.max_preference_length]

    with _cursor() as cur:
        cur.execute(
            "SELECT 1 FROM preferences WHERE user_id = ? AND key = ?", (user_id, key)
        )
        ja_existe = cur.fetchone() is not None
        if not ja_existe:
            cur.execute(
                "SELECT COUNT(*) AS total FROM preferences WHERE user_id = ?", (user_id,)
            )
            if int(cur.fetchone()["total"]) >= settings.max_preferences:
                raise TooManyPreferences(
                    f"Já estão guardadas {settings.max_preferences} preferências."
                )
        cur.execute(
            """
            INSERT INTO preferences (user_id, key, value) VALUES (?, ?, ?)
            ON CONFLICT (user_id, key) DO UPDATE SET value = excluded.value
            """,
            (user_id, key, value),
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
