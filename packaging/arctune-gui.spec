# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller-спецификация ArcTune GUI (одиночный windowed-exe `arctune-gui.exe`).

Сборка:
    pyinstaller --clean --noconfirm packaging/arctune-gui.spec

Отличия от arctune.spec:
  - точка входа — gui/main.py:run, а не CLI;
  - console=False: GUI-приложение без консольного окна (PyInstaller выставляет
    подсистему WINDOWS, иначе при запуске появляется чёрное окно);
  - тянем PySide6 (включая QtCharts для графиков) и Qt-плагины; без них exe
    стартует и сразу падает с "Could not find the Qt platform plugin".
"""

from pathlib import Path

# SPECPATH — каталог, где лежит этот .spec (packaging/).
ROOT = Path(SPECPATH).parent  # noqa: F821  (SPECPATH injected by PyInstaller)
SRC = ROOT / "src"

a = Analysis(
    [str(SRC / "arctuner" / "gui" / "main.py")],
    pathex=[str(SRC)],
    binaries=[],
    datas=[],  # igcl_api.h/доки в exe не нужны — DLL грузится по реальному пути
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "unittest", "pydoc"],  # отсекаем ненужное, меньше размер
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="arctune-gui",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # не пакуем: меньше false-positive антивирусов
    console=False,  # GUI: без консольного окна
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
