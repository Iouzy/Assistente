# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Assistente pessoal de Telegram, em Python, apoiado no modelo DeepSeek. Corre
num PC de casa (Windows, com painel de controlo próprio) ou num servidor Linux.

## Regras deste repositório

**Língua.** Comentários, docstrings, documentação, mensagens de commit e
descrições de PR **em português europeu**. Em inglês só o que viaja para o
modelo (descrições em `TOOL_SCHEMAS`, chaves dos resultados das ferramentas) e
o que o bot responde ao utilizador no Telegram — os tokens em inglês são mais
baratos e o catálogo de ferramentas é reenviado em todas as chamadas.

**Git.**

- Os commits vão em nome de `Iouzy <ytmp12295@gmail.com>` (já está no
  `git config` local do repositório). Não mudar o autor.
- **Sem rodapés de atribuição:** nada de `Co-Authored-By:`, `Claude-Session:`
  nem `🤖 Generated with…` nas mensagens de commit ou nos corpos dos PR. O
  histórico foi limpo destas linhas de propósito.
- **Nomes de branch sem prefixos de ferramenta** (nada de `claude/…`): um nome
  curto e descritivo, em português e com hífenes — `painel-permissoes`,
  `lembretes-recorrentes`.
- `main` é a branch principal. Trabalha-se numa branch e abre-se PR contra a
  `main`.
- Mensagens de commit no estilo do histórico: assunto curto no presente
  («Painel actualiza-se a si próprio e às dependências») e um corpo que explica
  **porquê**, não o que o diff já mostra.

## Comandos

```bash
pip install -r requirements.txt
cp .env.example .env          # preencher TELEGRAM_TOKEN e DEEPSEEK_API_KEY
python main.py                # arranca o bot (Ctrl+C encerra ordenadamente)
```

Testes — não há framework nem `pytest`: cada ficheiro é um programa que corre
verificações e sai com código 1 se alguma falhar. Para correr uma verificação
isolada, edita o ficheiro ou copia o bloco; não há forma de seleccionar por
nome.

```bash
python tests/test_tools.py    # datas, as 10 ferramentas, BD, acesso, lembretes reais (~30 s)
python tests/test_llm.py      # tool calling, cache, memória, ponte scheduler↔asyncio
python tests/test_acessos.py  # permissões geridas pelo painel (não abre janelas)
```

Os três substituem o cliente DeepSeek por um duplo e **não gastam um token**.
Escrevem numa base de dados temporária (definem `DATABASE_PATH` antes de
importar o `config`) — nunca tocam no `assistente.db` real.

```bash
python tests/test_tool_choice.py   # ⚠ fala com a API a sério e custa cêntimos
```

Só se justifica depois de mexer nas descrições em `TOOL_SCHEMAS`: mostra que
ferramenta o modelo escolhe para 26 frases-tipo.

## Arquitectura

O que não se percebe lendo um ficheiro só:

**A ordem do prompt é uma decisão de custo, não de estilo** (`llm.py`). A
DeepSeek desconta prefixos repetidos entre chamadas, mas só enquanto o início
do pedido for byte a byte igual. Por isso tudo o que varia — data e hora,
agenda do dia, resumo da memória — vai colado à **última** mensagem, e nunca no
prompt de sistema. Mexer nisto não parte testes nenhuns; parte a factura.

**Memória em duas camadas** (`llm.py`). Curto prazo em RAM (as últimas
`MAX_HISTORY_MESSAGES` mensagens); quando cresce, as mais antigas são
condensadas pelo modelo num resumo na tabela `summaries`. Uma conversa curta
nunca atinge o limite e só existe em RAM — daí o `flush_idle()` periódico e o
`flush_all()` no encerramento. **É por isso que matar o processo à força perde
memória** e que o painel pede a paragem por ficheiro-sentinela em vez de
`terminate()`.

**A base de dados é a fonte de verdade dos lembretes, não o APScheduler**
(`scheduler.py`). Grava-se primeiro na tabela `reminders`, agenda-se depois; no
arranque, `restore_pending_reminders()` reconstrói os jobs. Um reinício não
perde nada.

**Ponte thread → event loop** (`main.py`, `build_notifier`). O APScheduler corre
numa thread própria e não pode aguardar corotinas; o envio no Telegram é
assíncrono. O scheduler nunca envia mensagens — chama um *notifier* que faz
`run_coroutine_threadsafe` para o loop do `python-telegram-bot`. O cliente
`openai` é síncrono e é chamado com `asyncio.to_thread`.

**Controlo de acesso, três vias para a mesma lista** (`bot.py`, `windows/acessos.py`).
O porteiro (`guard_access`) decide a partir de uma cópia em memória
(`_acesso_cache`). Se `ALLOWED_USER_IDS` estiver preenchido no `.env`, essa
lista fixa manda e a base de dados é ignorada; senão vale a tabela `access`,
alimentada pelo `/allow`, pelo painel do Windows, ou pela primeira pessoa que
escrever ao bot (fica dona). Como o painel é **outro processo**, o `main.py`
relê a tabela de 10 em 10 segundos (`watch_access_list`) — sem isso, uma
permissão dada no painel só valia depois de reiniciar.

**O painel do Windows é um processo à parte** (`windows/painel.pyw`). Arranca o
`main.py` como filho e canaliza a saída para a caixa de texto; pára-o criando
`.stop-assistente`, que o `main.py` vigia (em Windows não há sinais fiáveis
para um processo sem consola). O `windows/acessos.py` **não pode importar o
`config.py`**: carregaria o `.env` para o ambiente do painel e o bot arrancado a
seguir herdava essas variáveis, ficando surdo a alterações feitas ao ficheiro
depois de o painel abrir. Por isso usa só a biblioteca-padrão e repete o DDL da
tabela `access` — o que também deixa dar permissões antes do primeiro arranque.

**Concorrência na base de dados** (`database.py`). Uma única ligação com
`check_same_thread=False`, serializada por um `RLock`, mais modo `WAL` — que é
também o que deixa o painel escrever com o bot a correr. Datas sempre em texto
ISO-8601 **com fuso**, para as comparações lexicográficas do SQLite baterem
certo.

## Armadilhas

- `tools.py` é o contrato com o modelo: mudar um nome ou uma descrição em
  `TOOL_SCHEMAS` muda o comportamento sem partir nenhum teste offline. Correr o
  `test_tool_choice.py` a seguir.
- `config.py` valida no arranque e levanta `ConfigError` com instruções — é o
  sítio certo para apanhar erros de configuração, não o meio do código.
- O `.env` só é lido no arranque: alterá-lo obriga a reiniciar o bot.
- O painel corre o código que carregou ao abrir; se `windows/painel*` ou
  `windows/acessos*` mudarem, o botão «Actualizar» propõe reabri-lo.
