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
- **Fechado por omissão:** o bot só responde a ids autorizados de propósito.
  A quem não estiver autorizado não responde *nada* — nem sequer uma recusa.
- **Ninguém fica dono por escrever primeiro.** O primeiro id é acrescentado por
  si, no painel de controlo (botão «Utilizadores», Windows ou Linux) ou em
  `ALLOWED_USER_IDS`.
- **Partilha por comando, só pelo dono:** `/allow <id>` e `/revoke <id>`, sem
  editar ficheiros nem reiniciar — dá para modo família. Quem foi convidado
  usa o bot, mas não pode convidar mais ninguém.
- **Retirar o acesso cala mesmo o bot:** os lembretes já agendados dessa pessoa
  são cancelados, em vez de continuarem a chegar-lhe.
- **Só em conversa privada.** Em grupos e canais o bot não responde: os dados
  são pessoais e um `/notes` num grupo mostrava-os a toda a gente.
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

**Regra da casa: o bot só fala com quem foi autorizado de propósito.** A quem
não estiver na lista ele não responde coisa nenhuma — nem uma recusa. E
**ninguém fica dono por escrever primeiro**: o primeiro id tens de o pôr tu.

> Isto é diferente de versões anteriores, em que a primeira pessoa a escrever
> ficava registada como dona. Bastava alguém descobrir o username antes de ti.

Há duas maneiras de gerir a lista, e é o `ALLOWED_USER_IDS` que decide qual.

#### Modo base de dados (`ALLOWED_USER_IDS` vazio)

Autoriza-te a ti primeiro, pelo painel de controlo — botão **👥 Utilizadores**
(Windows: secção 3.7; Linux: secção 3.8): escreve o teu id, dá-lhe um nome e
carrega em **Adicionar**. O primeiro id da lista fica **dono**. (Para saberes
o teu id, manda uma mensagem ao @userinfobot.)

Daí em diante geres tudo pelo Telegram:

| Comando | O que faz | Quem pode |
|---|---|---|
| `/who` | Mostra o teu id e a lista de quem tem acesso | Qualquer autorizado |
| `/allow <id> [nome]` | Deixa entrar mais alguém | **Só o dono** |
| `/revoke <id>` | Retira o acesso (o dono não pode ser retirado) | **Só o dono** |

Quem foi convidado usa o assistente normalmente, mas não pode convidar mais
ninguém nem tirar o acesso a quem quer que seja. Retirar o acesso a alguém
cancela também os lembretes que essa pessoa tivesse agendados.

Enquanto a lista estiver vazia o bot arranca mudo e diz-lo no registo:

```
Ninguém está autorizado: o assistente vai ignorar todas as mensagens, sem
responder. Autorize o seu id no painel de controlo («Utilizadores») ou
preencha ALLOWED_USER_IDS no .env.
```

#### Modo lista fixa (`ALLOWED_USER_IDS` preenchido)

`ALLOWED_USER_IDS=123456789,987654321` fixa a lista e desliga o `/allow` e o
`/revoke` — a lista passa a mudar-se só no ficheiro, com reinício. É o mais
adequado num servidor.

|  | Telegram (`/allow`) | Painel |
|---|---|---|
| Dar e retirar acesso | ✅ (só o dono) | ✅ |
| Mudar o dono | ❌ | ✅ |
| Autorizar o primeiro id | ❌ | ✅ |
| Funciona sem o computador ao pé | ✅ | ❌ |

É a mesma tabela da base de dados nos dois casos, por isso as duas vias podem
ser usadas à vontade. **Não é preciso reiniciar o bot:** ele relê a lista de 10
em 10 segundos e apanha o que o painel gravar.

> 💡 Podes fechar o bot **antes** de ele arrancar pela primeira vez: adiciona-te
> a ti no painel e já ninguém o pode reclamar escrevendo-lhe primeiro.

O id de quem te quer escrever aparece na consola do painel assim que essa
pessoa tenta falar com o bot (`Acesso recusado a Ana (id 987654321)`) — é a
forma mais simples de o obter.

