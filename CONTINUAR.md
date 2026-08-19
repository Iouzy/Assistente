# Continuar este trabalho

Documento de passagem para uma sessão nova do Claude. Escrito a 15/08/2026, no
fim da sessão que unificou os painéis e corrigiu a perda de lembretes;
actualizado a 19/08/2026, no fim da sessão que fez o CI e o `.exe`.

**Se és uma sessão nova: lê isto todo antes de mexer em código.** As secções 3 e
7 são as que poupam tempo — decisões já tomadas (não as reabras) e armadilhas
que já custaram uma descoberta cada.

---

## 1. Estado do git

| | |
|---|---|
| Repositório | `Iouzy/Assistente` |
| Branch tronco | `claude/telegram-assistant-bot-gc3k0b` — **é este o tronco, não o `main`** |
| Branch da sessão anterior | `claude/windows-version-status-e0mk8g` (CI + `.exe`) |

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
| `caminhos.py` | Onde vivem código e dados; importação da instalação antiga — só biblioteca-padrão |
| `assistente.py` | Entrada do `.exe`: painel por omissão, bot com `--bot` |
| `actualizacao.py` | Versões novas pela API de Releases — só biblioteca-padrão |
| `versao.py` | O número da versão, num sítio só |
| `Assistente.spec`, `instalador.iss` | Receitas do PyInstaller e do Inno Setup |
| `.github/workflows/` | `testes.yml` (Ubuntu + Windows) e `compilar.yml` (o `.exe`) |
| `windows/`, `linux/` | Instaladores e atalhos por sistema operativo (execução a partir do código) |

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
→ *Feito.* O ciclo automático de 6 em 6 horas foi removido; o
`_verificar_versao_uma_vez` do `painel.py` pergunta uma vez no arranque,
escreve na consola e cala-se.

**Credenciais:** se encontrar um `.env` antigo, importa; senão, ele escreve
outra vez na aba «Credenciais».

**Instalador**, não `.exe` solto (recomendação minha, sem objecção dele).

**Um painel só** para Windows e Linux. O painel Tkinter (`windows/painel.pyw`)
foi apagado — não o ressuscites.

---

## 4. Feito, por sessão

### Sessão de 15/08 — painéis e lembretes

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

### Sessão de 19/08 — CI e `.exe`

Os pontos 1 e 2 da lista «a seguir» ficaram feitos. Em detalhe:

**CI** — `.github/workflows/testes.yml`. As sete suites offline em
`ubuntu-latest` **e** `windows-latest` a cada push, mais um `compileall` que
apanha erros de sintaxe em ficheiros que nenhum teste importa (o `painel.py`,
por exemplo). O `test_tool_choice.py` tem um job próprio, com a chave em
`secrets.DEEPSEEK_API_KEY`, e só corre a pedido — gasta cêntimos de cada vez.
*Se o segredo não estiver definido, esse job passa sem correr nada e diz-o.*

**Pasta de dados** — `caminhos.py` (só biblioteca-padrão, como o `acessos.py`).
Decide tudo à importação, a partir de `sys.frozen`:

| | A partir do código | Compilado |
|---|---|---|
| Código | pasta do projecto | `sys._MEIPASS` (temporária) |
| Dados | pasta do projecto | `%LOCALAPPDATA%\Assistente` |

Em modo código **nada mudou de sítio** — é por isso que as suites antigas
passaram sem alterações. `config.py`, `main.py` e `acessos.py` passaram a
ancorar-se aí; `ASSISTENTE_DADOS` aponta os dados para outro lado (é o que os
testes usam).

**Primeira execução importa a instalação antiga.** `.env` e base de dados,
copiados (nunca movidos) da pasta que se encontrar — a do executável,
`~/Assistente`, o Ambiente de Trabalho, ou a que `ASSISTENTE_PASTA_ANTIGA`
indicar. A base é copiada pela API de backup do SQLite e não com um
`copyfile`: está em modo WAL, e levar o `.db` sem o `-wal` deixava de fora
tudo o que ainda não tinha sido integrado.

