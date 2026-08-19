"""A versão do assistente — um sítio só, lido por toda a gente.

Quem a lê:
  * o painel de controlo, para a mostrar e para saber se há versão mais nova;
  * o `actualizacao.py`, que a compara com a última publicada em *Releases*;
  * o `instalador.iss`, que a escreve nas propriedades do executável;
  * o workflow de compilação, que confirma que a etiqueta do git lhe
    corresponde antes de publicar.

**Para lançar uma versão nova:** subir o número aqui, fazer commit, e criar a
etiqueta correspondente (`v1.2.0`). O resto é automático — ver
`.github/workflows/compilar.yml`.

O formato é `MAIOR.MENOR.CORRECCAO`, sem sufixos: é comparado número a número
pelo `actualizacao.py`, e um sufixo do género `1.2.0-beta` fá-lo-ia recusar a
comparação em vez de adivinhar.
"""

VERSAO = "1.0.0"
