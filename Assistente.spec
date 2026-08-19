# Receita do PyInstaller: transforma o projecto num único `Assistente.exe`.
#
# Corre-se com:  pyinstaller --noconfirm Assistente.spec
# (é o que o .github/workflows/compilar.yml faz, em windows-latest — o
# PyInstaller não faz compilação cruzada, um `.exe` só se compila em Windows.)
#
# O executável é um só, com os dois modos lá dentro: sem argumentos abre o
# painel, com `--bot` corre o bot. Ver `assistente.py`.

import re
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

# --- Propriedades do ficheiro (o separador «Detalhes» do Windows) ----------
# Escritas aqui a partir do `versao.py` em vez de num ficheiro à parte: um
# `version_info.txt` mantido à mão ficava desactualizado à primeira versão em
# que alguém se esquecesse dele, e o Windows passava a mostrar um número
# errado sem nada a assinalá-lo.
VERSAO = re.search(
    r'^VERSAO\s*=\s*"([^"]+)"', Path("versao.py").read_text(encoding="utf-8"), re.M
).group(1)

# O formato exige exactamente quatro números.
_n = tuple((list(int(p) for p in VERSAO.split(".")) + [0, 0, 0, 0])[:4])

Path("version_info.txt").write_text(
    f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={_n}, prodvers={_n}, mask=0x3f, flags=0x0,
                    OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('080904b0', [
        StringStruct('CompanyName', 'Assistente'),
        StringStruct('FileDescription', 'Assistente pessoal no Telegram'),
        StringStruct('FileVersion', '{VERSAO}'),
        StringStruct('InternalName', 'Assistente'),
        StringStruct('OriginalFilename', 'Assistente.exe'),
        StringStruct('ProductName', 'Assistente'),
        StringStruct('ProductVersion', '{VERSAO}'),
    ])]),
    # 0x0809 = inglês (Reino Unido), 1200 = UTF-16. Não há identificador de
    # português europeu que o Windows aceite em todas as versões, e o que
    # importa aqui é o texto, que já está em português.
    VarFileInfo([VarStruct('Translation', [0x0809, 1200])]),
  ]
)
""",
    encoding="utf-8",
)

datas = []
binaries = []
hiddenimports = []

# O NiceGUI não é só código: leva HTML, CSS, JavaScript e as bibliotecas do
# Quasar/Vue. Sem os ficheiros de dados, o `.exe` compila e depois serve uma
# página em branco — falha que só aparece em execução.
for pacote in ("nicegui", "webview"):
    try:
        pacote_datas, pacote_binaries, pacote_hidden = collect_all(pacote)
    except Exception:
        # O `pywebview` só é instalado em Windows (ver requirements-painel.txt),
        # e é lá que o `.exe` é compilado. Fora de Windows este spec continua a
        # correr — sem janela nativa, que é o que o painel já faz nesse caso.
        print(f"[spec] {pacote} não está instalado — a compilar sem ele.")
        continue
    datas += pacote_datas
    binaries += pacote_binaries
    hiddenimports += pacote_hidden

# Descobertos por importação dinâmica, que a análise estática não vê:
#   * o dateparser carrega os idiomas por nome, em execução;
#   * o APScheduler resolve os tipos de trigger por texto de configuração;
#   * o tzdata é a base de fusos horários — sem ela, `ZoneInfo("Europe/Lisbon")`
#     rebenta em Windows, que não tem base de fusos própria.
hiddenimports += collect_submodules("dateparser")
hiddenimports += collect_submodules("apscheduler")
hiddenimports += ["tzdata"]

for pacote in ("dateparser", "dateparser_data", "tzdata"):
    pacote_datas, _, _ = collect_all(pacote)
    datas += pacote_datas

# O `.env.example` viaja dentro do executável: é a partir dele que o painel
# cria o `.env` da pasta de dados na primeira execução, com os comentários e
# os valores por omissão que documentam cada definição.
datas += [(".env.example", ".")]


a = Analysis(
    ["assistente.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    # Módulos de interface que nada aqui usa. Sem os excluir, o PyInstaller
    # arrasta o Tk inteiro (uns 10 MB) por causa de importações opcionais de
    # bibliotecas científicas.
    excludes=["tkinter", "matplotlib", "PyQt5", "PyQt6", "PySide2", "PySide6"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="Assistente",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # o UPX poupa uns MB e faz os antivírus desconfiarem — não compensa
    # Sem consola: isto é um programa de janela. O bot, lançado com `--bot`,
    # herda esta definição — é por isso que o painel lê a saída dele por um
    # `stdout` redireccionado e não por uma janela preta.
    console=False,
    disable_windowed_traceback=False,
    icon=None,
    version="version_info.txt",
)