#### Modo fixo (`.env`)

Preencher a variável fixa a lista e desliga o `/allow` e o `/revoke`:

```ini
ALLOWED_USER_IDS=123456789,987654321
```

Faz mais sentido num servidor, onde se quer a configuração no ficheiro e não
numa conversa. Para saberes o teu id, envia `/who` ao bot.

Enquanto a variável estiver preenchida, o `/allow`, o `/revoke` e o painel
ficam sem efeito — a janela **Utilizadores** avisa-te disso e tem um botão
**«Passar a gestão para o painel»**, que copia os ids do `.env` para a base de
dados e esvazia a linha (com cópia de segurança em `.env.bak`). Aí sim, é
preciso reiniciar o bot, porque o `.env` só é lido no arranque.

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

Quatro conjuntos de testes que **não gastam um único token da API** — o cliente
DeepSeek é substituído por um duplo de teste:

```bash
python tests/test_tools.py     # datas, as 10 ferramentas, BD, lembretes reais
python tests/test_llm.py       # tool calling, cache, memória, ponte scheduler↔asyncio
python tests/test_acessos.py   # permissões geridas pelo painel (não abre janelas)
python tests/test_seguranca.py # porteiro, posse dos dados, saneamento, limites
```

Correm em cerca de 30 segundos (esperam pelo disparo real de lembretes).

O `test_seguranca.py` está escrito do ponto de vista de quem ataca: cada bloco
corresponde a uma falha concreta que existiu, e passa quando o abuso deixa de
funcionar. Vale a pena corrê-lo sempre que se mexer no porteiro, na posse dos
dados ou no que vai para o registo.

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
| **`windows/painel.vbs`** | **Painel de controlo: ligar, parar, ver a consola, utilizadores, actualizar** |
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

O botão **Utilizadores** abre a gestão de permissões (secção 3.4.1): dar e
retirar acesso, e passar a coroa de dono. Escreve na mesma tabela que o
`/allow` usa e pode ser feito com o assistente a correr — ele relê a lista de
10 em 10 segundos.

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

### 3.8. Correr no Ubuntu (painel de controlo)

Equivalente ao painel do Windows (secção 3.7), mas como página web servida em
`localhost` — não precisa de Tkinter nem de nada específico do Windows.

**Instalação, uma vez:**

```bash
git clone <o-teu-fork-ou-repositório> Assistente
cd Assistente
bash linux/instalar.sh
```

Isto cria o ambiente virtual, instala as dependências do bot e do painel, e
regista um atalho **"Assistente — Painel de Controlo"** no menu de
aplicações. Não cria o `.env` nem pede credenciais — isso faz-se dentro do
próprio painel, a seguir.

**A partir daí, é só abrir o atalho** (menu de aplicações, ou copiado para o
Ambiente de Trabalho — o `instalar.sh` diz o caminho exacto). Abre o
`linux/painel.py`, que arranca um servidor local e abre-o sozinho numa aba do
navegador. Não é preciso terminal nem escrever `python` nenhum depois deste
primeiro passo.

O painel tem três abas:

