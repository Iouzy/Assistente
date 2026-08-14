# Melhorias — task file

> **Conceito.** A secção "Melhorias futuras" do `README.md` (§6) tinha catorze
> ideias em lista, sem especificação nenhuma. Este ficheiro pega nas quatro
> que são mecânicas — seguem um padrão que já existe no código, não pedem
> nenhuma credencial nova nem uma decisão de produto por resolver — e dá-lhes
> a forma de tarefa: exportação `.ics`, cópias de segurança automáticas,
> gestão de tarefas (to-do), e resumos diários/semanais agendados. As outras
> dez ficam em `docs/CONTEXT.md` §6, à espera de uma decisão do dono.
>
> Entrega-se como 4 tarefas autónomas (M1…M4). Cada tarefa é um PR.

## Como usar

> Faz a próxima em `docs/MELHORIAS.md`.

Faz a **primeira tarefa cujo Status é `pending`**, de cima para baixo, só
essa. Entrega pelo fluxo do `CLAUDE.md` (branch → testes locais → commit →
push → PR; o merge fica para o dono). Actualiza o Status da tarefa, o Log,
**e o `docs/CONTEXT.md`** no mesmo PR. Pára em vez de saltar se uma tarefa
estiver bloqueada por uma decisão que só o dono pode tomar.

**Cada resposta abre com a marca de progresso**, antes de qualquer chamada a
ferramentas:

```
**Feito:** —
**Agora:** M1 — exportação da agenda para .ics
**Falta:** M2…M4 (3)
```

---

## Guardrails

**`docs/GUARDRAILS.md` aplica-se por inteiro.** As que mais mordem aqui:

- §B — qualquer consulta a eventos, lembretes ou tarefas filtra sempre por
  `user_id`; as quatro tarefas deste ficheiro tocam dados pessoais.
- §D — uma dependência nova precisa de justificação no `Why:`; M1 e M2 estão
  especificadas para **não** precisarem de nenhuma.
- §F — não há CI; `Accept:` termina em "testes locais a passar", nunca "CI
  green".

**Extra, específico deste ficheiro:** nenhuma das quatro tarefas grava ou
lê nada de outro utilizador que não o que a pediu — nem para gerar um resumo
agendado, que corre sem pedido explícito de ninguém.

## Legenda de Status

`pending` · `in-progress (PR #n)` · `done (PR #n)` · `skipped (motivo)`

---

## Contexto partilhado

- Nenhuma das quatro tarefas muda o esquema de `events`, `reminders`,
  `notes` ou `moments` — M3 acrescenta uma tabela nova (`tasks`), as outras
  três só leem o que já existe.
- `scheduler.schedule_recurring(func, minutes, job_id)` já existe para jobs
  periódicos por intervalo (usado hoje por `flush_idle`). M4 precisa de um
  disparo a uma hora fixa do dia, não por intervalo — ver a tarefa para o
  porquê de precisar de uma função nova (`CronTrigger` do APScheduler, já
  uma dependência do projecto).
- Modelo sugerido: `sonnet` chega para as quatro — são extensões directas de
  padrões já existentes no código, não desenho de arquitectura nova.

## Decisões já tomadas — não reabrir

- **As dez ideias maiores da lista original ficam de fora deste ficheiro.**
  Ver `docs/CONTEXT.md` §6 para a lista e a razão de cada uma. Não as
  adicionar aqui "porque já agora" — cada uma pede uma decisão do dono que
  ainda não foi tomada.

---

## M1 · Exportar a agenda para `.ics` — Status: pending

**Depends on:** nothing

**Why:** é a forma mais directa de tirar os compromissos do bot para outro
calendário (telemóvel, Outlook, Google Agenda) — o utilizador continua a
gerir a agenda por linguagem natural aqui, mas quer poder vê-la noutro sítio.
Pedido explícito em `README.md` §6.

**Files to touch:**
- `bot.py` — novo comando `/export` (com alias `/exportar`), ao lado dos
  outros comandos directos (`/today`, `/agenda`) — resposta directa da base
  de dados, sem chamada à API, no mesmo espírito dos botões (ver `README.md`
  §2 "Botões").
- `database.py` — reaproveitar `get_upcoming_events` (ou uma variante sem
  limite) para obter todos os eventos futuros do utilizador.
