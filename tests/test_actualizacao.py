"""Testa a verificação de versões novas e a leitura da resposta do GitHub.

Não fala com a rede: a API é substituída por um duplo que devolve o JSON que
o GitHub devolveria. O que interessa testar aqui não é o GitHub — é a decisão
que se toma a partir do que ele responde, e essa decisão tem duas maneiras de
correr mal em silêncio: nunca avisar de uma versão nova, ou avisar sempre.

Escrito do ponto de vista de quem carrega no botão: se `ha_versao_nova()`
mentir, ou o utilizador fica preso numa versão antiga sem o saber, ou passa a
vida a descarregar a mesma.
"""
import json
import pathlib
import sys

# Em Windows, redirecionar a saída para um ficheiro (`> resultado.txt`) faz o
# Python largar o UTF-8 e usar a codificação local (cp1252), que não sabe
# escrever emojis — e o teste rebentava com UnicodeEncodeError logo na
# primeira linha de resultado. Forçamos UTF-8 na saída.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

RAIZ = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

import actualizacao  # noqa: E402
import versao  # noqa: E402

falhas = []


def check(nome, condicao, detalhe=""):
    marca = "OK " if condicao else "FALHA"
    print(f"[{marca}] {nome}" + (f" -> {detalhe}" if detalhe else ""))
    if not condicao:
        falhas.append(nome)


# --- comparação de versões --------------------------------------------------
check("lê «v1.2.3»", actualizacao.analisar("v1.2.3") == (1, 2, 3))
check("lê sem o v", actualizacao.analisar("1.2.3") == (1, 2, 3))
check("aceita espaços à volta", actualizacao.analisar("  v2.0  ") == (2, 0))
check("recusa o que não percebe", actualizacao.analisar("1.2.0-beta") is None)
check("recusa texto", actualizacao.analisar("a última") is None)
check("recusa vazio", actualizacao.analisar("") is None)

check("1.0.1 é mais recente do que 1.0.0", actualizacao.mais_recente("1.0.1", "1.0.0"))
check("1.1.0 é mais recente do que 1.0.9", actualizacao.mais_recente("1.1.0", "1.0.9"))
check("2.0.0 é mais recente do que 1.99.99", actualizacao.mais_recente("2.0.0", "1.99.99"))
check("a mesma versão não é mais recente", not actualizacao.mais_recente("1.0.0", "1.0.0"))
check("uma versão antiga não é mais recente", not actualizacao.mais_recente("0.9.0", "1.0.0"))

# `10 > 9` como número, mas `"10" < "9"` como texto. Comparar versões por
# ordem alfabética é o erro clássico, e só aparece à décima versão.
check("compara números, não texto", actualizacao.mais_recente("1.10.0", "1.9.0"))

# 1.2 e 1.2.0 são a mesma versão. Sem isto, uma etiqueta `v1.2` fazia o
# programa oferecer-se para actualizar para a versão que já tinha.
check("1.2 e 1.2.0 são a mesma", not actualizacao.mais_recente("1.2", "1.2.0"))
check("1.2.1 é mais recente do que 1.2", actualizacao.mais_recente("1.2.1", "1.2"))

# Perante uma etiqueta que não percebe, não avisa. É a escolha certa: avisar
# de uma versão que não se sabe comparar dava um aviso que nunca mais saía.
check("etiqueta estranha não gera aviso", not actualizacao.mais_recente("mais-recente", "1.0.0"))

# --- leitura da resposta do GitHub ------------------------------------------
def responder(dados):
    """Substitui o pedido à rede por uma resposta preparada."""
    actualizacao._pedir = lambda url: json.dumps(dados).encode("utf-8")


RESPOSTA = {
    "tag_name": "v9.9.9",
    "body": "Notas da versão.",
    "assets": [
        {"name": "Assistente.exe",
         "browser_download_url": "https://exemplo/Assistente.exe"},
        {"name": "Assistente-instalador-9.9.9.exe",
         "browser_download_url": "https://exemplo/Assistente-instalador-9.9.9.exe"},
    ],
}

_pedir_original = actualizacao._pedir
try:
    responder(RESPOSTA)
    ultima = actualizacao.ultima_versao()
    check("lê a etiqueta sem o v", ultima["versao"] == "9.9.9", ultima["versao"])
    check("lê as notas", ultima["notas"] == "Notas da versão.")

    # O `.exe` solto está lá e vem primeiro na lista. Escolher esse dava uma
    # actualização que substituía um ficheiro sem fechar o programa a correr,
    # sem refazer atalhos e sem aparecer nas aplicações instaladas.
    check("escolhe o instalador e não o .exe solto",
          ultima["url"].endswith("Assistente-instalador-9.9.9.exe"), ultima["url"])

    nova = actualizacao.ha_versao_nova()
    check("com uma versão posterior publicada, avisa", nova is not None)

    responder({**RESPOSTA, "tag_name": f"v{versao.VERSAO}"})
    check("com a mesma versão publicada, não avisa", actualizacao.ha_versao_nova() is None)

    responder({**RESPOSTA, "tag_name": "v0.0.1"})
    check("com uma versão anterior publicada, não avisa",
          actualizacao.ha_versao_nova() is None)

    # Uma release criada antes de a compilação terminar não tem instalador
    # nenhum anexado. Avisar está certo; deixar descarregar um endereço vazio
    # não — o painel tem de poder dizer «tente daqui a nada».
    responder({**RESPOSTA, "tag_name": "v9.9.9", "assets": []})
    sem_anexo = actualizacao.ha_versao_nova()
    check("uma release ainda sem instalador dá url vazio",
          sem_anexo is not None and sem_anexo["url"] == "")

    responder({"body": "sem etiqueta"})
    try:
        actualizacao.ultima_versao()
        check("uma resposta sem etiqueta é recusada", False)
    except actualizacao.ErroActualizacao:
        check("uma resposta sem etiqueta é recusada", True)
finally:
    actualizacao._pedir = _pedir_original

# --- descarga: o que se recusa a fazer --------------------------------------
try:
    actualizacao.descarregar("")
    check("recusa descarregar sem endereço", False)
except actualizacao.ErroActualizacao:
    check("recusa descarregar sem endereço", True)

# O ficheiro descarregado é executado a seguir. Por http, quem estivesse no
# caminho escolhia o que corria nesta máquina.
try:
    actualizacao.descarregar("http://exemplo/instalador.exe")
    check("recusa um endereço que não seja https", False)
except actualizacao.ErroActualizacao as exc:
    check("recusa um endereço que não seja https", "https" in str(exc), str(exc))

# --- a versão do próprio programa -------------------------------------------
check("o versao.py tem um número que se sabe comparar",
      actualizacao.analisar(versao.VERSAO) is not None, versao.VERSAO)

print()
if falhas:
    print(f"❌ {len(falhas)} teste(s) falharam: {falhas}")
    raise SystemExit(1)
print("✅ Todos os testes da actualização passaram.")
