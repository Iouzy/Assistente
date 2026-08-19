"""Carregamento e validação da configuração da aplicação.

Todas as definições vêm de variáveis de ambiente, tipicamente carregadas do
ficheiro `.env` da pasta de dados (ver `.env.example` e `caminhos.py`).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv

import caminhos

# Pasta do código. Os caminhos por omissão são resolvidos a partir da pasta de
# dados e não da de trabalho: arrancar o bot de outro sítio criava, em
# silêncio, uma base de dados nova e vazia — ou seja, sem lista de acesso
# nenhuma.
PROJECT_ROOT = caminhos.RAIZ_CODIGO

# Onde ficam a base de dados, o `.env` e o registo. A correr a partir do
# código é a própria pasta do projeto (como sempre foi); no programa
# compilado é `%LOCALAPPDATA%\Assistente` — ver `caminhos.py`.
DATA_DIR = caminhos.PASTA_DADOS

# Carrega o .env para o ambiente do processo. `override=False` garante que
# variáveis já definidas no sistema (ex.: em produção) têm prioridade.
load_dotenv(caminhos.FICHEIRO_ENV, override=False)


def _resolve(caminho: str) -> str:
    """Torna um caminho relativo absoluto, ancorando-o na pasta de dados."""
    return caminhos.resolver(caminho)


class ConfigError(RuntimeError):
    """Levantada quando a configuração é inválida ou está incompleta."""


def _get_str(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _get_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"A variável {name} tem de ser um número inteiro (valor: {raw!r}).") from exc


def _get_user_ids(name: str) -> frozenset[int]:
    """Lê uma lista de ids numéricos do Telegram separados por vírgulas."""
    raw = os.getenv(name, "").strip()
    if not raw:
        return frozenset()

    ids: set[int] = set()
    for pedaco in raw.replace(";", ",").split(","):
        pedaco = pedaco.strip()
        if not pedaco:
            continue
        try:
            ids.add(int(pedaco))
        except ValueError as exc:
            raise ConfigError(
                f"{name} tem de ser uma lista de números separados por vírgulas "
                f"(valor inválido: {pedaco!r}). O id aparece nos registos quando "
                "alguém fala com o bot."
            ) from exc
    return frozenset(ids)


@dataclass(frozen=True)
class Settings:
    """Configuração imutável da aplicação."""

    # --- Credenciais obrigatórias ---
    telegram_token: str
    deepseek_api_key: str

    # --- DeepSeek (API compatível com OpenAI) ---
    deepseek_base_url: str
    deepseek_model: str

    # --- Armazenamento ---
    database_path: str

    # --- Comportamento ---
    timezone: str
    max_history_messages: int
    history_keep_messages: int
    idle_flush_minutes: int
    event_reminder_lead_minutes: int
    late_reminder_grace_minutes: int
    max_tool_iterations: int
    connect_timeout: float
    read_timeout: float
    log_level: str
    log_file: str
    log_messages: bool
    allowed_user_ids: frozenset[int]
    max_preferences: int
    max_preference_length: int

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            telegram_token=_get_str("TELEGRAM_TOKEN"),
            deepseek_api_key=_get_str("DEEPSEEK_API_KEY"),
            deepseek_base_url=_get_str("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
            deepseek_model=_get_str("DEEPSEEK_MODEL", "deepseek-chat"),
            database_path=_resolve(_get_str("DATABASE_PATH", "assistente.db")),
            timezone=_get_str("TIMEZONE", "Europe/Lisbon"),
            # Nº máximo de mensagens mantidas em memória antes de resumir.
            # Cada mensagem é reenviada em todas as chamadas à API, por isso
            # este valor é o principal regulador do custo por conversa.
            max_history_messages=_get_int("MAX_HISTORY_MESSAGES", 12),
            # Quantas mensagens recentes ficam depois de um resumo.
            history_keep_messages=_get_int("HISTORY_KEEP_MESSAGES", 6),
            # Minutos de silêncio ao fim dos quais uma conversa é resumida e
            # arrumada, para não se perder se o processo morrer. 0 desliga.
            idle_flush_minutes=_get_int("IDLE_FLUSH_MINUTES", 30),
            # Minutos de antecedência do lembrete automático de um evento.
            event_reminder_lead_minutes=_get_int("EVENT_REMINDER_LEAD_MINUTES", 15),
            # Lembretes que expiraram enquanto o bot esteve offline ainda são
            # enviados (com aviso de atraso) dentro desta janela.
            late_reminder_grace_minutes=_get_int("LATE_REMINDER_GRACE_MINUTES", 120),
            # Rondas de tool calling permitidas por mensagem (trava ciclos).
            max_tool_iterations=_get_int("MAX_TOOL_ITERATIONS", 5),
            # Tempos-limite de rede em segundos. Os 5 segundos por omissão do
            # python-telegram-bot são curtos para ligações lentas ou filtradas.
            connect_timeout=float(_get_int("CONNECT_TIMEOUT", 20)),
            read_timeout=float(_get_int("READ_TIMEOUT", 30)),
            log_level=_get_str("LOG_LEVEL", "INFO").upper(),
            # Ficheiro de registo. Indispensável quando o bot corre sem janela:
            # é a única forma de ver o que se passou.
            log_file=_resolve(_get_str("LOG_FILE", "")),
            # Escrever no registo o texto das mensagens e das notas. Desligado
            # por omissão: o registo é um ficheiro em claro e as notas servem
            # justamente para guardar coisas como códigos e palavras-passe.
            log_messages=_get_str("LOG_MESSAGES", "").lower() in {"1", "true", "sim", "yes"},
            # Quem pode falar com o bot. Ver `validate` e o porteiro em bot.py:
            # se isto estiver vazio, manda a tabela `access` da base de dados.
            allowed_user_ids=_get_user_ids("ALLOWED_USER_IDS"),
            # Tectos das preferências: elas são reenviadas em todas as chamadas
            # à API, por isso sem limite uma conversa podia inchar o prompt
            # (e a factura) para sempre.
            max_preferences=_get_int("MAX_PREFERENCES", 32),
            max_preference_length=_get_int("MAX_PREFERENCE_LENGTH", 200),
        )

    @property
    def tzinfo(self) -> ZoneInfo:
        """Fuso horário usado para interpretar e apresentar datas."""
        return ZoneInfo(self.timezone)

    def validate(self) -> None:
        """Valida a configuração, levantando `ConfigError` se algo faltar."""
        missing = [
            name
            for name, value in (
                ("TELEGRAM_TOKEN", self.telegram_token),
                ("DEEPSEEK_API_KEY", self.deepseek_api_key),
            )
            if not value
        ]
        if missing:
            raise ConfigError(
                "Faltam variáveis de ambiente obrigatórias: "
                + ", ".join(missing)
                + ". Copie o ficheiro .env.example para .env e preencha-o."
            )

        # Erros de cópia no token são muito frequentes e o erro que o Telegram
        # devolve mais tarde é críptico. Validamos aqui o que é inequívoco.
        if any(char.isspace() for char in self.telegram_token):
            raise ConfigError(
                "O TELEGRAM_TOKEN tem espaços ou quebras de linha. "
                "Um token do Telegram é uma única palavra, sem espaços. "
                "Verifique a linha TELEGRAM_TOKEN= no ficheiro .env."
            )
        if ":" not in self.telegram_token:
            raise ConfigError(
                "O TELEGRAM_TOKEN parece incompleto: falta a parte numérica antes "
                "dos dois pontos. O formato correcto é "
                "TELEGRAM_TOKEN=1234567890:AAH... — copie o token inteiro que o "
                "@BotFather enviou."
            )
        if not self.deepseek_api_key.startswith("sk-"):
            raise ConfigError(
                "A DEEPSEEK_API_KEY não começa por 'sk-'. Confirme que copiou a "
                "chave inteira da consola da DeepSeek."
            )

        try:
            _ = self.tzinfo
        except ZoneInfoNotFoundError as exc:
            raise ConfigError(
                f"O fuso horário {self.timezone!r} não existe. "
                "Use um identificador IANA (ex.: Europe/Lisbon). "
                "Em Windows poderá ser necessário instalar o pacote `tzdata`."
            ) from exc

        if self.history_keep_messages >= self.max_history_messages:
            raise ConfigError(
                "HISTORY_KEEP_MESSAGES tem de ser menor do que MAX_HISTORY_MESSAGES."
            )
        if self.max_tool_iterations < 1:
            raise ConfigError("MAX_TOOL_ITERATIONS tem de ser pelo menos 1.")
        if self.max_preferences < 1:
            raise ConfigError("MAX_PREFERENCES tem de ser pelo menos 1.")
        if self.max_preference_length < 1:
            raise ConfigError("MAX_PREFERENCE_LENGTH tem de ser pelo menos 1.")


# Instância única partilhada por toda a aplicação.
settings = Settings.from_env()
