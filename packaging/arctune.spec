# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller-спецификация ArcTune (одиночный console-exe `arctune.exe`).

Сборка:
    pyinstaller --clean --noconfirm packaging/arctune.spec

Почему здесь, а не в командной строке: воспроизводимость. Все флаги зафиксированы
в репозитории, а не в истории шелла. Результат — dist/arctune.exe (onefile).

Примечания:
  - console=True — это CLI, нужен консольный подсистемный код.
  - upx=False — UPX-пакер часто триггерит антивирусы на неизвестные бинарники.
  - Данные (docs/тесты) не тянем — exe нужен только для работы с IGCL.
"""

from pathlib import Path

# SPECPATH — каталог, где лежит этот .spec (packaging/).
ROOT = Path(SPECPATH).parent  # noqa: F821  (SPECPATH injected by PyInstaller)
SRC = ROOT / "src"

a = Analysis(
    [str(SRC / "arctuner" / "__main__.py")],
    pathex=[str(SRC)],
    binaries=[],
    datas=[],  # igcl_api.h/доки в exe не нужны — DLL грузится по реальному пути
    hiddenimports=[],  # igcl/ — обычная пачка, скрытых импортов нет
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
    name="arctune",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # не пакуем: меньше false-positive антивирусов
    console=True,  # CLI
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