- **Consola** — os botões **Ligar**/**Parar** (o mesmo encerramento ordenado
  do painel do Windows, pelo ficheiro `.stop-assistente`) e a saída do bot ao
  vivo.
- **Utilizadores** — a mesma gestão de acesso da secção 3.4.1: dar e retirar
  permissões, passar a coroa de dono. Pode fazer-se com o bot ligado.
- **Credenciais** — onde entram o `TELEGRAM_TOKEN` e a `DEEPSEEK_API_KEY`.
  Escreve-os aqui e o painel grava-os no `.env` (criado na hora, a partir do
  `.env.example`, se ainda não existir) — não há ficheiro nenhum para copiar
  à mão de outro sistema. Se já tiveres as chaves noutra instalação (por
  exemplo, num Windows do mesmo computador em arranque duplo), também podes
  simplesmente copiar o `.env` de lá para aqui em vez de as voltar a escrever.

**Actualização:** o botão **Actualizar agora** faz o mesmo que o do Windows
(`git pull`, reinstala dependências se mudaram). O painel do Linux acrescenta
uma verificação automática a cada 6 horas, sem precisar de clicar em nada —
só corre com o assistente desligado, para não mexer no código a meio de uma
execução. Se a actualização tocar no próprio painel, fica um aviso na consola
a pedir para o reabrir.

### 3.9. Deixar a correr sempre num servidor Linux (opcional)

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

O `WAL` também é o que deixa o painel de controlo — que é outro processo —
alterar a lista de acesso com o bot a correr. Como o porteiro trabalha a partir
de uma cópia em memória, o `main.py` relê a tabela de 10 em 10 segundos
(`watch_access_list`); é assim que uma permissão dada no painel vale sem
reiniciar nada.

### 5.4.1. Datas: como são guardadas, e uma limitação conhecida

Compromissos e lembretes são guardados como texto ISO-8601 **com fuso**
(`2026-08-07T15:00:00+01:00`) e comparados pela base de dados como texto. Isso
funciona porque a parte da data e da hora vem primeiro e domina a comparação.

**A limitação:** na hora que se repete no fim do horário de verão (o último
domingo de Outubro, entre a 1h e as 2h), a mesma hora local acontece duas
vezes, primeiro com `+01:00` e depois com `+00:00`. Como texto, `+00:00`
ordena antes de `+01:00` — ou seja, ao contrário da ordem real. Dois
compromissos dentro dessa hora aparecem trocados na agenda.

Acontece uma hora por ano e só com dois registos dentro dela. **A hora a que
cada lembrete dispara está correcta** — o scheduler compara instantes, não
texto; o que troca é apenas a ordem de listagem. Corrigir a sério implicava
guardar tudo em UTC e converter na apresentação, ou seja, migrar o esquema e os
dados existentes — o que não compensa para o alcance do problema.

A linha do tempo (secção 5.5) não tem este problema: guarda dias
(`YYYY-MM-DD`), sem hora nem deslocamento, portanto não há nada para trocar.

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
| `acessos.py` | Lista de acesso e credenciais vistas do painel — só biblioteca-padrão |
| `windows/painel.pyw` | Painel de controlo do Windows (tkinter): processo, consola, utilizadores |
| `linux/painel.py` | Painel de controlo do Linux (NiceGUI): processo, consola, utilizadores, credenciais, auto-actualização |

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
| O bot não responde a nada | O teu id não está autorizado | Painel → «Utilizadores» → Adicionar; ou põe o id em `ALLOWED_USER_IDS`. O id recusado aparece no registo |
| «Only the owner can change who has access» | Estás autorizado mas não és o dono | Pede ao dono, ou passa a coroa no painel («Tornar dono») |

---

## 9. Privacidade

Tudo é local, exceto o texto das conversas, que é enviado à API DeepSeek para
gerar as respostas.

- **Acesso:** o bot só responde a quem estiver autorizado (secção 3.4.1), e só
  em conversa privada. Sem lista, não responde a ninguém.
- **Ficheiros:** a base de dados (`assistente.db`) e o registo são criados
  legíveis só pelo teu utilizador (0600 em Linux/macOS; em Windows valem as
  permissões da tua pasta de perfil).
- **`.gitignore`:** exclui o `.env` *e as suas cópias* (`.env.bak`, que o painel
  escreve ao passar a gestão da lista para a base de dados), a base de dados e
  os ficheiros de registo.
- **Registo:** por omissão **não** guarda o texto das mensagens nem o conteúdo
  das notas — só ids e nomes de ferramentas. Põe `LOG_MESSAGES=true` para os
  incluir enquanto diagnosticas alguma coisa, e volta a desligar depois.

Se guardares dados sensíveis, considera cifrar o disco — e vê a cifra da base
de dados na secção 6.

---

## 10. Licença

MIT — usa, modifica e partilha à vontade.
