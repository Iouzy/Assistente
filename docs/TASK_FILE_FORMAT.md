# Formato de um task file — a forma que todo `docs/*.md` de tarefas segue

> **Ler antes de escrever um task file novo.** Tem duas metades: a humana
> (porque a forma é a que é) e a mecânica (a sintaxe exacta que uma
> ferramenta de automação, se este repositório vier a usar uma, faz *parse*
> literal). As duas importam mesmo que nenhuma automação exista ainda — a
> metade humana é o que permite a uma sessão fria entregar correctamente.

---

## A ideia central

Um task file é escrito para **uma sessão que não sabe nada**. Abre a frio, lê
`CLAUDE.md` + `docs/GUARDRAILS.md` + `docs/CONTEXT.md` + este task file, e tem
de conseguir entregar um PR correcto sem perguntar nada.

Três consequências:

1. **Registar o porquê, não o quê.** O diff já mostra o que mudou.
2. **Escrever as decisões como fechadas**, ou voltam a ser propostas de X em
   X sessões.
3. **Ser honesto sobre o que não foi verificado.** Os testes locais passarem
   e "funciona mesmo" são duas afirmações diferentes.

## O que um task file não repete

| Em vez de repetir... | ...diz só | ...e aponta para |
|---|---|---|
| Regras globais | "aplica-se por inteiro", mais as 3–5 que mais mordem aqui | `docs/GUARDRAILS.md` |
| Estado do trabalho | nada — actualiza-se, não se duplica | `docs/CONTEXT.md` |
| Convenções, comandos, fluxo de entrega | nada | `CLAUDE.md` |

Um ficheiro que reexplica as regras vai divergir delas — e aí dois documentos
discordam sobre o que é permitido.

---

## Secções obrigatórias, por ordem

### 1 · Título e Conceito

```markdown
# <Nome> — task file

> **Conceito.** Para que serve este ficheiro, em três ou quatro frases: o
> problema, a forma da resposta, o que muda para quem usa o bot.
>
> Entrega-se como N tarefas autónomas (X1…Xn). Cada tarefa é um PR.
```

Diz o que **quem usa o bot** ganha, não o que o código ganha.

### 2 · Como usar

```markdown
> Faz a próxima em `docs/<FICHEIRO>.md`.
```

Diz explicitamente: fazer a **primeira tarefa cujo Status é `pending`**, de
cima para baixo, só essa; entregar pelo fluxo do `CLAUDE.md`; actualizar
Status, o Log, **e o `CONTEXT.md`** no mesmo PR; parar em vez de saltar se
uma tarefa estiver bloqueada por uma decisão.

**Especificar a marca de progresso** — um ficheiro trabalhado por ordem deve
abrir cada resposta com ela, antes de qualquer chamada a ferramentas:

```
**Feito:** M1 ✓
**Agora:** M2 — <do que se trata>
**Falta:** M3…Mn (n)
```

### 3 · Guardrails

```markdown
**`docs/GUARDRAILS.md` aplica-se por inteiro.** As que mais mordem aqui: …

**Extra, específico deste ficheiro:** …
```

Uma regra que devia aplicar-se a **tudo** vai para `GUARDRAILS.md`, não aqui.

### 4 · Legenda de Status