**Entrada única** — `assistente.py`. `Assistente.exe` abre o painel;
`Assistente.exe --bot` corre o bot. O painel usa `sys.executable --bot` quando
congelado e `.venv + main.py` quando não. O `multiprocessing.freeze_support()`
é a primeira coisa que corre — sem ele, o processo que o pywebview cria voltava
a arrancar o programa do princípio, numa cascata de janelas.

**Actualização por Releases** — `actualizacao.py` (também só biblioteca-padrão).
Pergunta à API, compara versões número a número (`"1.10" > "1.9"`, que como
texto seria falso), e **só ao carregar no botão** descarrega o instalador e
lho entrega. Recusa endereços que não sejam `https` — o ficheiro é executado a
seguir. Escolhe, entre os anexos da release, o `.exe` com «instalador» no
nome; o nome do ficheiro faz parte do contrato com o `compilar.yml`.

**Compilação** — `Assistente.spec` + `instalador.iss` +
`.github/workflows/compilar.yml`, em `windows-latest`. Uma etiqueta `vX.Y.Z`
corre os testes, compila, **confirma que o `.exe` arranca** (`--versao`),
constrói o instalador e publica em Releases. A etiqueta tem de bater certo com
o `versao.py` ou a compilação pára.

---

## 5. A seguir, por ordem

1. **Painel como supervisor + arranque automático.**
   Hoje, se o `main.py` rebentar, o painel escreve «Assistente terminou (código
   N)» e fica a olhar. Devia reiniciar sozinho com recuo exponencial. O
   interruptor de arranque com o Windows já existe — é uma opção do instalador
   (`Tasks: arranque` no `instalador.iss`) — mas só para quem instala; a partir
   do código continuam a valer as instruções de `shell:startup` do README.

2. **Cópias de segurança + DPAPI.**
   Backup diário automático (`VACUUM INTO`), rotação, pasta à escolha (apontar ao
   OneDrive resolve). Cifrar `.env` e o conteúdo das notas com a DPAPI do
   Windows: decifra sozinho para a conta dele, é ilegível se copiarem o disco.
   *Nota: com os dados agora em `%LOCALAPPDATA%\Assistente`, o backup tem um
   sítio fixo de onde ler — era mais difícil quando dependia de onde o
   repositório tivesse sido clonado.*

3. **Funcionalidades**, por ordem de falta que fazem:
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

## 6. O `.exe`: o que ficou por confirmar

**O que já foi verificado.** O `Assistente.spec` foi compilado com o PyInstaller
**em Linux** (o mesmo spec, só o bootloader muda) e o binário resultante foi
corrido nos dois modos:

* `--bot` chega a criar a base de dados na pasta de dados e a falar com o
  Telegram, parando no ponto certo — token inválido. Prova que as importações
  dinâmicas do `dateparser`, do APScheduler e do `tzdata` sobreviveram ao
  empacotamento.
* sem argumentos, o painel serve a página inteira em `127.0.0.1` — título
  correcto, e os recursos do NiceGUI (Vue, Quasar) a responderem `200`. Prova
  que o `collect_all("nicegui")` apanhou os ficheiros de dados; sem eles, o
  clássico é compilar bem e servir uma página em branco.

**O que continua por verificar, e só um Windows verifica:**

1. **O `pywebview`/WebView2 dentro do `.exe`.** Não é instalado em Linux, por
   isso o spec compilou sem ele (com um aviso, de propósito). É o único pedaço
   do pacote que nunca foi empacotado nem uma vez.

2. **A consola ao vivo pode ficar muda.** O `.exe` é compilado com
   `console=False`, e um programa de janela em Windows pode arrancar sem
   `sys.stdout`. O painel lança o bot com `stdout=PIPE`, o que *deve* dar-lhe um
   descritor válido — mas é preciso ver. Se ficar muda, o remédio já existe: os
   registos vão todos para o `assistente.log` da pasta de dados; bastaria pôr o
   painel a segui-lo em vez de ler o pipe.

3. **O Inno Setup.** O `instalador.iss` nunca foi compilado — o `iscc` só existe
   em Windows. E, dentro dele, o `/CLOSEAPPLICATIONS` a fechar o painel que o
   lançou: o `actualizacao.instalar()` devolve o controlo imediatamente e o
   painel chama `app.shutdown()` logo a seguir, de propósito — um processo com
   ficheiros abertos impede a sua própria substituição.

