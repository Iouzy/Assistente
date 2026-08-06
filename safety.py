"""Funções de saneamento de texto vindo de fora.

Três fronteiras diferentes, três tratamentos — não são intermutáveis:

* `para_registo`  — texto que vai para o ficheiro de registo. Sem isto, uma
  mensagem com mudanças de linha escreve linhas falsas no `assistente.log`,
  indistinguíveis das verdadeiras (o formato é uma linha por acontecimento).
* `neutralizar_markdown` — texto de outra pessoa que vai aparecer numa
  mensagem formatada. Sem isto, uma etiqueta como `[toca aqui](https://falso)`
  é entregue pelo bot como um link verdadeiro, com a credibilidade dele.
* `limpar_nome` — nomes do Telegram usados no prompt. São escolhidos pelo
  próprio utilizador e vão parar ao prompt de sistema.

Módulo sem dependências: é importado pelo bot e pelo `llm`.
"""

from __future__ import annotations

import re

# Comprimentos máximos. Não são regras de segurança em si — servem para que
# um valor absurdo não inche o prompt, o registo ou uma mensagem.
MAX_NOME = 64
MAX_ETIQUETA = 64
MAX_REGISTO = 200

# Caracteres com significado no Markdown clássico do Telegram, que é o modo
# que o `bot.send_text` usa.
#
# São **removidos**, não escapados com barra invertida. O Markdown clássico do
# Telegram (ao contrário do MarkdownV2) não garante o escape com `\`: a barra
# tanto pode ser respeitada como aparecer tal e qual, conforme o contexto. Com
# os caracteres fora, não há parser nenhum que consiga formar um link — e um
# link falso vindo do bot é exactamente o que aqui se quer evitar.
#
# Só estes cinco. Um link precisa dos parênteses rectos (`[texto](url)`), por
# isso tirá-los já impede o link — e os parênteses normais podem ficar, senão
# uma etiqueta como «Ana (irmã)» saía estropiada.
_MARKDOWN = str.maketrans({c: None for c in "_*`[]"})

# Categoria Cc (controlo) menos nada: tiramos tudo, incluindo \n e \r.
_CONTROLO = re.compile(r"[\x00-\x1f\x7f]")


def para_registo(valor: object, limite: int = MAX_REGISTO) -> str:
    """Prepara um valor vindo de fora para ser escrito numa linha de registo.

    Tira caracteres de controlo (é assim que se forjam linhas falsas) e corta
    o resto ao comprimento máximo.
    """
    texto = _CONTROLO.sub(" ", str(valor))
    texto = re.sub(r"\s+", " ", texto).strip()
    if len(texto) > limite:
        texto = texto[: limite - 1] + "…"
    return texto


def neutralizar_markdown(texto: object) -> str:
    """Tira a um texto de outra pessoa a capacidade de formar formatação.

    Pensado para valores que o bot mostra a terceiros — a etiqueta de um
    utilizador no `/who`, por exemplo. Depois disto, o texto aparece como
    texto: não vira link, nem negrito, nem bloco de código.
    """
    return _CONTROLO.sub(" ", str(texto)).translate(_MARKDOWN)


def limpar_nome(nome: object, limite: int = MAX_NOME) -> str:
    """Nome de utilizador do Telegram pronto a entrar num prompt.

    O nome é escolhido por quem fala com o bot e vai para o prompt de sistema,
    por isso tiramos mudanças de linha (que permitiriam acrescentar instruções
    novas) e limitamos o tamanho.
    """
    limpo = _CONTROLO.sub(" ", str(nome or ""))
    limpo = re.sub(r"\s+", " ", limpo).strip()
    return limpo[:limite]


def limitar(texto: object, limite: int) -> str:
    """Corta um texto ao comprimento pedido, sem outras alterações."""
    return str(texto or "").strip()[:limite]