```markdown
`pending` · `in-progress (PR #n)` · `done (PR #n)` · `skipped (motivo)`
```

Os quatro valores ficam em inglês — são o que a sintaxe mecânica reconhece
(ver abaixo).

### 5 · Contexto partilhado

O que as tarefas **partilham** vai aqui uma vez, não repetido por tarefa: a
evidência (se o ficheiro nasceu de defeitos observados, o quê e onde), um
delta ao modelo de dados, um modelo sugerido por tarefa.

### 6 · Decisões já tomadas

Uma lista curta do que está **fechado** no âmbito deste ficheiro. O quê, o
porquê, quando. Uma decisão que sobrevive a este ficheiro promove-se a
`GUARDRAILS.md`.

### 7 · As tarefas

Ver "A parte mecânica", abaixo, para a forma exacta.

### 8 · Sobras, e emendas a outros ficheiros (opcional)

- **Sobras** — achados pequenos demais para serem tarefa, com onde vivem.
- **Emendas a outros ficheiros** — se criar este ficheiro mudou algo noutro.

### 9 · Ordem (e dependências)

```
M1 → M2 → M3 → …
```

Ou, se as tarefas forem independentes, dizer isso.

### 10 · Log

Uma entrada por tarefa entregue, mais recente primeiro.

---

## A parte mecânica

Isto **não é estilo** — é a sintaxe que qualquer *parser* automático (se este
repositório vier a ser conduzido por uma ferramenta desse tipo) reconhece
literalmente.

### Cabeçalho

```
### M3 · Título curto no imperativo — Status: pending
```

- `M` = uma letra maiúscula, partilhada por todas as tarefas deste ficheiro.
- O separador antes do título é **U+00B7 MIDDLE DOT** (`·`) — não um hífen.
- O separador antes de `Status` é **U+2014 EM DASH** (`—`) — não um hífen
  nem um travessão normal.
- `Status` ∈ `pending` · `in-progress (PR #n)` · `done (PR #n)` ·
  `skipped (motivo)`.

### Corpo

```markdown
**Depends on:** M1 (ou "nothing")

**Why:** o porquê, em termos de quem usa o bot.

**Files to touch:**
- `ficheiro.py` — o que muda ali

<a especificação: prosa e esboços, o suficiente para construir, não mais>

**Out of scope:** o que fica para depois, nomeado.

**Never:** o desvio tentador, nomeado, e porque está errado.

**Accept:** resultados observáveis, separados por ponto e vírgula, a acabar
em "testes locais a passar" — **não** "CI green": este repositório não tem
CI (ver `CLAUDE.md`).
```

`Depends on:`, `Why:`, `Files to touch:`, `Out of scope:` e `Accept:` ficam em
**inglês**, sempre — são os campos que uma leitura mecânica reconhece por
esse texto exacto. `Never:` é opcional, mas escrever um sempre que houver um
desvio plausível.

- **Uma tarefa é um PR.** Se não couber, dividir.
- **`Out of scope` vs `Never`:** *out of scope* adia trabalho; **`Never`**
  nomeia uma armadilha — sobrescrever dados do utilizador, apagar em vez de
  corrigir, tornar obrigatório algo que era opcional, religar algo que um
  guardrail proíbe.
- **`Accept` é observável.** "Funciona bem" não é aceitação; um resultado
  nomeado e verificável é.

### O Log

```markdown
## Log (uma linha por tarefa entregue: data · tarefa · PR · nota)

YYYY-MM-DD · M3 · #PR · <um parágrafo denso: o que foi entregue, o
raciocínio por trás das decisões não óbvias, o que foi rejeitado e porquê> ·
Verificado: <o que foi de facto exercitado, e onde>
```

O que faz uma boa entrada:

- explica uma **escolha**, não uma mudança;
- regista o que foi rejeitado, e porque perdeu;
- diz o que foi verificado — testes locais, ou uso real, e se nunca chegou a
  um dispositivo real, dizer isso;
- é um parágrafo, não tópicos.

---

## Nomenclatura

- Um ficheiro por **corpo de trabalho coerente**, nomeado pelo que é.
- IDs de tarefa são uma letra maiúscula mais um número (`M3`) — a letra
  corresponde ao ficheiro. Confirmar que a letra está livre, incluindo num
  eventual `docs/archive/`.
- Nunca renumerar uma tarefa já entregue. Acrescentar; se a ordem tiver de
  mudar, dizer isso na secção Order em vez de reescrever IDs que PRs e linhas
  de Log já referenciam.

## Ficheiros que não são task files

| Ficheiro | Forma | Quem o actualiza |
|---|---|---|
| `GUARDRAILS.md` | regras, por secção, mais uma lista do que nunca fazer | qualquer PR que acrescente ou retire uma regra |
| `CONTEXT.md` | estado do trabalho | **todo PR que entrega uma tarefa** |
| `README.md` (em `docs/`) | índice | qualquer PR que acrescente ou arquive um task file |

---

## O esqueleto

Copiar por inteiro para um task file novo — ver
[`docs/MELHORIAS.md`](MELHORIAS.md) para um exemplo real já preenchido.

```markdown
# <Nome> — task file

> **Conceito.** …
>
> Entrega-se como N tarefas autónomas (X1…Xn). Cada tarefa é um PR.

## Como usar

> Faz a próxima em `docs/<FICHEIRO>.md`.

<regras vinculativas: primeira tarefa pending, só essa, a marca de
progresso, entregar pelo fluxo do CLAUDE.md, Status + Log + CONTEXT.md no
mesmo PR, parar se bloqueada>

---

## Guardrails

**`docs/GUARDRAILS.md` aplica-se por inteiro.** As que mais mordem aqui: …

**Extra, específico deste ficheiro:** …

## Legenda de Status

`pending` · `in-progress (PR #n)` · `done (PR #n)` · `skipped (motivo)`

---

## Contexto partilhado

## Decisões já tomadas — não reabrir

---

## X1 · … — Status: pending

**Depends on:** nothing

**Why:**

**Files to touch:**

**Out of scope:**

**Never:**

**Accept:** … ; testes locais a passar.

---

## Order

X1 → X2 → …

---

## Log (uma linha por tarefa entregue: data · tarefa · PR · nota)
```

---

## Log (acrescentar quando este formato muda)

<!-- YYYY-MM-DD · #PR · o que mudou no formato, e o que substituiu -->
2026-08-14 · — · primeira versão, adaptada do formato usado em
`iouzy/native-android` e reconhecido por `iouzy/claude-taskrunner`, traduzida
para português com os campos mecânicos mantidos em inglês (ver
`GUARDRAILS.md` §E) e "CI green" substituído por "testes locais a passar",
porque este repositório não tem CI.
