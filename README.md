# 🤖 Assistente Pessoal para Telegram

Um assistente pessoal completo que vive no Telegram, fala português europeu e
gere a tua agenda, lembretes e notas — com memória de longo prazo.

---

## 1. Visão geral

Este projecto é um bot de Telegram que funciona como secretário pessoal. Em vez
de comandos rígidos, falas com ele em linguagem natural e é o modelo
**DeepSeek** que decide, através de *function calling*, quais as ferramentas a
executar.

```
Tu: amanhã às 15h tenho consulta no dentista
Bot: ✅ Ficou marcado!
     🗓️ Consulta no dentista
     sexta-feira, 7 de agosto de 2026 às 15:00
     ⏰ Aviso-te às 14:45.
```

Ao contrário de um simples *chatbot*, o assistente:

* **guarda** eventos, notas e lembretes numa base de dados SQLite persistente;
* **avisa-te** a horas, mesmo que o bot tenha sido reiniciado entretanto;
* **recorda-se** de ti — os factos importantes das conversas antigas são
  condensados num resumo que acompanha todos os turnos seguintes.

---

## 2. Funcionalidades

### Agenda
- Criação de compromissos a partir de linguagem natural (`«sexta às 9h30»`,
  `«dia 12 ao almoço»`, `«daqui a duas semanas»`).
- **Lembrete automático 15 minutos antes** de cada compromisso (configurável).
- Consulta por dia (`«o que tenho hoje?»`), por palavra-chave (`«quando é o
  dentista?»`) ou dos próximos eventos.
- Compromissos demasiado próximos recebem um aviso imediato em vez de nenhum.

### Lembretes
- Lembretes pontuais independentes da agenda (`«lembra-me de ligar à Ana às
  18:00»`, `«daqui a 20 minutos»`).
- Entregues em mensagem privada ao utilizador que os criou.
- **Sobrevivem a reinícios**: no arranque, todos os lembretes pendentes são
  reagendados a partir da base de dados.
- Lembretes que expiraram com o bot offline são entregues à mesma (dentro de uma
  janela de tolerância configurável) com um aviso de atraso.

### Notas
- Guarda qualquer informação com data/hora automática.
- Pesquisa por texto (`«o que sabes sobre o wi-fi?»`).

### Memória
- **Curto prazo:** as últimas 20 mensagens de cada utilizador, em RAM.
- **Longo prazo:** quando o histórico cresce, o próprio modelo resume as
  mensagens antigas e o resumo fica guardado em SQLite, sendo reinjectado no
  prompt de sistema de cada turno.
- A agenda do dia é injectada no contexto, pelo que o assistente sabe sempre o
  que tens marcado sem precisar de perguntar.
- Preferências por utilizador guardadas em base de dados.

### Conversa
- Responde exclusivamente em **português europeu (pt-PT)**.
- Proactivo: sugere criar lembretes ou notas quando faz sentido.
- Faz perguntas de esclarecimento quando falta informação (hora, dia, assunto).
- Confirma sempre o que ficou guardado, mostrando os dados registados.
- Conversa normal sobre qualquer tema — não é apenas um executor de comandos.

### Robustez
- Base de dados protegida por lock, segura entre a thread do bot e a do scheduler.
- Falhas da API DeepSeek (rede, quota, autenticação) são traduzidas em mensagens
  compreensíveis — o bot nunca vai abaixo.
- Respostas longas são partidas automaticamente; Markdown inválido faz *fallback*
  para texto simples.

### Comandos rápidos (opcionais, respondem sem gastar tokens)

| Comando | O que faz |
|---|---|
| `/start` | Mensagem de boas-vindas |
| `/hoje` | Compromissos do dia |
| `/agenda` | Próximos compromissos |
| `/notas` | Notas mais recentes |
| `/lembretes` | Lembretes por disparar |
| `/esquecer` | Limpa a memória de curto prazo |
| `/ajuda` | Ajuda |

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
4. Escolhe um **username** terminado em `bot` (ex.: `meu_assistente_pessoal_bot`).
5. O BotFather devolve um token com este aspecto:
   `123456789:AAH8s-EXEMPLO-de-token-do-BotFather`. **Guarda-o** — é o
   `TELEGRAM_TOKEN`.
