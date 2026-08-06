# 🤖 Assistente Pessoal para Telegram

Um assistente pessoal completo que vive no Telegram e gere a tua agenda,
lembretes e notas — com memória de longo prazo.

---

## 1. Visão geral

Este projeto é um bot de Telegram que funciona como secretário pessoal. Em vez
de comandos rígidos, falas com ele em linguagem natural e é o modelo
**DeepSeek** que decide, através de *function calling*, quais as ferramentas a
executar.

```
Tu:  dentist tomorrow at 3pm
Bot: ✅ Booked!
     🗓️ Dentist
     Friday, 7 August 2026 at 15:00
     ⏰ I'll nudge you at 14:45.

Tu:  actually push it to 4
Bot: ✅ Moved to Friday, 7 August 2026 at 16:00 — alert now at 15:45.
```

> **Língua:** o bot fala **inglês**, mesmo quando lhe escreves em português —
> percebe as duas. Foi uma escolha de custo: o inglês gasta menos tokens, e o
> catálogo de ferramentas é reenviado em todas as chamadas.
>
> Manter isto exige mais do que uma linha no prompt, porque o instinto do
> modelo é responder na língua em que lhe falam. A regra está no topo da
> persona **e** repetida num `[answer in English]` colado a cada mensagem — a
> posição mais forte do prompt, por ~6 tokens. Para o passar a português,
> reescreve `_PERSONA` e essa etiqueta em `llm.py`.

Ao contrário de um simples *chatbot*, o assistente:

* **guarda** eventos, notas e lembretes numa base de dados SQLite persistente;
* **avisa-te** a horas, mesmo que o bot tenha sido reiniciado entretanto;
* **recorda-se** de ti — o que foi dito é condensado num resumo que acompanha
  todos os turnos seguintes.

---

## 2. Funcionalidades

### Agenda
- Compromissos a partir de linguagem natural (`«friday at 9:30»`,
  `«in two weeks»`, `«sexta às 9h30»` — o português também é entendido).
- **Alerta automático 15 minutos antes** de cada compromisso (configurável).
- **Remarcar e renomear**: «push the dentist to 4pm» move o evento *e* o alerta.
- **Apagar** eventos, notas ou alertas a pedido.
- Consulta por dia, por palavra-chave ou dos próximos compromissos.

### Lembretes
- Alertas pontuais independentes da agenda (`«remind me to call Ana at 6pm»`).
- Entregues em mensagem privada a quem os criou.
- **Sobrevivem a reinícios**: no arranque são reagendados a partir da base de dados.
- Os que expiraram com o bot offline são entregues à mesma (dentro de uma janela
  de tolerância) com aviso de atraso.

### Notas e preferências
- Guarda qualquer informação com data/hora e procura por texto.
- **Preferências duradouras**: «call me Mike», «no emoji please» ficam gravadas
  e entram no contexto de todas as conversas seguintes.

### Memória
- **Curto prazo:** as últimas 12 mensagens, em RAM.
- **Longo prazo:** o modelo resume as mensagens antigas e o resumo fica em SQLite.
- **Nada se perde:** conversas curtas que nunca atingem o limite de resumo são
  arrumadas na mesma — ao fim de 30 minutos de silêncio e no encerramento do bot.
- A agenda do dia entra no contexto automaticamente.

### Botões
Um menu fixo por cima da caixa de texto com as consultas mais frequentes.
**Cada toque responde direto da base de dados, sem uma única chamada à API.**

### Acesso
- **O bot fecha-se sozinho:** a primeira pessoa que lhe escrever fica registada
  como dona, e mais ninguém consegue falar com ele.
- **Partilha por comando:** `/allow <id>` e `/revoke <id>`, sem editar ficheiros
  nem reiniciar — dá para modo família.
- **Ou lista fixa** em `ALLOWED_USER_IDS`, para quem prefere a configuração no
  ficheiro.
- Os dados estão sempre isolados por utilizador: cada pessoa vê só a sua agenda.

### Robustez
- Base de dados protegida por lock, segura entre a thread do bot e a do scheduler.
- Falhas da API (rede, quota, autenticação) traduzidas em mensagens compreensíveis.
- Credenciais mal copiadas detetadas no arranque, com indicação do que corrigir.
- Respostas longas partidas automaticamente; Markdown inválido cai para texto simples.

