# Continuar este trabalho

Documento de passagem para uma sessão nova do Claude. Escrito a 15/08/2026, no
fim da sessão que unificou os painéis e corrigiu a perda de lembretes.

**Se és uma sessão nova: lê isto todo antes de mexer em código.** As secções 3 e
7 são as que poupam tempo — decisões já tomadas (não as reabras) e armadilhas
que já custaram uma descoberta cada.

---

## 1. Estado do git

| | |
|---|---|
| Repositório | `Iouzy/Assistente` |
| Branch tronco | `claude/telegram-assistant-bot-gc3k0b` — **é este o tronco, não o `main`** |
| Branch da sessão anterior | `claude/feedback-improvements-jpq1hm` (fundido, esgotado) |

**Está tudo fundido no tronco.** Nada ficou pendente:

```
PR #8  beb30db  Unifica o painel do Windows e do Linux num só (NiceGUI)
       2d092ab  Janela própria no Windows, em vez de aba do navegador
       8c804eb  Lembretes deixam de se perder em silêncio
       4fe2606  Documento de passagem (este ficheiro)
PR #7  ccd4300  Painel de controlo para Linux/Ubuntu
```

### Como começar

O `claude/feedback-improvements-jpq1hm` está **fundido e esgotado** — não
empilhes commits novos em cima dele. Parte do tronco actualizado:

```bash
git fetch origin claude/telegram-assistant-bot-gc3k0b
git checkout -B <branch-novo> origin/claude/telegram-assistant-bot-gc3k0b
```

> **Armadilha que já aconteceu uma vez:** o PR #7 foi fundido quando ainda só
> tinha o primeiro dos quatro commits, e os outros três ficaram no branch sem
> PR nenhum a segui-los — só se deu por isso ao preparar este documento. Um PR
> fundido não pode seguir trabalho novo. Se continuares a empurrar commits para
> um branch depois de abrires o PR, **confirma antes de terminares** que o PR
> ainda os apanha (`mcp__github__pull_request_read`, campo `merged` e o `sha` do
> `head`) em vez de assumir que sim.

---

## 2. O que é o projecto

Assistente pessoal em Telegram, escrito em Python, que fala com a API DeepSeek
(compatível com OpenAI). Guarda agenda, notas, lembretes e uma linha do tempo
numa base de dados SQLite local. Corre no computador do utilizador.

O código, os comentários, o README e as mensagens de commit estão todos em
**português europeu com grafia pré-AO** («actualizar», «correcção», «acção»,
«ficheiro»). Mantém isso — é consistente em todo o repositório.

### Ficheiros

| Ficheiro | Responsabilidade |
|---|---|
| `main.py` | Arranque, logging, ponte thread↔event loop, encerramento |
| `bot.py` | Handlers, comandos, botões, envio de mensagens |
| `llm.py` | Cliente DeepSeek, prompt, tool calling, memória |
| `tools.py` | As ferramentas expostas ao modelo + datas naturais (1165 linhas) |
| `database.py` | Esquema SQLite e CRUD thread-safe |
| `scheduler.py` | Agendamento, disparo e **reconciliação** de lembretes |
| `config.py` | Variáveis de ambiente, validadas no arranque |
| `safety.py` | Saneamento de texto vindo de fora |
| `acessos.py` | Lista de acesso e credenciais, vistas do painel — só biblioteca-padrão |
| `painel.py` | Painel de controlo (NiceGUI), Windows e Linux |
| `windows/`, `linux/` | Instaladores e atalhos por sistema operativo |

---

## 3. Decisões já tomadas — não reabrir

Estas custaram conversa. Tratá-las como fechadas.

**Corre no PC do utilizador, localmente. Não num servidor.**
Foi discutido e recusado. Razão: a base de dados tem notas que o próprio README
diz servirem para códigos e palavras-passe, e o `.env` tem as credenciais — num
servidor alugado isso fica em disco de outrem, em claro. Cifrar não resolve com
honestidade, porque um processo que arranca sozinho tem de ter a chave na
máquina.

