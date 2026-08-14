# Guardrails — as regras que vinculam qualquer tarefa

> **Vinculativo.** Este ficheiro vence qualquer task file que discorde dele.
> Uma sessão lê-o uma vez no arranque e trata-o como fechado, não como uma
> proposta.

## Como as regras estão organizadas

Por assunto (identidade, acesso, dados, língua…), não por task file — uma
regra escreve-se uma vez, independentemente de quantos task files lhe
tocarem. Um task file que a repita corre o risco de ela divergir daqui, e aí
os dois ficheiros discordam sobre o que é permitido.

## A · Identidade e âmbito

Assistente pessoal de Telegram para uma pessoa, ou um pequeno grupo de pessoas
explicitamente convidadas ("modo família", ver §B) — nunca um produto para
desconhecidos. Não há conta própria, servidor central, nem recolha de dados
para terceiros: as únicas dependências externas são a API do Telegram e a API
da DeepSeek. Isto não muda com uma tarefa; uma tarefa que precise de o mudar
(ex.: um backend próprio, uma conta multi-tenant) é uma decisão do dono, não
uma implementação directa.

## B · Acesso e posse dos dados — a porta

Esta é a secção de segurança do repositório. Vinculativa, não meramente
informativa.

- **Fechado por omissão.** A quem não estiver autorizado, o bot não responde
  **nada** — nem uma recusa. Isto é deliberado: uma recusa já confirma que o
  bot existe e está online.
- **Ninguém fica dono por escrever primeiro** (ver §G — decisão fechada). O
  primeiro id é sempre um acto explícito: no painel do Windows, ou em
  `ALLOWED_USER_IDS`.
- **`ALLOWED_USER_IDS` no `.env`, quando preenchido, manda sobre a tabela
  `access`** e desliga `/allow`/`/revoke` e o painel — modo lista fixa,
  pensado para um servidor. Vazio, vale a tabela `access` — modo base de
  dados, gerido por `/allow`, `/revoke`, ou pelo painel.
- **Só o dono dá ou retira acesso**, e o dono não pode ser retirado a si
  próprio por outra via que não o painel.
- **Retirar o acesso cancela também os lembretes já agendados** dessa pessoa
  — nunca continuam a chegar a quem perdeu o acesso.
- **Só em conversa privada.** Em grupos e canais o bot não responde: os dados
  são pessoais e uma consulta num grupo mostrava-os a toda a gente.
- **`windows/acessos.py` nunca importa `config.py`.** Importar carregaria o
  `.env` para o processo do painel, e o bot arrancado a seguir herdava essas
  variáveis — ficando surdo a alterações feitas ao ficheiro depois de o
  painel abrir. Por isso este módulo usa só a biblioteca-padrão e repete o
  DDL da tabela `access`.
- **O porteiro relê a lista a cada 10 segundos** (`watch_access_list`,
  `main.py`) porque o painel é outro processo e trabalha a partir de uma cópia
  em memória (`_acesso_cache`) — sem isto, uma permissão dada no painel só
  valia depois de reiniciar o bot.

## C · Dados e privacidade

- Os dados de cada utilizador estão isolados por `user_id` em todas as
  tabelas — uma tarefa nova que adicione uma tabela repete este isolamento,
  não o reinventa nem o omite.
- **`.env`, `.env.bak`, `assistente.db*` e `*.log*` nunca são versionados** —
  já estão no `.gitignore`; uma tarefa não adiciona excepções a isto.
- **Ficheiros de dados são criados com permissões restritas** (0600 em
  Linux/macOS). Uma tarefa que crie um novo ficheiro persistente (uma cópia
  de segurança, uma exportação) segue o mesmo padrão.
- **O registo não guarda o texto das mensagens nem o conteúdo das notas por
  omissão** — só ids e nomes de ferramentas. `LOG_MESSAGES=true` liga isto
  explicitamente, para diagnóstico, e é responsabilidade de quem o liga
  desligá-lo depois.
- **Todo o texto vindo de fora passa por `safety.py` antes de ser
  registado, mostrado a terceiros, ou colado num prompt** — ver as três
  funções e a razão de cada uma no cabeçalho desse ficheiro. Uma tarefa que
  introduza uma nova fronteira externa (uma nova origem de texto do
  utilizador) usa a função certa para essa fronteira, não inventa uma nova.

## D · Dependências

Sem regra de "biblioteca-padrão apenas" a nível do repositório — mas:

- Uma dependência nova justifica-se no `Why:` da tarefa que a introduz, e
  actualiza o `requirements.txt` com um intervalo de versões (o padrão já
  usado: `>=x.y,<próxima-major`), com um comentário a dizer porquê essa
  versão mínima.
- **Excepção vinculativa:** `windows/acessos.py` não pode importar
  `config.py` nem depender de nada que o faça (ver §B) — continua limitado à
  biblioteca-padrão, mesmo que o resto do projeto ganhe uma dependência nova.