4. **Falta um ícone.** O `.exe` sai com o ícone genérico do Windows
   (`icon=None` no spec). Um `.ico` na raiz e uma linha no spec resolvem.

**A decisão que foi mudada em relação ao que estava planeado aqui:** este
documento previa que o `.exe` se renomeasse a si próprio e se substituísse. Com
um instalador do Inno Setup isso deixa de ser preciso — é ele que fecha o
programa, troca os ficheiros e o reabre, e ainda trata dos atalhos e da
desinstalação. Menos código nosso a fazer malabarismo com ficheiros abertos.

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
python tests/test_acessos.py      # permissões e credenciais
python tests/test_tools.py        # ferramentas, lembretes, porteiro
python tests/test_seguranca.py    # escrito do ponto de vista de quem ataca
python tests/test_timeline.py     # linha do tempo
python tests/test_llm.py          # ciclo LLM (com duplos, sem API)
python tests/test_caminhos.py     # pasta de dados, importação da instalação antiga
python tests/test_actualizacao.py # comparação de versões, resposta do GitHub
```
Nenhum destes gasta tokens nem fala com a rede. Todos passavam no fim desta
sessão, e o CI corre-os a cada push nos dois sistemas — mantém-nos assim.

**O `test_caminhos.py` reimporta o `caminhos` a cada caso** (`importlib.reload`),
porque o módulo decide tudo à importação. O ambiente que ele põe **fica posto**
depois do reload, de propósito: a `ASSISTENTE_PASTA_ANTIGA` é lida no momento da
procura, não na importação, e repô-la fazia a procura correr sem ela. Já mordeu
uma vez.

**`tests/test_tool_choice.py` fala com a API a sério**, custa cêntimos e precisa
da chave. É o único teste que mede a escolha de ferramenta, que é o que mais
falha. Tem agora um job próprio no CI (`Actions → Testes → Run workflow`), mas
**só corre se o segredo `DEEPSEEK_API_KEY` estiver definido** em Settings →
Secrets and variables → Actions. Enquanto não estiver, passa sem correr nada —
continua a não servir para nada.

**`test_seguranca.py` conta jobs do scheduler.** Se acrescentares jobs de
manutenção, filtra por prefixo — já mordeu uma vez quando entrou o
`reconcile-reminders`. O filtro está lá, mantém-no.

**Correr o painel exige o Python do `.venv` explicitamente.**
`python painel.py` usa o Python global e dá `ModuleNotFoundError: nicegui`. Tem
de ser `.venv\Scripts\python.exe painel.py` (Windows) ou `.venv/bin/python
painel.py` (Linux). O utilizador já tropeçou nisto duas vezes. (Deixa de valer
para quem usar o programa instalado — aí não há `.venv` nenhum.)

**O `caminhos.py` e o `actualizacao.py` são só biblioteca-padrão, e têm de
continuar a ser.** O `caminhos.py` é importado pelo `config.py`, que corre antes
de qualquer dependência estar garantida; e os dois são importados pelo painel,
que tem de abrir mesmo sem ambiente virtual criado, para poder dizer que ele
falta. Uma importação de terceiros num destes dois ficheiros parte isso em
silêncio.

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
  importar para quem usar o `.exe` (as versões ficam congeladas na compilação),
  mas continua a valer para quem corre a partir do código — e agora também para
  o próprio CI, que instala do zero a cada execução. Não fixes versões às cegas:
  o ambiente dele é Python 3.14 e o `python-telegram-bot>=22.8` existe
  precisamente para o proteger aí.
- **O segredo `DEEPSEEK_API_KEY` não está definido no GitHub.** Sem ele, o job
  da escolha de ferramenta passa sem correr nada. É preciso pedir-lhe que o
  ponha em Settings → Secrets and variables → Actions.
- **A primeira compilação nunca foi corrida.** Ver a secção 6: o workflow está
  escrito e validado, mas só uma execução em `windows-latest` prova que o `.exe`
  sai inteiro. Vale a pena correr `Actions → Compilar → Run workflow` (que não
  publica nada) antes de criar a primeira etiqueta.
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