6. (Opcional) `/setdescription` e `/setuserpic` para personalizar o bot.

> ⚠️ O token dá controlo total sobre o bot. Nunca o publiques nem o commites.

### 3.3. Obter a chave da API DeepSeek

1. Cria conta em <https://platform.deepseek.com/>.
2. Vai a **API Keys → Create new API key**.
3. Copia a chave (`sk-...`) — só é mostrada uma vez. É o `DEEPSEEK_API_KEY`.
4. Carrega alguns euros de saldo em **Top up** (ver a secção 7: para uso pessoal,
   5 € duram muitos meses).

### 3.4. Instalar

```bash
# 1. Obter o código
git clone <url-do-repositorio>
cd Assistente

# 2. Ambiente virtual (recomendado)
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

# 3. Dependências
pip install -r requirements.txt

# 4. Configuração
cp .env.example .env             # Windows: copy .env.example .env
```

Edita o `.env` e preenche as duas variáveis obrigatórias:

```ini
TELEGRAM_TOKEN=123456789:AAH8s-o-teu-token-real
DEEPSEEK_API_KEY=sk-a-tua-chave-real
```

As restantes variáveis são opcionais e estão documentadas no `.env.example`
(fuso horário, caminho da base de dados, tamanho da memória, antecedência dos
lembretes, nível de logging).

### 3.5. Executar

```bash
python main.py
```

Devias ver algo como:

```
2026-08-06 11:12:03 | INFO | database | Base de dados pronta em assistente.db
2026-08-06 11:12:03 | INFO | scheduler | Scheduler iniciado (fuso Europe/Lisbon).
2026-08-06 11:12:03 | INFO | scheduler | 0 lembrete(s) pendente(s) reagendado(s).
2026-08-06 11:12:04 | INFO | main | Assistente online como @meu_assistente_pessoal_bot.
```

Abre a conversa com o teu bot no Telegram e envia `/start`. Para parar, `Ctrl+C`.

### 3.6. Testes (opcional)

O repositório inclui dois conjuntos de testes que não gastam um único token da
API — o cliente DeepSeek é substituído por um duplo de teste:

```bash
python tests/test_tools.py   # datas em pt-PT, ferramentas, BD, lembretes reais
python tests/test_llm.py     # ciclo de tool calling, memória, ponte scheduler↔asyncio
```

Ambos usam uma base de dados temporária e imprimem um relatório linha a linha.
Correm em cerca de 20 segundos (esperam pelo disparo real de lembretes).

### 3.7. Deixar a correr sempre (opcional)

Num servidor Linux com systemd, cria `/etc/systemd/system/assistente.service`:

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

Como os lembretes são reagendados a partir da base de dados, reiniciar o serviço
não perde nada.

---

## 4. Exemplos de utilização

