# `docs/` — como o trabalho acontece aqui

Cada alteração de fundo entra por um task file: uma especificação, um PR, e
uma linha de Log a explicar porquê foi construída assim. Isto não documenta o
código — o código documenta-se a si próprio, em português. Isto são **planos
e o raciocínio por trás deles**.

---

## Ler primeiro, por esta ordem

| # | Ficheiro | O que é |
|---|---|---|
| 1 | [`../CLAUDE.md`](../CLAUDE.md) | O repositório: arquitectura, comandos, fluxo de trabalho, convenções |
| 2 | [`GUARDRAILS.md`](GUARDRAILS.md) | **Vinculativo.** O que se pode e não pode fazer — identidade, acesso e posse dos dados, privacidade, dependências, língua, decisões fechadas, o que nunca fazer |
| 3 | [`CONTEXT.md`](CONTEXT.md) | O estado do mundo: o que já existe, o que está activo, o que foi de facto verificado |
| 4 | o teu task file | ver a tabela abaixo |

Mais um, quando precisares: [`TASK_FILE_FORMAT.md`](TASK_FILE_FORMAT.md) —
ler antes de escrever um task file **novo**.

---

## Activos

| Ordem | Ficheiro | Âmbito | Tarefas |
|---|---|---|---|
| 1 | [`MELHORIAS.md`](MELHORIAS.md) | Subconjunto pequeno e mecânico de "Melhorias futuras" (`README.md` §6): exportação `.ics`, cópias de segurança, tarefas (to-do), resumos agendados | M1…M4 |

## Completos

Nenhum ainda.

---

## Manter isto verdadeiro faz parte do trabalho

- Uma tarefa que muda algo visível a quem usa o bot **actualiza o
  `README.md` da raiz** no mesmo PR.
- Uma tarefa que muda o estado do trabalho **actualiza `CONTEXT.md`** no
  mesmo PR.
- Um task file **novo** entra nesta tabela no PR que o cria, e move-se para
  `archive/` no PR que termina a sua última tarefa.
