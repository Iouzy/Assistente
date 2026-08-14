# Context — o estado do mundo

> Actualizado no mesmo PR que qualquer tarefa entregue. É o ficheiro que
> responde "onde estamos?" sem ler o Log de cada task file.

## 1 · O que este projeto é

Um assistente pessoal de Telegram em Python, apoiado na API DeepSeek, que
gere agenda, lembretes, notas, uma linha do tempo e preferências duradouras
via linguagem natural. Corre num PC de casa (com painel de controlo em
`windows/`) ou num servidor Linux. Ver `CLAUDE.md` para a arquitectura e
`README.md` para a referência completa de instalação e utilização.

## 2 · Onde procurar o quê

| Pergunta | Ver |
|---|---|
| O que posso e não posso fazer? | `GUARDRAILS.md` — vinculativo |
| Como funciona o controlo de acesso? | `GUARDRAILS.md` §B, e `README.md` §3.4.1 |
| Porque é que algo tem o aspecto que tem? | O Log do task file que o entregou, ou o histórico de commits (cada um explica o porquê, não só o quê) |
| Uma decisão que volta a aparecer? | `GUARDRAILS.md` §G |
| O que já foi testado e como? | Secção 4, abaixo |

## 3 · O trabalho, num relance

| Ficheiro | Âmbito | Tarefas |
|---|---|---|
| [`MELHORIAS.md`](MELHORIAS.md) | Um subconjunto pequeno e bem definido da lista "Melhorias futuras" do `README.md` §6 — exportação `.ics`, cópias de segurança, gestão de tarefas, resumos agendados | M1…M4 |

### A ordem

As quatro tarefas de `MELHORIAS.md` são independentes entre si (ver a secção
Order desse ficheiro) — podem ser feitas por qualquer ordem, cada uma é um PR
próprio.

## 4 · O que foi de facto verificado

A distinção entre "os testes passam" e "uma pessoa viu funcionar" é onde
vivem os defeitos reais — ser explícito sobre qual das duas afirmações é
verdadeira.

### Automatizado

Quatro scripts em `tests/`, sem framework, cada um um programa que sai com
código 1 se falhar — correm com um duplo do cliente DeepSeek, **sem gastar
tokens**, contra uma base de dados temporária:

- `test_tools.py` — datas em linguagem natural, as ferramentas, a base de
  dados, lembretes reais (dispara-os a sério, dentro do próprio teste).
- `test_llm.py` — *tool calling*, cache de prompt, memória, a ponte
  scheduler↔asyncio.
- `test_acessos.py` — permissões geridas pelo painel (não abre janelas).
- `test_seguranca.py` — escrito do ponto de vista de quem ataca: cada bloco
  corresponde a uma falha concreta que existiu (ver o commit
  `473f860` — "Fecha o bot a quem não foi autorizado e corrige 20 falhas de
  segurança"), e passa quando o abuso deixa de funcionar.

Não há CI configurado neste repositório — estes quatro scripts **são** o
portão, e correm-se à mão antes de cada commit (ver `CLAUDE.md`).

Há um quinto, `test_tool_choice.py`, que fala a sério com a API DeepSeek e
custa cêntimos — não faz parte do portão, só se corre depois de mexer nas
descrições em `TOOL_SCHEMAS`.

### Em uso real, por uma pessoa

**Não documentado aqui até agora.** O histórico de commits sugere uso real —
o commit da fuga de sintaxe interna (`33a205e`) descreve um caso visto "no
telemóvel" — mas não há um registo estruturado de o que foi exercitado num
dispositivo real vs. só nos testes offline. Uma tarefa que altere algo do
lado do Telegram (mensagens, botões, formatação) devia dizer aqui, no seu
Log, se foi vista a funcionar num chat real ou só nos testes com o duplo.

## 5 · Conhecido e ainda não escrito nalgum lado

- A limitação da hora repetida do horário de verão (ver `README.md` §5.4.1)
  — conhecida, documentada, aceite como está; não é uma tarefa porque
  corrigi-la a sério implica migrar o esquema para UTC.
- Os itens maiores de "Melhorias futuras" (`README.md` §6) que não entraram
  em `MELHORIAS.md` — ver secção 6, abaixo.

## 6 · Perguntas em aberto para o dono

A lista "Melhorias futuras" do `README.md` tinha catorze itens; só quatro
(exportação `.ics`, cópias de segurança, gestão de tarefas, resumos
agendados) entraram em `docs/MELHORIAS.md` como tarefas prontas a fazer — são
mecânicos, seguem padrões já existentes no código (`schedule_recurring`,
tabelas ao estilo de `notes`/`reminders`), e não pedem nenhuma decisão de
produto ou de fornecedor externo antes de começar.

Os restantes ficam de fora **porque cada um pede uma decisão do dono
primeiro**, não porque sejam menos importantes:

- **Google Calendar / Outlook** — sincronização bidireccional pede OAuth,
  gestão de tokens por utilizador, e uma decisão sobre o que fazer em
  conflito (o Calendar muda um evento que o bot também mudou).
- **Multi-utilizador / modo família com agendas partilhadas** — hoje os
  dados são isolados por utilizador (ver `GUARDRAILS.md` §C); partilhar
  implica decidir o que é privado dentro de uma "família".
- **Pesquisa na web (DuckDuckGo)** — nova dependência externa, novo custo,
  nova superfície de conteúdo não confiável a chegar ao prompt.
- **Integração de email** — outra credencial, outro protocolo, e a mesma
  pergunta do Calendar sobre o que é fonte de verdade.
- **WhatsApp via Twilio** — outro canal inteiro, conta Twilio, custo por
  mensagem.
- **Mensagens de voz (Whisper)** — nova dependência, custo por chamada,
  decisão sobre onde correr a transcrição.
- **Lembretes por localização** — pede acesso a localização do lado do
  Telegram/dispositivo, fora do que o bot faz hoje.
- **Registo de despesas com relatórios** — pequeno pedaço mecânico
  (parecido com M3), mas "relatórios mensais" é suficientemente vago para
  merecer uma conversa sobre o formato antes de virar tarefa.
- **Perfis de personalidade configuráveis** — mexe na `_PERSONA` (ver
  `GUARDRAILS.md` §E), que hoje é uma decisão deliberada e única; múltiplos
  perfis multiplica o que há a manter consistente.
- **Cifra ponta-a-ponta da base de dados** (SQLCipher ou por campo) — pede
  decidir onde vive a chave, e o que acontece a instalações já existentes.
- **Eventos recorrentes** — muda o modelo de dados de `events` de forma
  não trivial (uma ocorrência vs. uma série).
- **Exportação de cópias de segurança automáticas para fora da máquina**
  — M2 cobre a cópia local; enviar para outro sítio (nuvem, outro disco)
  é uma escolha de destino que o dono não fez ainda.

Quando o dono decidir o suficiente sobre um destes para ter um `Why:`, um
`Files to touch:` e um `Accept:` reais, vira uma tarefa nova — em
`MELHORIAS.md` se ainda couber lá, ou num task file próprio se for grande
demais para uma tarefa só.

---

## Log (acrescentar uma linha por PR que muda o estado do trabalho)

<!-- YYYY-MM-DD · #PR · o que mudou no estado do trabalho, numa linha -->
2026-08-14 · — · primeira versão: `docs/` criado, `GUARDRAILS.md` e este
ficheiro extraídos do estado actual do repositório, `MELHORIAS.md` aberto com
M1…M4.