| # | O que escreves | O que acontece | Resposta típica |
|---|---|---|---|
| 1 | «Marca dentista amanhã às 15h» | `add_event` grava o evento e agenda o aviso para as 14:45 | ✅ Marcado! 🗓️ Dentista — sexta-feira, 7 de agosto de 2026 às 15:00. Aviso-te às 14:45. |
| 2 | «O que tenho hoje?» | `get_current_datetime` + `search_events("hoje")` | 🗓️ Hoje tens 2 compromissos: 10:00 — Reunião de equipa; 15:00 — Dentista. |
| 3 | «Lembra-me de tomar o comprimido às 9:00» | `set_reminder` agenda para as 09:00 (ou amanhã, se já passou) | ⏰ Combinado! Aviso-te às 09:00 para tomares o comprimido. |
| 4 | «Guarda que o código do alarme é 4471» | `save_note` grava a nota com data/hora | 📝 Apontado! «O código do alarme é 4471» — 06/08/2026 11:20. |
| 5 | «Qual era o código do alarme?» | `search_notes("alarme")` | 📝 Tens apontado: «O código do alarme é 4471», guardado a 06/08/2026. |
| 6 | «Quando é a consulta?» | `search_events("consulta")` | 🗓️ A consulta é na sexta-feira, 7 de agosto, às 15:00. |
| 7 | «Marca jantar» | Falta a data — o assistente pergunta em vez de adivinhar | Com todo o gosto! Para que dia e a que horas marco o jantar? |
| 8 | «Amanhã tenho de tratar dos impostos» | Reconhece a intenção e sugere agendar | Queres que marque isso para amanhã? A que horas te dá jeito? 🗓️ |
| 9 | *(às 14:45, sem escreveres nada)* | O scheduler dispara e envia mensagem privada | ⏰ **Lembrete de compromisso** — Dentista 🗓️ sexta-feira, 7 de agosto às 15:00 (faltam 15 minutos). |
| 10 | «Estou stressado com o trabalho» | Conversa normal, sem ferramentas | Compreendo... queres desabafar? Se quiseres, posso ajudar-te a organizar a semana. |
| 11 | «Chamo-me Miguel e trabalho em Aveiro» | Vai para o histórico e, mais tarde, para o resumo de memória | Muito prazer, Miguel! 👋 Fico a saber. |
| 12 | *(dias depois)* «Lembras-te onde trabalho?» | O resumo guardado é injectado no prompt de sistema | Claro — em Aveiro. 🙂 |

---

## 5. Como funciona

### 5.1. Fluxo de uma mensagem

```
   Telegram
      │  (mensagem de texto)
      ▼
  bot.py ─── constrói o ToolContext (user_id, chat_id, nome)
      │
      ▼
  llm.py ─── monta as mensagens:
      │        [sistema: persona + data/hora + agenda de hoje + resumo de memória]
      │        [histórico das últimas ≤20 mensagens]
      │        [mensagem actual]
      ▼
  DeepSeek API  (modelo deepseek-chat, formato OpenAI, tools=[...])
      │
      ├── sem tool_calls ──────────────► resposta em texto ──► Telegram
      │
      └── com tool_calls
             │
             ▼
        tools.py ─── executa add_event / search_events / save_note / ...
             │             │
             │             ├──► database.py  (SQLite: grava/lê)
             │             └──► scheduler.py (APScheduler: agenda o aviso)
             ▼
        resultados JSON devolvidos ao modelo (role="tool")
             │
             ▼
        2.ª chamada à DeepSeek ──► resposta final em texto ──► Telegram
```

O ciclo repete-se até o modelo responder sem pedir ferramentas (máximo de
`MAX_TOOL_ITERATIONS` rondas, para travar ciclos).

### 5.2. Lembretes e threads

Este é o ponto mais delicado da arquitectura:

* o `python-telegram-bot` v20 é **assíncrono** (um event loop);
* o `BackgroundScheduler` do APScheduler corre numa **thread separada**;
* `bot.send_message` é uma corotina, que uma thread normal não pode aguardar.

A ponte está em `main.py`: quando o scheduler dispara, chama um *notifier* que
usa `asyncio.run_coroutine_threadsafe(...)` para agendar o envio no event loop
do bot e aguarda o resultado. Sem isto, os lembretes falhariam em silêncio.

A **base de dados é a fonte de verdade**, não o scheduler. Cada lembrete é
primeiro gravado na tabela `reminders` e só depois agendado; no arranque,
`scheduler.restore_pending_reminders()` reconstrói todos os jobs.

### 5.3. Concorrência na base de dados

O bot e o scheduler escrevem na mesma base SQLite a partir de threads
diferentes. Usamos uma única ligação com `check_same_thread=False`, protegida
por um `threading.RLock` que serializa todos os acessos, mais o modo `WAL` para
reduzir bloqueios entre leituras e escritas.

### 5.4. Memória em duas camadas