- `icalendar_export.py` (novo) — função `eventos_para_ics(eventos: list[dict]) -> str`
  que produz o texto de um ficheiro `.ics` válido (cabeçalho `VCALENDAR`, um
  bloco `VEVENT` por evento com `UID`, `DTSTART`, `SUMMARY`). Só
  biblioteca-padrão — o formato é texto simples, não justifica uma
  dependência nova (ver `GUARDRAILS.md` §D).

**Out of scope:** importar um `.ics` para dentro do bot (só exportação);
eventos recorrentes (o modelo de dados não os tem); lembretes no ficheiro
exportado (só os eventos — um lembrete não é um evento de calendário).

**Never:** incluir eventos de outro `user_id` que não o de quem pediu; enviar
o ficheiro para outro chat que não o de quem pediu o `/export`.

**Accept:** `/export` numa conversa com eventos futuros devolve um ficheiro
`.ics` anexado que uma aplicação de calendário abre sem erro (ou que passa
uma verificação mínima de formato nos testes); `/export` sem eventos futuros
responde com uma mensagem em vez de um ficheiro vazio; `README.md` actualizado
com o novo comando na tabela da secção 2; testes locais a passar.

---

## M2 · Cópias de segurança automáticas da base de dados — Status: pending

**Depends on:** nothing

**Why:** hoje um disco que falha ou um `assistente.db` apagado por engano
perde tudo — agenda, notas, linha do tempo, preferências. Pedido explícito em
`README.md` §6.

**Files to touch:**
- `backup.py` (novo) — função `fazer_copia(destino_dir: str) -> pathlib.Path`
  que usa `sqlite3.Connection.backup()` (API da biblioteca-padrão, segura com
  o modo `WAL` já em uso — ver `CLAUDE.md` "Concorrência na base de dados")
  para produzir uma cópia consistente com nome `assistente-YYYYMMDD-HHMMSS.db`;
  e `podar_copias(destino_dir: str, manter: int)` que apaga as mais antigas
  além do número a manter.
- `config.py` — `backup_enabled` (bool, omissão `true`), `backup_interval_minutes`
  (omissão 1440 — uma vez por dia), `backup_dir` (omissão `backups/`,
  resolvido como os outros caminhos via `_resolve`), `backup_keep` (omissão 7).
- `scheduler.py` — registar o job com o `schedule_recurring` já existente.
- `main.py` — chamar o registo do job no arranque, só se `backup_enabled`.
- `.gitignore` — acrescentar `backups/`.

**Out of scope:** enviar a cópia para fora da máquina (nuvem, outro disco) —
ver `docs/CONTEXT.md` §6, é uma decisão de destino que o dono ainda não
tomou; cifrar a cópia (a base de dados original também não está cifrada).

**Never:** deixar a cópia bloquear a ligação principal por tempo apreciável —
é para isso que existe a API `.backup()` em vez de copiar o ficheiro à mão
enquanto o `WAL` pode estar a meio de uma escrita; escrever a cópia por cima
do `assistente.db` em uso.

**Accept:** com `backup_enabled=true`, um job periódico produz um ficheiro de
cópia válido (abre com `sqlite3.connect` e tem as tabelas esperadas) dentro
de `backup_dir`; correndo o job várias vezes com `backup_keep=N`, sobram no
máximo N cópias; ficheiros criados com as mesmas permissões restritas que
`assistente.db` (ver `GUARDRAILS.md` §C); testes locais a passar, incluindo
um teste novo que chama `fazer_copia`/`podar_copias` directamente contra uma
base de dados temporária.

---

## M3 · Gestão de tarefas (to-do) — Status: pending

**Depends on:** nothing

**Why:** hoje há eventos (com hora) e notas (sem estrutura); falta o meio
termo — uma lista do que fazer, sem hora marcada, que se risca ao ser feita.
Pedido explícito em `README.md` §6.

**Files to touch:**
- `database.py` — tabela nova `tasks` (id, user_id, chat_id, description,
  done, created_at), ao lado de `events`/`notes`/`moments` no `init_db`;
  funções `create_task`, `list_open_tasks`, `complete_task`, `delete_task`,
  `task_belongs_to` — mesmo padrão de posse e isolamento por `user_id` que
  `moments`/`notes` já seguem.
- `tools.py` — três ferramentas novas em `TOOL_SCHEMAS`: `add_task`,
  `list_tasks`, `complete_task`; e `delete_item` ganha `"task"` como um
  `kind` válido, ao lado de `"event"`/`"note"`/`"reminder"`/`"moment"`.