- O botão «Actualizar» do painel (`windows/painel.pyw`) decide se reinstala
  dependências comparando o `requirements.txt` entre commits — uma
  dependência nova só chega a quem usa o painel dessa forma, então o
  ficheiro tem de ser mesmo actualizado, nunca só o código.

## E · Língua

- **Português europeu**: comentários, docstrings, documentação em `docs/`,
  mensagens de commit, descrições de PR, o `README.md`.
- **Inglês**: tudo o que viaja para o modelo (as descrições em `TOOL_SCHEMAS`,
  as chaves dos resultados de ferramentas devolvidos ao modelo) e tudo o que o
  bot responde ao utilizador no Telegram — decisão de custo (ver `README.md`
  §1: os tokens em inglês custam menos, e o catálogo de ferramentas é
  reenviado em todas as chamadas).
- **Os campos mecânicos de um task file ficam em inglês, sempre** —
  `Status:`, `pending`/`in-progress`/`done`/`skipped`, `Depends on:`, `Why:`,
  `Files to touch:`, `Out of scope:`, `Never:`, `Accept:`, `Log`. Não são
  texto livre: é a forma que `TASK_FILE_FORMAT.md` documenta, e traduzi-los
  quebra qualquer ferramenta que venha a fazer parse mecânico destes
  ficheiros (ver `docs/TASK_FILE_FORMAT.md`).
- Manter a resposta do bot em inglês exige mais do que uma linha no prompt —
  a regra está na `_PERSONA` **e** repetida como `[answer in English]` colado
  a cada mensagem em `llm.py`. Mudar isto é uma decisão de produto, não um
  efeito colateral de outra tarefa.

## F · Fluxo de trabalho

- **Não há autorização permanente para comitar, abrir PR e fazer merge sem
  confirmação neste repositório** — ao contrário de outros repositórios do
  mesmo dono. Uma tarefa entrega até ao PR; o merge fica para o dono, salvo
  instrução explícita em contrário nesse momento.
- **Não há CI configurado.** O portão são os testes locais (ver `CLAUDE.md` →
  Comandos) — correm antes de cada commit, não depois. Um task file que peça
  "CI green" está enganado sobre este repositório; o `Accept:` certo é
  "testes locais a passar".
- Uma tarefa que muda algo visível para quem usa o bot actualiza o
  `README.md` no mesmo PR — a tabela de comandos, a secção de
  funcionalidades, o que for tocado.
- Uma tarefa que muda o estado do trabalho actualiza `docs/CONTEXT.md` no
  mesmo PR.

## G · Decisões já tomadas — não reabrir

- **Ninguém fica dono por escrever primeiro.** Fechado antes desta escrita
  (ver `README.md` §3.4.1, que descreve explicitamente o comportamento
  anterior como abandonado). Bastava alguém descobrir o username antes do
  dono legítimo — o primeiro id é sempre um acto deliberado agora.
- **O bot não responde em grupos nem canais**, só em conversa privada.
  Motivo de privacidade, não técnico — não re-propor "para poupar um passo".
- **O bot responde sempre em inglês**, mesmo quando lhe escrevem em
  português. Decisão de custo (ver §E), não uma limitação — percebe as duas
  línguas.
- **Sem framework de testes.** Cada ficheiro em `tests/` é um programa
  autónomo que sai com código 1 se algo falhar. Introduzir `pytest` é uma
  mudança de convenção, não uma tarefa isolada.
- **`windows/acessos.py` fica limitado à biblioteca-padrão**, mesmo que o
  resto do projeto ganhe dependências (ver §D) — a razão é a fuga do `.env`
  descrita em §B, não uma preferência de estilo.

## H · Nunca fazer isto

- Deixar alguém que não é o dono conceder ou retirar acesso — mesmo por um
  atalho "só desta vez".
- Registar o texto de uma mensagem ou de uma nota sem `LOG_MESSAGES=true`
  estar explicitamente ligado.
- Deixar o bot confirmar que gravou, corrigiu ou apagou alguma coisa sem uma
  chamada de ferramenta com resultado "ok" nesse mesmo turno — foi exactamente
  isto que causou a fuga de sintaxe interna documentada no Log de
  `docs/MELHORIAS.md` e no histórico de commits: o modelo inventou uma
  ferramenta inexistente e garantiu três vezes que tinha gravado algo que
  nunca mudou.
- Fazer `windows/acessos.py` importar `config.py`, directa ou
  indirectamente.
- Responder a uma mensagem de grupo ou canal.
- Versionar `.env`, `.env.bak`, a base de dados, ou ficheiros de registo.
- Traduzir os campos mecânicos de um task file (`Status:`, `Why:`,
  `Files to touch:`, `Out of scope:`, `Never:`, `Accept:`, `Depends on:`,
  `Log`) para português — ver §E.

---

## Log (acrescentar quando uma regra é criada, alterada ou retirada)

<!-- YYYY-MM-DD · #PR · o que mudou na regra, e porquê -->
2026-08-14 · — · primeira versão, extraída do `README.md`, de `safety.py`, de
`config.py`, de `database.py`/`scheduler.py` e do histórico de commits deste
repositório.