| Camada | Onde vive | Conteúdo | Quando é usada |
|---|---|---|---|
| Curto prazo | RAM (`llm._histories`) | últimas ≤20 mensagens | em cada turno |
| Longo prazo | SQLite (`summaries`) | resumo dos factos importantes | injectado no prompt de sistema |

Quando o histórico passa das 20 mensagens, as mais antigas são enviadas ao
modelo com um pedido de resumo (`summarize_memory`, interno) e substituídas por
esse resumo — o que mantém o custo por mensagem estável ao longo do tempo.

### 5.5. Ficheiros

| Ficheiro | Responsabilidade |
|---|---|
| `main.py` | Arranque: logging, configuração, BD, aplicação Telegram, scheduler |
| `bot.py` | Handlers do Telegram, comandos, envio de mensagens |
| `llm.py` | Cliente DeepSeek, prompt de sistema, ciclo de tool calling, memória |
| `tools.py` | As 7 ferramentas + esquemas OpenAI + interpretação de datas em pt-PT |
| `database.py` | Esquema SQLite e operações CRUD thread-safe |
| `scheduler.py` | Agendamento, disparo e restauro de lembretes |
| `config.py` | Leitura e validação das variáveis de ambiente |

> Nota: `llm.py` não constava da especificação original de ficheiros, mas separar
> a camada do modelo da camada do Telegram mantém o `bot.py` legível e torna a
> lógica de conversa testável sem o Telegram à frente.

### 5.6. Ferramentas expostas ao modelo

| Ferramenta | Parâmetros | Efeito |
|---|---|---|
| `get_current_datetime` | — | Data/hora actuais no fuso configurado |
| `add_event` | `date`, `description` | Grava o evento **e** agenda o aviso prévio |
| `search_events` | `query` | Procura por data, por palavra-chave ou próximos |
| `save_note` | `content` | Grava uma nota com data/hora |
| `search_notes` | `query` | Procura nas notas |
| `set_reminder` | `message`, `time` | Agenda um aviso pontual |
| `list_reminders` | — | Lista os lembretes por disparar |

`summarize_memory` existe em `llm.py` mas **não** é exposta ao modelo: é
chamada internamente quando o histórico precisa de ser compactado.

---

## 6. Melhorias futuras

- [ ] **Google Calendar / Outlook** — sincronização bidireccional dos eventos.
- [ ] **Multi-utilizador (modo família)** — agendas partilhadas, permissões e
      eventos visíveis a vários membros.
- [ ] **Pesquisa na web (DuckDuckGo)** — nova ferramenta para responder a
      perguntas actuais (meteorologia, notícias, horários).
- [ ] **Gestão de tarefas (to-do)** — listas com prioridade, prazo e estado.
- [ ] **Resumos diários/semanais** — mensagem automática às 8:00 com a agenda do
      dia e, ao domingo, o resumo da semana.
- [ ] **Integração de email** — ler a caixa de entrada e transformar mensagens em
      eventos ou tarefas.
- [ ] **WhatsApp via Twilio** — o mesmo assistente noutro canal.
- [ ] **Mensagens de voz (Whisper)** — transcrever áudio e responder em voz.
- [ ] **Lembretes por localização** — «avisa-me quando chegar ao supermercado».
- [ ] **Registo de despesas** — «gastei 12 € no almoço» com relatórios mensais.
- [ ] **Perfis de personalidade configuráveis** — formal, informal, motivacional.
- [ ] **Cifra ponta-a-ponta da base de dados** — SQLCipher ou cifra ao nível do
      campo para as notas.

Extras que valem a pena considerar: exportação para `.ics`, cópias de segurança
automáticas, painel web de administração e testes automatizados das ferramentas.

---

## 7. Estimativa de custo mensal

### 7.1. Como funciona o preço da DeepSeek

A DeepSeek cobra **por token** (≈ 4 caracteres), não por mensagem, com preços
diferentes para *input* e *output*. Há ainda um desconto substancial para
**cache hits** — partes do prompt repetidas entre chamadas, que é exactamente o
caso do nosso prompt de sistema e dos esquemas das ferramentas.