### Comandos

| Comando | O que faz |
|---|---|
| `/start` | Boas-vindas e menu de botões |
| `/today` | Compromissos do dia |
| `/agenda` | Próximos compromissos |
| `/notes` | Notas mais recentes |
| `/reminders` | Alertas por disparar |
| `/forget` | Arruma e limpa a memória de curto prazo |
| `/forget all` | Apaga também a memória de longo prazo |
| `/who` | O teu id do Telegram e quem tem acesso |
| `/allow <id>` | Dá acesso a outra pessoa |
| `/revoke <id>` | Retira o acesso |
| `/help` | Ajuda |

Os nomes portugueses (`/hoje`, `/notas`, `/lembretes`, `/esquecer`, `/ajuda`)
continuam a funcionar como aliases.

---

## 3. Instalação e configuração

### 3.1. Requisitos

* Python **3.10 ou superior**
* Uma conta de Telegram
* Uma conta na [plataforma DeepSeek](https://platform.deepseek.com/)

### 3.2. Criar o bot no Telegram (BotFather)

1. No Telegram, abre uma conversa com [**@BotFather**](https://t.me/BotFather).
2. Envia `/newbot`.
3. Escolhe um **nome** (ex.: `O Meu Assistente`).
4. Escolhe um **username** terminado em `bot`.
5. O BotFather devolve um token com este aspeto:
   `8123456789:AAH8s-EXEMPLO-de-token`. **Copia-o inteiro**, incluindo o número
   antes dos dois pontos — é o `TELEGRAM_TOKEN`.

> ⚠️ O token dá controlo total sobre o bot. Nunca o publiques nem o commites.
> Se escapar, `/token` no @BotFather gera outro e invalida o antigo.

### 3.3. Obter a chave da API DeepSeek

1. Cria conta em <https://platform.deepseek.com/>.
2. Vai a **API Keys → Create new API key**.
3. Copia a chave (`sk-...`) — só é mostrada uma vez.
4. Carrega saldo em **Top up** (ver secção 7: 5 € duram muito tempo).

### 3.4. Instalar

```bash
git clone <url-do-repositorio>
cd Assistente

python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

pip install -r requirements.txt
cp .env.example .env             # Windows: copy .env.example .env
```

Edita o `.env` e preenche as duas variáveis obrigatórias:

```ini
TELEGRAM_TOKEN=8123456789:AAH8s-o-teu-token-real
DEEPSEEK_API_KEY=sk-a-tua-chave-real
```

As restantes são opcionais e estão documentadas no `.env.example`.

### 3.4.1. Quem pode falar com o bot

**Um bot de Telegram é público.** Qualquer pessoa que descubra o username pode
escrever-lhe. Os dados estão isolados por utilizador — um estranho nunca veria
a tua agenda, teria um assistente vazio só dele — mas **cada mensagem dele
gastaria o saldo da tua conta DeepSeek**.

Há dois modos, e o primeiro não exige configuração nenhuma.

#### Modo automático (por omissão)

Deixa `ALLOWED_USER_IDS` vazio. **A primeira pessoa que escrever ao bot fica
registada como dona** e o bot fecha-se sozinho:

```
🔒 This assistant is now yours.
You're registered as the owner (id 123456789) and nobody else can talk to me.
```

Daí em diante geres tudo pelo Telegram:

| Comando | O que faz |
|---|---|
| `/who` | Mostra o teu id e a lista de quem tem acesso |
| `/allow <id> [nome]` | Deixa entrar mais alguém |
| `/revoke <id>` | Retira o acesso (o dono não pode ser retirado) |

> ⚠️ Escreve ao bot **assim que o arrancares pela primeira vez**. Enquanto
> ninguém o reclamar, quem escrever primeiro fica dono.

**Para dar acesso a outra pessoa:** pede-lhe que te diga o id dela (o Telegram
mostra-o em bots como o @userinfobot, ou aparece nos teus registos se ela
tentar escrever ao teu bot) e faz `/allow 987654321 Ana`.

#### Modo fixo (`.env`)

Preencher a variável fixa a lista e desliga o `/allow` e o `/revoke`:

```ini
ALLOWED_USER_IDS=123456789,987654321
```

Faz mais sentido num servidor, onde se quer a configuração no ficheiro e não
numa conversa. Para saberes o teu id, envia `/who` ao bot.

Em qualquer dos modos, quem não estiver na lista recebe uma recusa educada e
nada mais acontece — **nem uma chamada à API**.

### 3.5. Executar

```bash
python main.py
```

```
INFO | database  | Base de dados pronta em assistente.db
INFO | scheduler | Scheduler iniciado (fuso Europe/Lisbon).
INFO | main      | Assistente online como @o_teu_bot.
```

Abre a conversa com o bot e envia `/start`. Para parar, `Ctrl+C` — a memória em
RAM é resumida e gravada antes de encerrar.

### 3.6. Testes (opcional)

Dois conjuntos de testes que **não gastam um único token da API** — o cliente
DeepSeek é substituído por um duplo de teste:

```bash
python tests/test_tools.py   # datas, as 10 ferramentas, BD, lembretes reais
python tests/test_llm.py     # tool calling, cache, memória, ponte scheduler↔asyncio
```

São 115 verificações e correm em cerca de 30 segundos (esperam pelo disparo
real de lembretes).

Há um terceiro, que **fala com a API a sério** — é a única forma de saber se as
descrições das ferramentas são claras o suficiente para o modelo escolher bem:

```bash
python tests/test_tool_choice.py
```

Manda 26 frases-tipo e mostra que ferramenta o modelo atribuiu a cada uma. É um
ensaio a seco (nada é executado nem gravado na base de dados real) e custa
poucos cêntimos. Vale a pena correr sempre que se mexer nas descrições em
`TOOL_SCHEMAS`.

### 3.7. Correr em segundo plano no Windows

| Ficheiro | Para quê |
|---|---|
| **`windows/painel.vbs`** | **Painel de controlo: ligar, parar, ver a consola, actualizar** |
| `windows/iniciar_bot.bat` | Arranca com janela visível — bom para testar |
| `windows/iniciar_oculto.vbs` | Arranca **sem janela**, via `pythonw.exe` |
| `windows/parar_bot.bat` | Pára o bot que corre sem janela |

**O caminho mais simples** é fazer um atalho para `windows/painel.vbs` no
Ambiente de Trabalho: uma janela com estado, botões Ligar/Parar, a consola do
bot ao vivo e um botão que faz `git pull`.

O botão **Parar** não mata o processo: cria o ficheiro `.stop-assistente`, que
o `main.py` vigia, e o bot encerra ordenadamente — a memória de curto prazo é
gravada antes de sair. Só ao fim de 30 segundos sem obedecer é que é terminado
à força.

O botão **Actualizar** faz `git pull` e, comparando os ficheiros entre commits,
reinstala as dependências se o `requirements.txt` mudar e propõe reabrir-se se
o próprio painel tiver sido actualizado (o código em memória é o do arranque).

**Arrancar sozinho ao iniciar sessão:** `Windows`+`R` → `shell:startup` →
copiar para lá um **atalho** para `iniciar_oculto.vbs`.

Sem janela não há registos no ecrã, por isso o `.vbs` define `LOG_FILE` para
`assistente.log`. É aí que se vê o que aconteceu (`type assistente.log`).

| Ação | O bot… |
|---|---|
| Bloquear o ecrã (`Windows`+`L`) | ✅ continua |
| Fechar a janela do cmd (se arrancou oculto) | ✅ continua |
| **Suspender ou hibernar** | ❌ pára |
| Terminar sessão / reiniciar / desligar | ❌ pára |

Para não suspender: **Definições → Sistema → Energia → Suspender: Nunca**, e
**Painel de Controlo → Opções de Energia → ao fechar a tampa: Não fazer nada**.

### 3.8. Deixar a correr sempre num servidor Linux (opcional)

`/etc/systemd/system/assistente.service`:

```ini
[Unit]
Description=Assistente Pessoal Telegram
After=network-online.target

[Service]
Type=simple
User=SEU_UTILIZADOR
WorkingDirectory=/caminho/para/Assistente
ExecStart=/caminho/para/Assistente/.venv/bin/python main.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now assistente
sudo journalctl -u assistente -f
```

---

## 4. Exemplos de utilização

| # | O que escreves | O que acontece | Resposta típica |
|---|---|---|---|
| 1 | «dentist tomorrow at 3pm» | `add_event` grava e agenda o aviso para as 14:45 | ✅ Booked! 🗓️ Dentist — Friday, 7 August 2026 at 15:00. I'll nudge you at 14:45. |
| 2 | «what's on today?» | `get_current_datetime` + `search_events` | 🗓️ Two things today: 10:00 team meeting, 15:00 dentist. |
| 3 | «remind me to take the pill at 9» | `set_reminder` agenda para as 09:00 | ⏰ Done — I'll ping you at 09:00. |
| 4 | «note: the alarm code is 4471» | `save_note` grava com data/hora | 📝 Noted — “the alarm code is 4471”. |
| 5 | «what was the alarm code?» | `search_notes` | 📝 You saved: “the alarm code is 4471”, on 06/08/2026. |
| 6 | **«push the dentist to 4»** | `search_events` + `update_event` | ✅ Moved to Friday at 16:00 — alert now at 15:45. |
| 7 | **«cancel the dentist»** | `search_events` + `delete_item` | ✅ Deleted. The alert is off too. |
| 8 | **«call me Mike from now on»** | `set_preference` | ✅ Got it, Mike. |
| 9 | «book dinner» | Falta a data — pergunta em vez de adivinhar | Happy to — what day and time? |
| 10 | *(às 14:45, sem escreveres nada)* | O scheduler dispara sozinho | ⏰ **Reminder** — Dentist 🗓️ Friday, 7 August at 15:00 (in 15 minutes). |
| 11 | «I'm stressed about work» | Conversa normal, sem ferramentas | That sounds heavy… want to talk it through, or shall I help you plan the week? |
| 12 | *(dias depois)* «where do I work again?» | O resumo de memória entra no contexto | In Aveiro. 🙂 |
| 13 | *(toque no botão 📅 Today)* | Consulta direta à base de dados | A lista do dia — **sem chamar a API** |

---

## 5. Como funciona

### 5.1. Fluxo de uma mensagem

```
   Telegram
      │  (mensagem de texto)
      ▼
  bot.py ─── botão do menu? ──sim──► resposta direta da BD (custo zero)
      │ não
      ▼
  llm.py ─── monta o pedido:
      │        [sistema : persona — SEMPRE igual                    ]
      │        [histórico: até 12 mensagens — só acrescenta         ]
      │        [utilizador: [context: hora + agenda + memória] + msg]
      ▼
  DeepSeek API  (deepseek-chat, formato OpenAI, tools=[...])
      │
      ├── sem tool_calls ──────────────► resposta em texto ──► Telegram
      │
      └── com tool_calls
             │
             ▼
        tools.py ─── add_event / update_event / delete_item / ...
             │             ├──► database.py  (SQLite)
             │             └──► scheduler.py (APScheduler)
             ▼
        resultados JSON devolvidos ao modelo (role="tool")
             │
             ▼
        2.ª chamada ──► resposta final ──► Telegram
```

### 5.2. Porque é que a ordem do prompt importa

A DeepSeek desconta fortemente os **prefixos repetidos** entre chamadas
(*context caching*) — mas só enquanto o início do pedido for **exatamente
igual**. Por isso:

* o **prompt de sistema não contém nada que mude** — nem a hora, nem a agenda,
  nem o resumo de memória;
* tudo o que varia vai num bloco `[context: ...]` colado à **última** mensagem;
* o histórico é **só acrescentado**, nunca reescrito.

Resultado: cerca de **78% de cada pedido é prefixo estável** e elegível para
cache. Se a hora ficasse dentro do prompt de sistema — como estava — invalidava
tudo o que vinha a seguir, incluindo o histórico inteiro.

Para veres a taxa real de acerto, põe `LOG_LEVEL=DEBUG` no `.env`:

```
Tokens: 1766 entrada (1385 em cache, 78%), 143 saída
```

### 5.3. Lembretes e threads

O ponto mais delicado da arquitetura:

* o `python-telegram-bot` é **assíncrono** (um event loop);
* o `BackgroundScheduler` corre numa **thread separada**;
* `bot.send_message` é uma corotina, que uma thread normal não pode aguardar.

A ponte está em `main.py`: quando o scheduler dispara, chama um *notifier* que
usa `asyncio.run_coroutine_threadsafe(...)`. Sem isto, os lembretes falhariam em
silêncio.

A **base de dados é a fonte de verdade**, não o scheduler: cada lembrete é
gravado primeiro e só depois agendado; no arranque os jobs são reconstruídos.

### 5.4. Concorrência na base de dados

Uma única ligação com `check_same_thread=False`, protegida por um
`threading.RLock` que serializa todos os acessos, mais o modo `WAL`.

### 5.5. Memória em duas camadas

| Camada | Onde vive | Conteúdo |
|---|---|---|
| Curto prazo | RAM (`llm._histories`) | últimas ≤12 mensagens |
| Longo prazo | SQLite (`summaries`) | resumo dos factos importantes |

Três momentos geram resumo:

1. **Ao passar das 12 mensagens** — as antigas são condensadas e removidas.
2. **Ao fim de 30 minutos de silêncio** (`IDLE_FLUSH_MINUTES`) — a conversa é
   arrumada mesmo que nunca tenha chegado ao limite.
3. **No encerramento** — `flush_all()` grava tudo o que resta em RAM.

Os pontos 2 e 3 existem porque, sem eles, uma conversa curta vivia só em RAM e
desaparecia ao desligar o bot.

### 5.6. Ficheiros

| Ficheiro | Responsabilidade |
|---|---|
| `main.py` | Arranque, logging, ponte thread↔event loop, encerramento |
| `bot.py` | Handlers, comandos, botões, envio de mensagens |
| `llm.py` | Cliente DeepSeek, prompt, tool calling, memória |
| `tools.py` | As 10 ferramentas + esquemas OpenAI + datas naturais |
| `database.py` | Esquema SQLite e CRUD thread-safe |
| `scheduler.py` | Agendamento, disparo e restauro de lembretes |
| `config.py` | Variáveis de ambiente, validadas no arranque |

### 5.7. Ferramentas expostas ao modelo

| Ferramenta | Efeito |
|---|---|
| `get_current_datetime` | Data/hora atuais |
| `add_event` | Grava o evento **e** agenda o aviso prévio |
| `update_event` | Remarca ou renomeia, **reagendando o aviso** |
| `delete_item` | Apaga um evento, nota ou alerta |
| `search_events` | Procura por data, palavra-chave ou próximos |
| `save_note` / `search_notes` | Notas |
| `set_reminder` / `list_reminders` | Alertas pontuais |
| `set_preference` | Preferências duradouras de comportamento |

`summarize_memory` existe em `llm.py` mas **não** é exposta ao modelo: é
chamada internamente.

> As descrições das ferramentas são deliberadamente curtas e estão em inglês —
> viajam em todas as chamadas, por isso cada palavra é paga vezes sem conta.
> A distinção entre `add_event` e `set_reminder` é o único sítio onde vale a
> pena gastar palavras, porque é aí que o modelo se engana.

---

## 6. Melhorias futuras

- [ ] **Google Calendar / Outlook** — sincronização bidirecional.
- [ ] **Multi-utilizador (modo família)** — agendas partilhadas e permissões.
- [ ] **Pesquisa na web (DuckDuckGo)** — meteorologia, notícias, horários.
- [ ] **Gestão de tarefas (to-do)** — listas com prioridade e prazo.
- [ ] **Resumos diários/semanais** — a agenda do dia às 8:00, a semana ao domingo.
- [ ] **Integração de email** — transformar mensagens em eventos ou tarefas.
- [ ] **WhatsApp via Twilio** — o mesmo assistente noutro canal.
- [ ] **Mensagens de voz (Whisper)** — transcrever áudio e responder em voz.
- [ ] **Lembretes por localização** — «avisa-me quando chegar ao supermercado».
- [ ] **Registo de despesas** — «gastei 12 € no almoço», com relatórios mensais.
- [ ] **Perfis de personalidade configuráveis** — formal, informal, motivacional.
- [ ] **Cifra ponta-a-ponta da base de dados** — SQLCipher ou cifra por campo.
- [ ] **Eventos recorrentes** — «todas as terças às 18h».
- [ ] **Exportação para `.ics`** e cópias de segurança automáticas.

---

## 7. Estimativa de custo mensal

### 7.1. Como funciona o preço

A DeepSeek cobra **por token** (≈ 4 caracteres), com preços diferentes para
entrada e saída — e um desconto grande para **cache hits**, que é exatamente o
que a estrutura do prompt (secção 5.2) procura maximizar.

| Tipo | Preço aproximado |
|---|---|
| Entrada (cache miss) | ~$0,28 / M tokens |
| Entrada (**cache hit**) | ~$0,03 / M tokens |
| Saída | ~$0,42 / M tokens |

> ⚠️ Confirma os valores atuais em
> <https://api-docs.deepseek.com/quick_start/pricing> — a DeepSeek já ajustou
> preços mais do que uma vez e costuma ter descontos fora de horas.

### 7.2. Composição de um pedido

| Componente | Tokens | Estável? |
|---|---|---|
| Esquemas das 10 ferramentas | ~995 | ✅ sempre igual |
| Prompt de sistema | ~390 | ✅ sempre igual |
| Histórico (12 mensagens) | ~360 | ✅ só acrescenta |
| Bloco de contexto + mensagem | ~40 | ❌ muda sempre |
| **Total** | **~1 766** | **~78% em cache** |

### 7.3. Estimativa para uso pessoal

15 mensagens/dia (≈450/mês), ~1,6 chamadas por mensagem, ~150 tokens de saída:

| Item | Custo/mês |
|---|---|
| Entrada em cache (~1 385 × 720 chamadas) | ~$0,03 |
| Entrada sem cache | ~$0,08 |
| Saída | ~$0,03 |
| Resumos de memória | <$0,01 |
| **Total** | **≈ $0,15 ≈ €0,14** |

Antes das otimizações eram ~€0,38. **Um carregamento de 5 € dura anos.**

### 7.4. E se usar muito mais?

| Mensagens/dia | Custo estimado/mês |
|---|---|
| 5 | ~€0,05 |
| 15 | ~€0,14 |
| 30 | ~€0,28 |
| 150 | ~€1,40 |

Como reduzir ainda mais:

* **usar os botões** — respondem da base de dados, sem qualquer chamada à API;
* baixar `MAX_HISTORY_MESSAGES` (menos contexto por chamada);
* definir um limite de gastos na consola da DeepSeek.

> **Perspetiva:** um portátil ligado 24/7 gasta ~8,6 kWh/mês, à volta de **2 €**
> de eletricidade. O modelo custa menos de um décimo disso. Se o objetivo é
> poupar dinheiro a sério, o passo seguinte é um Raspberry Pi (~0,35 €/mês),
> não otimizar mais o prompt.

---

## 8. Resolução de problemas

| Sintoma | Causa provável | Solução |
|---|---|---|
| `Configuração inválida: Faltam variáveis...` | `.env` em falta | `cp .env.example .env` e preencher |
| `O TELEGRAM_TOKEN tem espaços` | Token mal copiado | Copiar a linha inteira, sem espaços |
| `O TELEGRAM_TOKEN parece incompleto` | Falta o número antes de `:` | Copiar o token todo do @BotFather |
| `O Telegram não respondeu a tempo` | Rede lenta, firewall, antivírus | Subir `CONNECT_TIMEOUT`/`READ_TIMEOUT`; testar `curl -m 20 https://api.telegram.org` |
| «My model credentials were rejected» | `DEEPSEEK_API_KEY` errada | Gerar nova chave |
| «I am getting rate limited» | Saldo esgotado | Carregar saldo |
| `RuntimeError: no current event loop` | `python-telegram-bot` antigo no Python 3.14 | `pip install -r requirements.txt` (exige ≥22.8) |
| Lembretes à hora errada | `TIMEZONE` incorreto | `TIMEZONE=Europe/Lisbon` |
| `ZoneInfoNotFoundError` | Base de fusos em falta | `pip install tzdata` |
| Bot não responde | Processo parado ou token errado | Ver `assistente.log` |
| «This is a private assistant…» | Outra pessoa reclamou o bot primeiro | `/who` do lado dela e `/allow <o teu id>`; ou apagar a tabela `access` |

---

## 9. Privacidade

Tudo é local, exceto o texto das conversas, que é enviado à API DeepSeek para
gerar as respostas. Define `ALLOWED_USER_IDS` (secção 3.4.1) — sem isso o bot
aceita mensagens de qualquer pessoa. A base de dados (`assistente.db`) fica na tua máquina e o
`.gitignore` já a exclui, tal como o `.env`. Se guardares dados sensíveis,
considera cifrar o disco — e vê a cifra da base de dados na secção 6.

---

## 10. Licença

MIT — usa, modifica e partilha à vontade.
