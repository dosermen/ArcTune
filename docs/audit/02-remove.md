# Audit — REMOVE (что убрать / рефакторить)

**Проект:** ArcTune
**Файл:** docs/audit/02-remove.md
**Дата:** 2026-10-23 (итоговый аудит после верификации)
**Статус:** итоговый список

## Критерии REMOVE

1. Мёртвый код (unreachable / не импортируется).
2. Дублирование (same logic in >1 place, no architectural reason).
3. Оверинжиниринг (abstraction with <2 call sites, premature).
4. Легаси, неиспользуемые зависимости, устаревшие конфиги.

## Что убрать / рефакторить

| # | Пункт | Статус | Комментарий |
|---|-------|--------|-------------|
| 1 | **`autofind.py`** — «удалить как мёртвый» | ❌ **не применимо** | Модуль **используется**: `cli.py` (`cmd_autofind`), `gui/tabs/autotune.py`, `autotune/engine.py` импортируют `MODE_*`/`AutofindAbortError`. **Реальное действие — рефакторинг**: вынести `MODE_*` и `AutofindAbortError` в `arctuner/autotune/`, оставить `autofind.py` как thin-compat. Сам файл (239 стр.) — не мёртвой, но дублирует `TuningEngine` логику. |
| 2 | **`type: ignore[dict-item]`** в `igcl/api.py` (`read_oc_properties`) | ✅ **убрать** | Пропуск из-за неверного типа возврата; после type-annotation — лишнее. |
| 3 | **Lambda в `gui/worker.py:_guarded`** | ✅ **рефакторить** | `self._guarded(lambda: self._emit_write(writer(self._ctx, value)))` → named inner function; стиль, не критично. |
| 4 | **`examples/read_telemetry.py`** | ✅ **рефакторить/удалить** | Дублирует read-only путь CLI `telemetry` + `IgclApi`; пример малодейственный. |
| 5 | **`_ROLLBACK_MAP` (два источника)** | ❌ **не применимо** | `cli.py` (`_build_rollback_map` + `_ROLLBACK_MAP`) и `context.py` (`_ROLLBACK_MAP` ClassVar) — разные интерфейсы (функции tuner vs методы GpuContext); lazy-init в CLI — осознанная защита от circular import. Не дубли. |

## False positives — НЕ трогать (подтверждено по коду)

| Пункт | Почему НЕ REMOVE |
|---|---|
| `autofind.py` целиком | Активно импортируется (см. #1 — только рефакторинг) |
| `gui/context.py` | Используется: `worker.py`, `tabs/*`, `tests/conftest.py`, `tests/test_gui.py`, `gui/__init__.py` |
| `device_profile.py` | Находится внутри пакета `src/arctuner/`, корректный импорт `arctuner.device_profile` |
| `_ROLLBACK_MAP` объединение | Семантически разные карты; объединение = переписывание рабочей логики ради -50 строк |
| `pytest-xdist` | Не нужен: полный прогон 3.7 s |

## Итог

- **Кандидатов на чистое удаление:** `type: ignore[dict-item]`, lambda-в-wrapper, дублирующийся example.
- **Кандидатов на рефакторинг:** `autofind.py` → `autotune/` (вынос `MODE_*`/`AutofindAbortError`).
- **Не трогать:** `gui/context.py`, `device_profile.py`, `_ROLLBACK_MAP` (семантика разная).

---

## Сопутствующее исправление (security/quality fix — ADD-adjacent)

| # | Пункт | Статус | Комментарий |
|---|-------|--------|-------------|
| 1 | `subprocess.Popen(cmd, shell=True)` в `autotune/loader.py:start_external` | ✅ **исправить** | `shell=True` + строка = **command injection** при `external_cmd` из профиля/ввода. Нужно `shell=False` + список args. Это не REMOVE, но security fix в зоне ADD-инфраструктуры. |