**Consequência aceite: PC desligado = não recebe.**
Palavras do utilizador: «quando nao tiver o pc ligado nao recebo, simples, tenho
de o ter ligado.» Não voltes a propor soluções para isto.

**CORTADO do plano:** temporizadores de reactivação do Windows, impedir a
suspensão. Propostos e recusados explicitamente.

**Quer um programa a sério, não um script.**
Nada de abrir a pasta do repositório, nada de `git pull`, nada de
`.venv\Scripts\python.exe`, nada de PowerShell. Instala uma vez, ícone, duplo
clique. Foi dito de forma directa: «isto nao é um programa mas sim uma script».

**Actualização só a pedido, mas com aviso.**
Verifica se há versão nova e **avisa**; só actualiza quando ele carregar no
botão. Nada de actualizar sozinho.
→ *O código actual ainda tem um ciclo automático de 6 em 6 horas
(`_ciclo_auto_actualizacao` no `painel.py`). **Isto contraria a decisão e tem de
ser mudado** quando se fizer o trabalho do `.exe`.*

**Credenciais:** se encontrar um `.env` antigo, importa; senão, ele escreve
outra vez na aba «Credenciais».

**Instalador**, não `.exe` solto (recomendação minha, sem objecção dele).

**Um painel só** para Windows e Linux. O painel Tkinter (`windows/painel.pyw`)
foi apagado — não o ressuscites.

---

## 4. Feito nesta sessão

