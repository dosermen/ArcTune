# Разработка

## Требования

- **Windows 10/11 x64** (IGCL — Windows-only)
- **Python 3.10+**
- **Intel Arc Graphics драйвер** 32.0.101.9xxx+ установлен (есть `IGCL.dll`)
- Дискретная **Intel Arc** карта (проверено на B580)

Проверить наличие драйвера/IGCL:
```powershell
python -m arctuner doctor
```

`doctor` возвращает `0`, если обязательные функции IGCL найдены, и `1` с сообщением
`[FAIL]`, если библиотека не загрузилась или в ней нет обязательных `ctl*`-функций
(старый драйвер).

---

## Настройка окружения

```powershell
# Вход в папку проекта (после git clone)
cd arctune

# Виртуальное окружение
python -m venv .venv
.venv\Scripts\Activate.ps1

# Установка в режиме разработки (с тестовыми зависимостями)
pip install -e ".[dev]"
```

---

## Структура и куда что класть

См. [architecture.md](architecture.md). Кратко:

- Правки, связанные с IGCL.dll напрямую (новые функции/структуры) → `src/arctuner/igcl/`
- Логика чтения датчиков → `src/arctuner/telemetry.py`
- Логика записи настроек → `src/arctuner/tuner.py`
- Авторазгон (общий для CLI и GUI) → `src/arctuner/autotune/`; `autofind.py` —
  старый модуль совместимости и тестов, новую логику туда не добавляйте
- CLI-команды → `src/arctuner/cli.py`

---

## Запуск

```powershell
# Проверка окружения (безопасно, read-only)
python -m arctuner doctor

# Снимок телеметрии (безопасно, read-only)
python -m arctuner telemetry

# Текущие настройки разгона (read-only)
python -m arctuner status
```

Запись настроек (⚠️ меняет состояние карты — читайте safety.md):
```powershell
# ПРИМЕР. Меняет состояние! Сначала поймите, что делаете.
python -m arctuner set --vram-speed 20
python -m arctuner set --power-limit 120
```

### GUI

```powershell
pip install -e ".[gui]"        # PySide6 (необязательный extra)
python -m arctuner.gui.main    # то же, что entry-point arctune-gui
```

Все вызовы IGCL идут в отдельном потоке, окно не блокируется; записи — только через
`tuner.py` и только после подтверждения в диалоге. Вкладок семь: Мониторинг /
Разгон / Вентиляторы / V/F / Журнал / Авторазгон / Оверлей.

---

## Тесты

```powershell
pytest
```

Виды тестов:
- **Тесты layout структур** — размер/смещение полей `_ctl_*_t` должны совпадать с C.
  Работают без железа. Ловят опечатки в `types.py`.
- **Smoke-тесты** (`tests/test_smoke.py`) — проверяют загрузку `IGCL.dll` и наличие
  обязательных функций; без библиотеки автоматически пропускаются (`skip`). Реального
  устройства не требуют.
- **Тесты логики** — проверки диапазонов `tuner` мокаются; движок авторазгона,
  watchdog, профили и проверка нагрузки — `tests/test_autotune.py`, старый
  `autofind` — `tests/test_autofind.py`.
- **Тесты журнала и реплея** — `tests/test_logbook.py`, `tests/test_replay.py`
  (без железа).
- **Тесты CLI** (`tests/test_cli.py`) — parser и значения по умолчанию, `doctor`,
  `_version()`, маркеры reset/fans, проброс autofind в общий движок; без IGCL и Qt.
- **Тесты GUI** (`tests/test_gui.py`) — платформа `offscreen`, железо не требуется:
  сессии IGCL подменяются фейками, а попытка открыть реальную сессию корректно
  обрабатывается и без драйвера. Конструирование вкладок (включая «Авторазгон»),
  попадание данных в виджеты, чтение/валидация CSV V/F, полнота карты записи
  worker'а и закрытие окна. Пропускаются, если PySide6 не установлен (extra `gui`).

Прогнать только тесты, не требующие железа:
```powershell
pytest -m "not hardware"
```

---

## Стиль кода

См. [CONTRIBUTING.md](../CONTRIBUTING.md). Кратко:
- `ruff` для линтинга и форматирования
- type hints обязательны на всех новых функциях
- docstrings на публичных функциях (Google style)
- низкоуровневый слой `igcl/` повторяет C-имена без «улучшений»

Полный набор проверок перед коммитом:
```powershell
ruff format .
ruff check --fix .
python -m mypy src/arctuner   # базовый уровень, зелёный в CI
pytest -m "not hardware"
```

### pre-commit (необязательно)

```powershell
pip install pre-commit
pre-commit install   # вешает ruff format / ruff check --fix / mypy на git commit
```

Хуки локальные (system): используют установленные ruff/mypy и не ходят в сеть.
Сброс одного коммита без хуков: `git commit --no-verify`.

---

## Отладка IGCL

Если функция IGCL возвращает неожиданный код ошибки:

1. Сверьте имя функции и сигнатуру с [igcl-api.md](igcl-api.md) и `igcl_api.h`.
2. Убедитесь, что драйвер достаточно свежий (старые IGCL могут не иметь V2-функций).
3. Запустите `python -m arctuner doctor`: он перечисляет отсутствующие обязательные
   функции и возвращает код 1, если их нет. Коды ошибок IGCL выводятся в сообщениях
   `[FAIL]`/`[BLOCK]`; отдельного режима логирования у `telemetry` нет.
4. Отсутствие функции в `IGCL.dll` → `loader` сообщит об этом явно (не молча).

---

## Запись в репозиторий

```powershell
git add -A
git commit -m "тип: краткое описание"
git push origin main
```

Типы коммитов: `feat`, `fix`, `docs`, `test`, `refactor`, `chore`, `perf`.
Подробнее — [CONTRIBUTING.md](../CONTRIBUTING.md).
