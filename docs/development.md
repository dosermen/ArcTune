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

---

## Настройка окружения

```powershell
# Вход в папку проекта
cd F:\AI\INTEL

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
`tuner.py` и только после подтверждения в диалоге.

---

## Тесты

```powershell
pytest
```

Виды тестов:
- **Тесты layout структур** — размер/смещение полей `_ctl_*_t` должны совпадать с C.
  Работают без железа. Ловят опечатки в `types.py`.
- **Smoke-тесты** — требуют реального Arc; автоматически пропускаются (`skip`), если
  устройства нет.
- **Тесты логики** — проверки диапазонов `tuner` мокаются.
- **Тесты GUI** (`tests/test_gui.py`) — платформа `offscreen`, IGCL не вызывается
  вовсе: конструирование вкладок, попадание данных в виджеты, чтение/валидация CSV
  V/F, полнота карты записи worker'а. Пропускаются, если PySide6 не установлен
  (extra `gui`).

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

---

## Отладка IGCL

Если функция IGCL возвращает неожиданный код ошибки:

1. Сверьте имя функции и сигнатуру с [igcl-api.md](igcl-api.md) и `igcl_api.h`.
2. Убедитесь, что драйвер достаточно свежий (старые IGCL могут не иметь V2-функций).
3. Включите логирование: `python -m arctuner telemetry --verbose` печатает коды результатов.
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
