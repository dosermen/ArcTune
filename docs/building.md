# Сборка exe

Как получить переносимые `arctune.exe` (CLI) и `arctune-gui.exe` (GUI) без
установки Python на целевой машине.

## Зависимости для сборки

Только на машине СБОРКИ (не на целевой) нужен Python 3.10+ и PyInstaller:

```powershell
pip install -e ".[build]"        # pyinstaller>=6.0
pip install -e ".[gui]"          # только для сборки arctune-gui.exe (PySide6)
```

На целевой машине Python не нужен — нужен только установленный Intel Arc Graphics
(драйвер с `IntelControlLib.dll`).

## Сборка

```powershell
# из корня репозитория
pyinstaller --clean --noconfirm packaging/arctune.spec      # -> dist/arctune.exe (CLI)
pyinstaller --clean --noconfirm packaging/arctune-gui.spec  # -> dist/arctune-gui.exe (GUI)
```

Результат: `dist/arctune.exe` (onefile, ~9 МБ, консольный) и
`dist/arctune-gui.exe` (onefile, ~48 МБ, без консольного окна).

Все флаги зафиксированы в `packaging/*.spec`, а не в истории шелла —
сборка воспроизводима. Зачем spec, а не ключи в команде:

- `console=True` — CLI: нужен консольный подсистемный код; у GUI наоборот
  `console=False`, иначе при запуске вылезает пустое чёрное окно;
- `upx=False` — UPX-пакер чаще даёт false-positive антивирусов;
- `excludes` — отсекаем `tkinter`/`pydoc`, меньше размер;
- GUI-spec тянет PySide6 с Qt-плагинами целиком: без платформенного плагина
  (`platforms/qwindows.dll`) exe падает при старте с
  «Could not find the Qt platform plugin».

## Проверка собранного exe

```powershell
.\dist\arctune.exe --version
.\dist\arctune.exe doctor      # находит IntelControlLib.dll; код 1 и [FAIL], если обязательных функций нет
.\dist\arctune.exe telemetry   # read-only, живые датчики
.\dist\arctune.exe vf --csv vf.csv
.\dist\arctune-gui.exe         # окно с 7 вкладками, опрос раз в секунду
```

Всё read-only проверяется сразу. Команды записи (`set`, `vf --apply`, `autofind`,
`fans --set`, `reset`, `log --rollback`) работают одинаково в exe и в python —
они используют тот же код и те же проверки диапазонов. `autofind` использует общий
движок `autotune/engine.py` с watchdog (TDR, заморозка телеметрии, аварийная
температура). GUI пишет **только** через `tuner.py`, то есть оба уровня проверок
действуют и там.

## Как exe находит IGCL

exe **не** содержит и не модифицирует драйвер. Он ищет
`IntelControlLib.dll`/`ControlLib.dll` в реальных путях Windows
(DriverStore, папка приложения Intel) — см. `loader.py`. Ничего копировать
рядом с exe не нужно.

## Windows Defender / антивирусы

onefile-exe, собранный PyInstaller, иногда детектится антивирусами как
«подозрительный» (типичный false-positive для упакованных бинарников). Если
чистовая машина ругается:

- собирайте с `upx=False` (уже так);
- отдавайте предпочтение onedir-сборке (см. ниже) или подпишите бинарник;
- покажите исходники — проект открыт и собирается из чистого pyproject.

### Вариант onedir (меньше false-positive, но не один файл)

В `packaging/arctune.spec` замените финальный `EXE(...)` на связку
`EXE` + `COLLECT` (стандартный шаблон PyInstaller onedir) — получите папку
`dist/arctune/` с `arctune.exe` внутри. Оттуда проще распространять через
корпоративные каналы, где одиночный exe может блокироваться.

## Что дальше

Сборка exe — артефакт для распространения. Дальнейшие пункты roadmap
(внутренний стресс-нагрузчик, софт-кривая вентиляторов) в отдельных итерациях.