- `bot.py` — um botão/comando `/tasks` (alias `/tarefas`) que lista tarefas
  em aberto directo da base de dados, sem chamada à API — mesmo padrão de
  `/notes`.

**Out of scope:** prioridade ou prazo por tarefa (fica para uma iteração
seguinte, se vier a fazer falta); subtarefas ou listas dentro de listas;
tarefas partilhadas entre utilizadores.

**Never:** criar automaticamente um lembrete agendado para uma tarefa nova
sem o utilizador pedir explicitamente — uma tarefa e um lembrete são coisas
diferentes, e misturá-las por omissão surpreende quem só queria apontar algo
para não esquecer, não ser interrompido a uma hora que não escolheu.

**Accept:** "add buy milk to my tasks" grava uma tarefa e confirma; "what's
on my to-do list" lista as tarefas em aberto do próprio utilizador, nunca as
de outro; marcar uma como feita tira-a da lista; `/tasks` responde direto da
base de dados; `README.md` actualizado (nova secção "Tarefas" e a linha na
tabela de comandos); testes locais a passar, incluindo `tests/test_tools.py`
estendido com as três ferramentas novas.

---

## M4 · Resumos diários e semanais agendados — Status: pending

**Depends on:** nothing

**Why:** hoje o bot só fala quando lhe perguntam ou quando um lembrete
dispara — nunca avisa proactivamente "isto é o que tens hoje". Pedido
explícito em `README.md` §6.

**Files to touch:**
- `scheduler.py` — nova função `schedule_daily(func, hour, minute, job_id)`
  usando `apscheduler.triggers.cron.CronTrigger(hour=hour, minute=minute,
  timezone=settings.tzinfo)` — `schedule_recurring` existente é só por
  intervalo (`IntervalTrigger`) e não serve para "às 8:00 todos os dias";
  e `schedule_weekly(func, day_of_week, hour, minute, job_id)` com
  `CronTrigger(day_of_week=..., hour=..., minute=...)`.
- `config.py` — `daily_summary_enabled` (omissão `false` — proactivo demais
  para ligar sem o dono decidir), `daily_summary_hour`/`_minute` (omissão
  8:00), `weekly_summary_enabled` (omissão `false`), `weekly_summary_day`
  (omissão `sun`), `weekly_summary_hour`/`_minute` (omissão 9:00).
- `main.py` — registar os dois jobs no arranque, só se activados; cada job
  itera os utilizadores autorizados (`database.list_access`) e usa o
  *notifier* já existente (o mesmo que os lembretes usam, ver
  `CLAUDE.md` "Ponte thread → event loop") para mandar a cada um o seu
  próprio resumo — nunca o resumo de outra pessoa.
- `tools.py` ou um módulo novo — função que monta o texto do resumo diário
  (agenda de hoje) e semanal (agenda dos próximos 7 dias), reaproveitando
  `get_daily_context`/`get_events_between` já existentes.

**Out of scope:** personalizar o conteúdo do resumo por utilizador além da
hora (ligar/desligar cada um); incluir as tarefas de M3 no resumo (fica para
depois de M3 estar entregue, e é uma tarefa própria).

**Never:** mandar um resumo a quem não tem acesso, ou a quem desligou os
resumos; deixar um resumo semanal disparar mais de uma vez por semana se o
processo reiniciar perto da hora marcada (o `CronTrigger` com
`replace_existing=True` já evita isto — não reinventar o agendamento).

**Accept:** com `daily_summary_enabled=true`, um utilizador autorizado
recebe uma mensagem à hora configurada com a agenda do dia, sem ter escrito
nada; o mesmo para o semanal; ninguém sem acesso recebe nada; `README.md`
actualizado com as duas variáveis novas em `.env.example` documentadas;
testes locais a passar, incluindo um teste que chama a função de montagem do
resumo directamente (sem esperar pelo `CronTrigger` a sério).

---

## Sobras — pequenas demais para serem tarefa

Nenhuma ainda.

## Emendas a outros ficheiros

Nenhuma ainda.

---

## Order

M1, M2, M3 e M4 são independentes entre si — qualquer ordem serve. Sugestão:
M1 e M2 primeiro (mais mecânicas, sem tabela nova), depois M3, depois M4
(pode querer reaproveitar o texto de resumo de M3 mais tarde, mas não
depende dele).

---

## Log (uma linha por tarefa entregue: data · tarefa · PR · nota)
