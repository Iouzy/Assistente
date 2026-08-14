# CLAUDE.md

Guia para trabalhar neste repositório.

## O que é

**Assistente** é um assistente pessoal de Telegram, em Python, apoiado no
modelo **DeepSeek** (API compatível com OpenAI, `function calling`). Corre num
PC de casa (com painel de controlo próprio em `windows/`) ou num servidor
Linux. Sem conta própria além de Telegram + DeepSeek, sem analytics de
terceiros, sem venda de dados. Fala com quem escreve em linguagem natural e
decide, via *tool calling*, que ferramenta executar — agenda (`events`),
lembretes (`reminders`), notas (`notes`), uma linha do tempo (`moments`) e
preferências duradouras (`preferences`), com memória de curto e longo prazo.

O `README.md` é a referência completa para quem instala e usa o bot — secções,
arquitectura, custo, resolução de problemas. Este ficheiro é para quem
modifica o código.

## Como o trabalho acontece — `docs/`

Cada alteração de fundo entra através de um task file: uma especificação, um
PR, e uma linha de Log a explicar porquê foi construída assim. Quando pedirem
"faz a tarefa X" ou "faz a próxima pendente": lê esse ficheiro, faz **só**
aquela tarefa seguindo a sua especificação, entrega pelo fluxo abaixo, e
actualiza o Status da tarefa + o Log **e o `docs/CONTEXT.md`** no mesmo PR.

**Três ficheiros são o briefing completo de uma sessão fria** — este, depois:

- **`docs/GUARDRAILS.md`** — **vinculativo.** O que pode e não pode ser feito:
  identidade e âmbito, o modelo de acesso e posse dos dados, dados e
  privacidade, dependências, língua, fluxo de trabalho, decisões fechadas e a
  lista do que nunca fazer. Onde um task file discordar dele, ele vence.
- **`docs/CONTEXT.md`** — o estado do trabalho: o que já existe, o que está
  activo, o que foi de facto verificado (testes locais vs. uso real), e as
  perguntas em aberto para o dono.
- **o teu task file** — ver `docs/README.md` para o índice.

Mais um, quando uma tarefa precisar: **`docs/TASK_FILE_FORMAT.md`** — ler
antes de escrever um task file **novo**.

**Task files activos:**

- `docs/MELHORIAS.md` — um subconjunto pequeno e bem definido da lista
  "Melhorias futuras" do `README.md` (secção 6): exportação `.ics`, cópias de
  segurança da base de dados, gestão de tarefas (to-do), resumos
  diários/semanais. Os itens maiores dessa lista (Google Calendar, WhatsApp,
  voz, multi-utilizador, etc.) ficam de fora de propósito — precisam de uma
  decisão do dono antes de virarem tarefa; ver `docs/CONTEXT.md` §6.

## Arquitectura — o que não se percebe lendo um ficheiro só

**A ordem do prompt é uma decisão de custo, não de estilo** (`llm.py`). A
DeepSeek desconta prefixos repetidos entre chamadas, mas só enquanto o início
do pedido for byte a byte igual. Por isso tudo o que varia — data e hora,
agenda do dia, resumo da memória — vai colado à **última** mensagem, nunca no
prompt de sistema. Mexer nisto não parte testes; parte a factura (ver
`README.md` secção 5.2).

**Memória em duas camadas** (`llm.py`). Curto prazo em RAM (últimas
`MAX_HISTORY_MESSAGES`); ao crescer, as mais antigas são condensadas pelo
modelo num resumo na tabela `summaries`. `flush_idle()` periódico e
`flush_all()` no encerramento existem porque, sem eles, uma conversa curta
vivia só em RAM e desaparecia ao desligar o bot.

**A base de dados é a fonte de verdade dos lembretes, não o APScheduler**
(`scheduler.py`, `database.py`). Grava-se primeiro em `reminders`, agenda-se
depois; no arranque, `restore_pending_reminders()` reconstrói os jobs. Um
reinício não perde nada. `schedule_recurring(func, minutes, job_id)` já existe
para jobs periódicos genéricos (usado por qualquer resumo agendado).

**Ponte thread → event loop** (`main.py`, `build_notifier`). O `APScheduler`
corre numa thread própria e não pode aguardar corotinas; o envio no Telegram é
assíncrono. O scheduler nunca envia mensagens — chama um *notifier* que faz
`asyncio.run_coroutine_threadsafe` para o loop do `python-telegram-bot`.