**`ccd4300` — Painel para Linux** (fundido no PR #7)
NiceGUI, com abas Consola / Utilizadores / Credenciais. O `acessos.py` saiu de
`windows/` para a raiz e ganhou `garantir_env()` e `definir_variaveis()`, que
escrevem no `.env` preservando comentários e o resto do ficheiro.

**`beb30db` — Painel unificado**
`painel.py` subiu para a raiz e corre nos dois sistemas, escolhendo caminho do
`.venv`, `creationflags` e forma de abrir a pasta por `sys.platform`.
`windows/painel.pyw` (Tkinter) removido; `windows/painel.vbs` aponta ao novo.
`windows/instalar.bat` criado, a par do `linux/instalar.sh`.

**`2d092ab` — Janela própria no Windows**
`MODO_NATIVO` liga o `native=True` do NiceGUI (WebView2, já vem no Edge) por
omissão no Windows. No Linux fica no navegador, porque o pywebview aí depende de
GTK/Qt. `pywebview` só é instalado no Windows (marcador `sys_platform`). Se o
pacote faltar, cai sozinho para o navegador em vez de rebentar.

**`8c804eb` — Lembretes deixam de se perder em silêncio**
Eram dois bugs, ambos a comer dados:

1. O `misfire_grace_time` de 300 s fazia o APScheduler **descartar** um job cuja
   hora passou há mais do que isso. Sendo um `DateTrigger`, o job morria aí: o
   `fired` ficava a `0` e o lembrete **nunca mais disparava**, com o bot a correr
   e nada a assinalar. Bastava a tampa fechada dez minutos.
2. Fora da janela de tolerância, o lembrete era marcado como disparado **sem
   nunca ter sido enviado** — desaparecia deixando só uma linha no registo.

`reconcile_reminders()` passa a correr de 5 em 5 minutos (`RECONCILE_MINUTES`),
compara os pendentes com os jobs vivos e repõe o que falta, sem tocar em quem já
tem job. O que ficou para trás é comunicado numa mensagem agrupada por pessoa.

---

## 5. A seguir, por ordem

1. **CI** ← *era o próximo passo acordado*
   Não existe nenhum (`.github/workflows` está vazio). As quatro suites offline
   em `ubuntu-latest` + `windows-latest`. O bug do UTF-8 em Windows (PR #6)
   precisou de uma máquina real; isto apanhava-o de graça. É também **pré-
   requisito para o `.exe`**, que só compila em Windows.

2. **O `.exe` com instalador** — o pedido principal por satisfazer. Ver secção 6.

3. **Painel como supervisor + arranque automático.**
   Hoje, se o `main.py` rebentar, o painel escreve «Assistente terminou (código
   N)» e fica a olhar. Devia reiniciar sozinho com recuo exponencial. Mais um
   interruptor de arranque com o Windows, em vez das instruções manuais de
   `shell:startup` no README.

4. **Cópias de segurança + DPAPI.**
   Backup diário automático (`VACUUM INTO`), rotação, pasta à escolha (apontar ao
   OneDrive resolve). Cifrar `.env` e o conteúdo das notas com a DPAPI do
   Windows: decifra sozinho para a conta dele, é ilegível se copiarem o disco.

5. **Funcionalidades**, por ordem de falta que fazem:
   - **Eventos recorrentes** («todas as terças às 18h») — a ausência mais
     gritante. Está na lista de melhorias futuras do README, por fazer.
   - **Adiar / concluir a partir do lembrete** — botões inline «+10min / +1h /
     amanhã» e «✅ feito». Hoje o aviso chega e não há nada a fazer com ele.
   - **Notas de voz** (Whisper) — maior salto de utilidade por euro, num
     assistente que se usa a andar na rua.
   - **Custos à vista** — a DeepSeek devolve `prompt_cache_hit_tokens`; dá para
     mostrar gasto real no painel em vez da estimativa do README.
   - **Verificação anti-confabulação por código.** O PR #5 corrigiu por regra de
     persona o caso em que o assistente disse três vezes que tinha gravado sem
     ter gravado. Uma regra de persona é uma sugestão; verificar que houve uma
     chamada com `status: ok` antes de deixar sair uma afirmação de escrita é uma
     garantia.
   - **Ver e editar dados no painel** — rede de segurança para quando o modelo
     falha a corrigir um registo.

> **Cuidado ao acrescentar funcionalidades:** já há quatro tipos de registo
> (eventos, notas, linha do tempo, lembretes) e a fronteira notas/linha-do-tempo
> precisou de regras de persona e 16 casos de teste para o modelo não se
> enganar. Não acrescentes um quinto tipo sem necessidade real.

---

## 6. O trabalho do `.exe`, em detalhe

O que já foi pensado, para não se repensar:

- **PyInstaller** empacota Python + nicegui + pywebview + o bot. Sem `.venv`,
  sem pasta de repositório, sem Python instalado na máquina.
- **Tem de ser compilado em Windows** — o PyInstaller não faz compilação
  cruzada, e o ambiente de desenvolvimento aqui é Linux. Solução: GitHub Actions
  em `windows-latest` compila e publica em *Releases*. Daí o CI vir primeiro.
- **A auto-actualização deixa de ser `git pull`.** Passa a: perguntar à API de
  Releases do GitHub se há versão nova → **avisar** → e só ao carregar no botão,
  descarregar o `.exe` novo, substituir-se e reiniciar. (No Windows um `.exe` a
  correr não se sobrepõe a si próprio: renomeia-se o actual, põe-se o novo no
  lugar e reinicia-se.)
- **O bot deixa de arrancar pelo `.venv`.** O `.exe` relança-se a si próprio em
  modo bot (`Assistente.exe --bot`), mantendo o processo separado e o
  encerramento ordenado pelo ficheiro `.stop-assistente` que já existe.
- **Os dados mudam de sítio**, para `%LOCALAPPDATA%\Assistente` — senão uma
  actualização que substitui a pasta leva a base de dados à frente.
- **Instalador** com Inno Setup, também compilado no Actions: atalhos e
  desinstalação decentes.
- **Importar o `.env` antigo** na primeira execução, se encontrar um.
- **Remover o ciclo automático de 6 em 6 horas** do `painel.py` (contraria a
  decisão da secção 3).

---

## 7. Armadilhas — cada uma custou uma descoberta

**O notificador não pode ser chamado de dentro do event loop.**
`build_notifier` (em `main.py`) faz `run_coroutine_threadsafe(...)` seguido de
`future.result(timeout=30)`. No arranque, `restore_pending_reminders()` é
chamado a partir do `post_init`, que **corre no próprio event loop** — chamar lá
o notificador bloqueia à espera de si mesmo. É por isso que o resumo dos
lembretes falhados vai num job com 10 segundos de atraso, e não directamente.
Se precisares de enviar mensagens a partir do arranque, usa o mesmo padrão.

**Os testes não são pytest.** São scripts à mão, com uma função `check()` e uma
lista `falhas`. Correm-se um a um:
```bash
python tests/test_acessos.py     # permissões e credenciais
python tests/test_tools.py       # ferramentas, lembretes, porteiro
python tests/test_seguranca.py   # escrito do ponto de vista de quem ataca
python tests/test_timeline.py    # linha do tempo
python tests/test_llm.py         # ciclo LLM (com duplos, sem API)
```
Nenhum destes gasta tokens. Todos passavam no fim desta sessão.

**`tests/test_tool_choice.py` fala com a API a sério**, custa cêntimos e precisa
da chave. Está marcado como «não corri» em três PRs seguidos — nunca correu.
É o único teste que mede a escolha de ferramenta, que é o que mais falha.
Entra no CI com a chave em secret, ou não serve para nada.

**`test_seguranca.py` conta jobs do scheduler.** Se acrescentares jobs de
manutenção, filtra por prefixo — já mordeu uma vez quando entrou o
`reconcile-reminders`. O filtro está lá, mantém-no.

**Correr o painel exige o Python do `.venv` explicitamente.**
`python painel.py` usa o Python global e dá `ModuleNotFoundError: nicegui`. Tem
de ser `.venv\Scripts\python.exe painel.py` (Windows) ou `.venv/bin/python
painel.py` (Linux). O utilizador já tropeçou nisto duas vezes.

**Variáveis de ambiente do painel:** `PAINEL_PORT` (8765), `PAINEL_NATIVE`
(0/1), `PAINEL_ABRIR_NAVEGADOR` (0/1). As duas últimas são indispensáveis para
testar sem interface.

**O painel foi testado com Playwright** num sandbox isolado (cópia do
repositório, `.env` e base de dados próprios). Chromium está em
`/opt/pw-browsers/chromium-1194/chrome-linux/chrome` — passa-o em
`executable_path`, porque o `playwright install` não corre aqui.

**`xdg-open` pode não existir** em Linux; `abrir_pasta()` está protegido.

---

## 8. Por verificar com o utilizador

- **A janela nativa no Windows nunca foi confirmada.** O `2d092ab` acrescentou o
  `MODO_NATIVO`, ele instalou o `pywebview` — mas não chegou a dizer se a janela
  abriu mesmo como janela em vez de aba. **Pergunta antes de assumir.**
- **Sem lockfile.** Assinalado nos PRs #3, #4 e #5, nunca feito. `pip install`
  com intervalos abertos, e o painel a corrê-lo sem supervisão. Deixa de
  importar quando houver `.exe` (versões congeladas na compilação), mas até lá é
  uma roleta. Não fixes versões às cegas: o ambiente dele é Python 3.14 e o
  `python-telegram-bot>=22.8` existe precisamente para o proteger aí.
- **O README diz para guardar palavras-passe nas notas.** Eu desaconselharia o
  contrário — para isso há gestores de palavras-passe, e assim a base de dados
  deixa de ser um alvo que vale a pena. Sugerido, ainda não decidido.

---

## 9. Ambiente de desenvolvimento

Contentor Linux efémero; o repositório é clonado de novo a cada sessão. **O que
não for commitado e enviado perde-se.**

```bash
python3 -m venv .venv-t
source .venv-t/bin/activate
pip install -r requirements.txt -r requirements-painel.txt
```

Não há `gh` CLI — o GitHub faz-se pelas ferramentas MCP (`mcp__github__*`).
Chromium para Playwright já está instalado; **não corras `playwright install`**.