Valores de referência para o `deepseek-chat` (em dólares por milhão de tokens):

| Tipo | Preço aproximado |
|---|---|
| Input (cache miss) | ~$0,28 / M tokens |
| Input (cache hit) | ~$0,03 / M tokens |
| Output | ~$0,42 / M tokens |

> ⚠️ Confirma sempre os valores actuais em
> <https://api-docs.deepseek.com/quick_start/pricing> — a DeepSeek já ajustou
> os preços mais do que uma vez e costuma ter descontos em horário de menor
> procura.

### 7.2. Estimativa para uso pessoal

Premissas de uma utilização pessoal realista:

* **15 mensagens por dia** (≈ 450 por mês);
* ~1 800 tokens de input por chamada (persona + agenda do dia + resumo de
  memória + histórico + esquemas das ferramentas);
* ~1,6 chamadas à API por mensagem (algumas usam ferramentas, logo há uma
  segunda chamada) → **≈ 2 900 tokens de input** por mensagem;
* ~150 tokens de output por mensagem;
* alguns resumos de memória por mês (custo residual).

| Item | Cálculo | Custo/mês |
|---|---|---|
| Input | 450 × 2 900 = 1,31 M tokens × $0,28 | ~$0,37 |
| Output | 450 × 150 = 0,07 M tokens × $0,42 | ~$0,03 |
| Resumos de memória | ~20 chamadas curtas | <$0,01 |
| **Total** | | **≈ $0,41 ≈ €0,38** |

**Conclusão: bem abaixo de €0,50/mês** para uso pessoal típico. Com cache hits a
factura tende a ficar ainda mais baixa. Um carregamento de 5 € dura,
realisticamente, mais de um ano.

### 7.3. E se usar muito mais?

O custo escala quase linearmente com o número de mensagens:

| Mensagens/dia | Custo estimado/mês |
|---|---|
| 5 | ~€0,13 |
| 15 | ~€0,38 |
| 30 | ~€0,75 |
| 60 | ~€1,50 |
| 150 | ~€3,80 |

Como reduzir, se precisares:

* usar os comandos `/hoje`, `/agenda`, `/notas` — respondem directamente da base
  de dados, **sem qualquer chamada à API**;
* baixar `MAX_HISTORY_MESSAGES` (menos contexto por chamada);
* reduzir `max_tokens` em `llm.py`;
* definir um limite de gastos na consola da DeepSeek.

O Telegram é gratuito e a base de dados é um ficheiro local — a API do modelo é
o único custo do projecto.

---

## 8. Resolução de problemas

| Sintoma | Causa provável | Solução |
|---|---|---|
| `Configuração inválida: Faltam variáveis...` | `.env` em falta ou incompleto | `cp .env.example .env` e preencher |
| «A minha chave de acesso ao modelo foi recusada» | `DEEPSEEK_API_KEY` errada | Gerar nova chave na consola DeepSeek |
| «Estou a receber pedidos a mais» | Saldo esgotado ou *rate limit* | Carregar saldo / esperar |
| Lembretes chegam à hora errada | `TIMEZONE` incorrecto | Definir `TIMEZONE=Europe/Lisbon` no `.env` |
| `ZoneInfoNotFoundError` | Base de fusos em falta (Windows) | `pip install tzdata` |
| Bot não responde | Processo parado ou token errado | Ver logs; confirmar o `TELEGRAM_TOKEN` |
| «Só consigo ler mensagens de texto» | Enviaste áudio/imagem | Ainda não suportado (ver secção 6) |

---

## 9. Privacidade

Tudo é local, excepto o texto das conversas, que é enviado à API DeepSeek para
gerar as respostas. A base de dados (`assistente.db`) fica na tua máquina e o
`.gitignore` já a exclui, tal como o `.env`. Se guardares dados sensíveis,
considera cifrar o disco — e vê a cifra da base de dados na lista de melhorias
futuras.

---

## 10. Licença

MIT — usa, modifica e partilha à vontade.
