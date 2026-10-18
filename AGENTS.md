# Правила проекта (для AI-ассистентов и соавторов)

Эти правила обязательны при работе с кодовой базой ArcTune. Не отступайте от них.

## Что проект делает
Python-обёртка над публичным IGCL API (Intel) для мониторинга и разгона дискретных
Intel Arc (проверено на B580). Read-only телеметрия + контролируемая запись настроек.

## Железные правила (инварианты) — НЕ нарушать
1. НИКОГДА не реализовывать: обход лимита питания выше аппаратного (120%/226 Вт),
   запись/модификацию vBIOS, доступ к таймингам памяти (страпы). Это вне scope навсегда.
2. Read-only по умолчанию. Любая операция, меняющая карту, — явная, оповещает
   пользователя и проверяет min/max/step/IsSupported из IGCL.
3. Безопасность важнее удобства.
4. Честно писать, если что-то не поддерживается на B580.

## Структура
- src/arctuner/igcl/   — низкоуровневый слой, честно повторяет IGCL.dll (loader.py, types.py, api.py)
- src/arctuner/telemetry.py  — чтение датчиков (read-only)
- src/arctuner/tuner.py      — запись настроек (единственный опасный модуль)
- src/arctuner/cli.py        — командная строка
- tests/, docs/, examples/

## Стиль
- Python 3.10+, type hints обязателен, Google docstrings на публичном.
- ruff: `ruff format .` и `ruff check --fix .` перед коммитом. line-length=100.
- Низкий слой igcl/ повторяет C-имена IGCL (ctlOverclock*, поля _ctl_*_t) без «улучшений».
- Ошибки IGCL → исключение IgclError, не «глотать».
- Коммиты: conventional commits (feat/fix/docs/test/refactor/chore/perf), описание по-русски.

## Тесты
- Новый код — тесты. Изменение types.py — тест layout структуры.
- Тесты на реальном железе помечать @pytest.mark.hardware (пропускаются без Arc).

## Термины / контекст железа (B580)
- TDP 190 Вт, софтверный лимит 120% ≈ 226 Вт (аппаратный, по шунту 2 мОм).
- Память 6× Samsung K4ZAF325BC-SC20 (16Гбит ×32, 12 ГБ, 192-бит), номинал 20 Гбит/с (ставят 19), V=1.35В фикс.
- Основные ctl*-функции: ctlOverclock* (GpuFrequencyOffset/GpuMaxVoltageOffset/PowerLimit/
  TemperatureLimit/VramFrequencyOffset/VramMemSpeedLimit/VramVoltageOffset/VFCurve), ctlPowerTelemetryGet,
  ctlFan*, ctlMemory*, ctlEnumerateDevices.

## Язык
Комментарии, docstrings, документация, сообщения — по-русски. Имена кода — English.