**Controlo de acesso, três vias para a mesma lista** (`bot.py`,
`windows/acessos.py`). Ver `docs/GUARDRAILS.md` §B — é a secção de segurança
deste repositório e é vinculativa, não apenas informativa.

**Concorrência na base de dados** (`database.py`). Uma ligação com
`check_same_thread=False`, serializada por `RLock`, mais modo `WAL` — é também
o que deixa o painel escrever com o bot a correr. Datas em texto ISO-8601
**com fuso** (ver a limitação conhecida do horário de verão em `README.md`
§5.4.1 — afecta só a *ordem de listagem*, nunca a hora real do disparo).

**Saneamento de texto vindo de fora** (`safety.py`). Três fronteiras
diferentes, três tratamentos, não intermutáveis: `para_registo` (regista),
`neutralizar_markdown` (mostra a terceiros), `limpar_nome` (entra no prompt).
Ver `docs/GUARDRAILS.md` §B para o porquê de cada uma existir.

## Convenções

- **Língua: português europeu** em comentários, docstrings, documentação,
  mensagens de commit e descrições de PR. **Inglês** só no que viaja para o
  modelo (`TOOL_SCHEMAS`, chaves dos resultados das ferramentas) e no que o
  bot responde ao utilizador no Telegram — decisão de custo documentada em
  `README.md` §1 e implementada em `llm.py` (`_PERSONA` + a etiqueta
  `[answer in English]`). Ver `docs/GUARDRAILS.md` §E antes de mexer nisto.
- **Nomes de branch:** `claude/<descrição-curta>` — é o que todo o histórico
  deste repositório já usa.
- **Mensagens de commit:** assunto curto no presente («Painel actualiza-se a
  si próprio e às dependências»), corpo em parágrafos que explicam **porquê**,
  não o que o diff já mostra — incluindo o que foi rejeitado e porquê. É o
  mesmo padrão exigido do Log de um task file (`docs/TASK_FILE_FORMAT.md`).
- **Testes:** sem framework nem `pytest` — cada ficheiro em `tests/` é um
  programa que corre verificações e sai com código 1 se alguma falhar.

## Comandos

```bash
pip install -r requirements.txt
cp .env.example .env          # preencher TELEGRAM_TOKEN e DEEPSEEK_API_KEY
python main.py                # arranca o bot (Ctrl+C encerra ordenadamente)
```

```bash
python tests/test_tools.py     # datas, as ferramentas, BD, acesso, lembretes reais (~30 s)
python tests/test_llm.py       # tool calling, cache, memória, ponte scheduler↔asyncio
python tests/test_acessos.py   # permissões geridas pelo painel (não abre janelas)
python tests/test_seguranca.py # porteiro, posse dos dados, saneamento, limites
```

Os quatro substituem o cliente DeepSeek por um duplo e **não gastam um
token**; escrevem numa base de dados temporária, nunca em `assistente.db`.
Este conjunto é o portão — **não há CI configurado neste repositório**, por
isso "testes locais a passar" é literalmente a única verificação automática
que existe antes de um PR.

```bash
python tests/test_tool_choice.py   # ⚠ fala com a API a sério e custa cêntimos
```

Só se justifica depois de mexer nas descrições em `TOOL_SCHEMAS`.

## Fluxo de trabalho — como entregar alterações

Não há autorização permanente registada para comitar/entregar PRs sem
confirmação — ao contrário de outros repositórios do mesmo dono, este não tem
essa nota por escrito. Assumir o caminho mais conservador até isso mudar:

1. **Branch** a partir do branch de desenvolvimento actual (ver
   `git remote show origin` para o `HEAD branch` — nem sempre é `main`; à data
   desta escrita era `claude/telegram-assistant-bot-gc3k0b`).
2. Correr os quatro testes offline (secção **Comandos**) antes de comitar.
3. **Commit**, **push**, **abrir PR**.
4. Actualizar o Status da tarefa + o Log do task file **e `docs/CONTEXT.md`**
   no mesmo PR.
5. Deixar o merge para o dono, salvo instrução explícita em contrário.

**Nunca** deixar um commit preso num branch sem PR.
