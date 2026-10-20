# Changelog

Все изменения проекта ArcTune документируются здесь. Формат основан на
[Keep a Changelog](https://keepachangelog.com/), версия **1.0.0**.

Стиль версий — **SemVer** (`MAJOR.MINOR.PATCH`):

- **MAJOR** — несъвместимые изменения API или архитектуры
- **MINOR** — новые фичи, обратная совместимость с прошлыми `x.y.0`
- **PATCH** — исправления ошибок, без изменения публичного интерфейса

Публичный интерфейс ArcTune: CLI-команды (`arctune *`), профили JSON
(`~/.arctune/profiles/`), журнал (`~/.arctune/logbook.jsonl`), дамп
инцидентов (`~/.arctune/incidents/`).

---

## [0.1.0] — 2026-10-20

Первый публичный релиз. Python-обёртка над публичным IGCL API для
мониторинга и разгона дискретных Intel Arc (проверено на B580).

### Добавлено

- **Ядро (читай/пиши)**
  - [ctlInit](ctlClose](enumerateDevices/ctlGetDeviceProperties — инициализация сессии, перечисление GPU, свойства
  - [ctlOverclockGetProperties](ctlOverclockWaiverSet) — свойства OC, waiver
  - [ctlOverclockResetToDefault) — сброс к дефолту
  - [ctlOverclockReadVFCurve]([ctlOverclockWriteCustomVFCurve) — кривая V/F (чтение и запись)
  - [ctlOverclockGpuFrequencyOffsetSetV2](ctlOverclockGpuMaxVoltageOffsetSetV2) — частота и напряжение ядра
  - [ctlOverclockPowerLimitSetV2](ctlOverclockTemperatureLimitSetV2) — лимит питания и температура
  - [ctlOverclockVramMemSpeedLimitSetV2] — лимит памяти (Гбит/с)
  - V2 Get-функции (dynamic load через `loader`) — чтение текущих значений OC

- **Телеметрия**
  - [ctlPowerTelemetryGet](arctuner.igcl.api.IgclApi) — снимок датчиков: температура, ватты, активность
  - Ватты из энергосчётчиков (`gpuEnergyCounter`/`totalCardEnergyCounter`), ΔE/Δt
  - Ватты памяти (`vramEnergyCounter`)
  - VR-фазы (gpuVrTemp, vramVrTemp, saVrTemp)
  - Пропускная способность памяти (vramReadBandwidth / vramWriteBandwidth)
  - Надбавка напряжения (gpuOverVoltagePercent)

- **Вентиляторы**
  - [ctlEnumFans](ctlFanGetProperties) — свойства вентиляторов (RPM, maxRPM, canControl)
  - [ctlFanGetState]([ctlFanSetFixedSpeedMode] — чтение/запись RPM
  - [ctlFanSetDefaultMode]([ctlFanGetConfig] — режим авто/фикс
  - На B580: только FIXED (supportedModes=0x2), таблица temp/speed недоступна

- **PCIe / Прошивка / ECC**
  - [ctlPciGetProperties](ctlPciGetState) — PCIe Gen, ReBAR
  - [ctlGetFirmwareProperties] — версия firmware (UNKNOWN на B580)
  - [ctlEccGetProperties](ctlEccGetState) — ECC (UNSUPPORTED_FEATURE на B580)

### CLI

| Команда | Описание |
|---|---|
| `arctune doctor` | Проверка IGCL API (healthcheck) |
| `arctune telemetry` | Снимок телеметрии (+ `--watch` + `--replay`) |
| `arctune status` | Диапазоны OC + текущие значения + кривая V/F |
| `arctune set` | Запись OC-настроек (с двухуровневой проверкой) |
| `arctune vf` | Кривая V/F (чтение, CSV экспорт, запись с валидацией) |
| `arctune autofind` | Авторазгон (адаптивный спуск к стабильности) |
| `arctune fans` | Вентиляторы (чтение RPM, `--set`, возврат в авто) |
| `arctune reset` | Сброс OC или вентиляторов к дефолту |
| `arctune log` | Журнал изменений (+ `--rollback`) |
| `arctune info` | PCIe / ReBAR / firmware / ECC / режим вентиляторов |
| `arctune --version` | Версия пакета |

### GUI (PySide6, `arctune-gui`)

| Вкладка | Описание |
|---|---|
| Мониторинг | Карточки: частота, напряжение, температура, ватты, память, VR-фазы, график |
| Разгон | Спинбоксы: питание (%), частота (МГц), напряжение (%), температура (°C), память (Гбит/с) |
| Вентиляторы | RPM с читаются с карты, фиксация скорости |
| V/F | Кривая напряжение/частота (drag-редактор на графике, CSV импорт/экспорт, запись) |
| Журнал | История изменений (что было → что стало), откат к исходным значениям |
| Авторазгон | Конфигуратор поиска (шаг, min_step, max_steps, temp ceiling, external_cmd), живой график, JSON-профили |

### Безопасность

- Двухуровневая проверка записи: жёсткие инварианты проекта + min/max/step/supported от IGCL
- Жёсткие потолки: питание ≤120% (~226 Вт), память ≤22 Гбит/с (21+ — с предупреждением)
- `ctlOverclockWaiverSet` вызывается перед записью напряжения/VF
- Подтверждение пользователя для каждой операции записи
- Журнал всех изменений в `~/.arctune/logbook.jsonl`
- Реплей инцидента: кольцевой буфер телеметрии, дамп при сбросе счётчиков

### Известные ограничения B580

- `vramVoltageOffset` / `vramFrequencyOffset` — unsupported (min=max=0, deprecated)
- `VFCurveVoltageLimit` / `VFCurveFrequencyLimit` — границы драйвера (400…1500 мВ / 0…4300 МГц)
- `VFCurveVoltageOffset` — min=0 (undervolt через offset невозможен на B580)
- Вентиляторы: только FIXED-режим (таблица temp/speed недоступна)
- Тройная защита: лимит питания (120%), тайминги памяти (страпы), vBIOS — вне scope навсегда

### Зависимости

- Ядро: только Python stdlib (ctypes)
- GUI: PySide6 (extra `gui`)
- Dev: pytest, ruff, mypy (extra `dev`)
- Сборка exe: PyInstaller (extra `build`)

---

> Этот релиз основан на кодовой базе на момент 2026-10-20. Все данные
> телеметрии и диапазоны OC взяты с реальной карты Intel Arc B580 (12 ГБ,
> Samsung K4ZAF325BC-SC20).
