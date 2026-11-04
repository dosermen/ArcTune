# Roadmap

## Сделано

### IGCL API — разобрано и подтверждено

- Разобран IGCL API из установленных бинарников Intel Graphics Software
- Определены реальные точки входа разгона B580 (`ctlOverclock*`)
- Зафиксированы границы возможного (нет страпов, нет обхода лимита питания)
- Точные раскладки `_ctl_*_t` сверены с публичным `igcl_api.h` (v1-r1)

### Подтверждено живым API (Intel-данные с B580)

- `powerLimit`: **50...120 %** (потолок 120 % ≈ 226 Вт — шунт, аппаратно)
- `vramMemSpeedLimit`: **19...22 Гбит/с** (сток 19, номинал чипа 20, драйвер до 22)
- `gpuFrequencyOffset`: **-300...+1000 МГц**, `temperatureLimit`: **60...100 °C**
- Датчики читаются: ядро ~1.0 В, память 1.35 В / ~19.75 ГТ/с (Samsung)
- **Ватты из счётчиков энергии**: `gpuEnergyCounter` / `totalCardEnergyCounter`
  (единицы = Дж); средняя мощность = ΔE/Δt.
- **Ватты памяти** (`vramEnergyCounter`, Дж) — на живой карте idle ~7 Вт
- **VR-фазы** (ядро/память/SA) ≈ 52/64/55 °C idle
- **Фактическая пропускная способность памяти**: 1703/252 МБ/с в покое
- **Waiver обязателен**: `ctlOverclockWaiverSet` перед записью напряжения,
  отрицательного смещения частоты и кривой V/F

### Обёрнуто IGCL API (27 из 128 функций)

| Категория | Функций | Что обёрнуто |
|---|---|---|
| **Инициализация и устройство** | 4 | `ctlInit`, `ctlClose`, `ctlEnumerateDevices`, `ctlGetDeviceProperties` |
| **Разгон V2** | 10 | GetProperties, WaiverSet, ResetToDefault, ReadVFCurve, WriteCustomVFCurve, 5×SetV2 |
| **Телеметрия** | 1 | `ctlPowerTelemetryGet` |
| **Вентиляторы** | 7 | EnumFans, GetProperties, GetState, SetFixed/SpeedTable/DefaultMode, GetConfig |
| **PCIe / прошивка / ECC** | 5 | Pci(GetProperties/GetState), GetFirmwareProperties, Ecc(GetProperties/GetState) |

Пять V2 Get-функций (`...GetV2`) вызываются из `tuner.py` динамически, без отдельных обёрток в `api.py`.
Остальные 96 функций IGCL **не обёрнуты** (память, домены частот, GpuLock, дисплейная подсистема ~80).

### MVP — read-only (на живой B580)

- `ctlInit` → `ctlEnumerateDevices` → `ctlGetDeviceProperties` → `ctlPowerTelemetryGet` → `ctlOverclockGetProperties` → `ctlClose`
- Команды `doctor` / `telemetry` / `status` — работают на реальной карте
- Тесты layout структур проходят на точных раскладках

### Запись (tuner.py + safety-гарды)

- [x] **Запись с двухуровневыми проверками**: жёсткие инварианты проекта (min/max/step/supported из `ctlOverclockGetProperties`)
- [x] **Откат к дефолту** (`ctlOverclockResetToDefault`) + команда `reset`
- [x] **Вентиляторы**: чтение RPM/свойств + фиксация скорости (`fans --set`). B580: только FIXED (`supportedModes=0x2`), единица только RPM
- [x] **Кривая V/F**: чтение (`ctlOverclockReadVFCurve`, 10 точек на B580)
- [x] **Логирование**: JSONL-журнал (`logbook.py`) + откат (`log --rollback`)
- [x] **Экспорт телеметрии в CSV** (`telemetry --csv`) + режим наблюдения `--watch`
- [x] **Кривая V/F**: экспорт в CSV (`vf --csv`) и валидированная запись (`vf --apply`)
  — границы [600..1250 мВ, 300..3300 МГц] + монотонность + сверка числа точек со стоковой; стоковая кривая B580 (745..1195 мВ) проходит

### Авторазгон (autotune/)

- [x] **Общий движок `autotune/engine.py`** для CLI и GUI: адаптивный шаг, снаппинг к сетке IGCL (`min/step`), проверка нагрузки, температуры и watchdog, откат к последнему стабильному значению
- [x] **Watchdog авторазгона**: TDR (Event ID 4101), заморозка телеметрии (счётчик энергии не растёт при активности), аварийная температура 85 °C
- [x] **JSON-профили параметров поиска** (`autotune/profiles.py`) — без применения к карте
- [x] CLI `autofind` переведён на новый движок; `autofind.py` — модуль совместимости
  (MODE_*/`AutofindAbortError` канонически в `autotune/engine.py`, отсюда переэкспорт)

### GUI (PySide6)

- [x] **8 вкладок**: Мониторинг / Разгон / Вентиляторы / V/F / Журнал / Об устройстве / Авторазгон / Оверлей
- [x] **GpuWorker** — все вызовы IGCL изолированы в `QThread`: окно не подвисает
- [x] **Живые графики** QtCharts (окно 5 мин), карточки телеметрии, спинбоксы из `ctlOverclockGetProperties`
- [x] **Подтверждения записи**: диалог с предпросмотром, по умолчанию «Отмена»
- [x] **Тёмная тема**: theme.py + QSS + Fusion, меню «Вид → Тема», переключение на ходу
- [x] **Правка кривой V/F на графике** — потянуть мышью (Y = напряжение, X = частота)
- [x] **Оверлей (HUD)** — полупрозрачная панель поверх игр/бенчмарков: 3 макета (полоса 1×6, сетка 3×2, подробный 2×5), свой набор метрик (10 чекбоксов, включая загрузку ядра и занятость памяти), непрозрачность 50/70/85/100 %
- [x] **Мощность в Ваттах** — из счётчиков энергии (PowerMeter, ΔE/Δt)
- [x] **Ревью GUI**: исправлено 15 багов отображения
- [x] **Локализация GUI** — русский/английский: инлайновый `tr(ru, en)`, переключение
  с диалогом перезапуска, язык в `~/.arctune/language` / env `ARCTUNE_LANG`
- [x] **Сборка**: `packaging/arctune-gui.spec` → `dist/arctune-gui.exe` (~48 МБ)

### Совместимые модули

- [x] **replay.py** — кольцевой буфер телеметрии + дамп при сбросе счётчика
- [x] **i18n.py** — локализация CLI (Русский / English)
- [x] **device_profile.py** — профили устройств; универсальная запись не только для B580

### Инфраструктура и устойчивость

- [x] **CI** (`.github/workflows/ci.yml`): ruff format/check + mypy + pytest `-m "not hardware"`
  + smoke + PyInstaller — python 3.10–3.12, windows-latest. **✅ сверено 2026-11-03**:
  все шаги на месте (на аудите 2026-10-23 mypy-шага ещё не было)
- [ ] **mypy строгий** — конфиг `pyproject.toml` есть (`strict = false`,
  `check_untyped_defs = true`), ошибок пока нет (62 → 0 по файлам); CI-шаг есть →
  открыт только сам строгий режим (перспектива, раздел «К»)
- [x] **Release по тегу** — `.github/workflows/release.yml`: при теге `v*` сверяет
  тег с `__version__` (расхождение = ошибка), собирает CLI/GUI exe, smoke-проверяет,
  публикует GitHub Release с `arctune-cli-x64.zip` / `arctune-gui-x64.zip`
- [x] **Единый источник версии** — `pyproject.toml`: `dynamic = ["version"]` →
  `version = { attr = "arctuner.__version__" }`; дублирования нет
- [x] **Покрытие pytest** — `addopts = "-q --cov=arctuner --cov-report=term-missing"`,
  `[tool.coverage.run] branch = true`, `show_missing = true`
- [x] **pre-commit** — `.pre-commit-config.yaml` (local-хуки: ruff format,
  ruff check --fix, mypy src/arctuner)
- [x] **ADR-журнал** — `docs/adr/`: ADR-0001…0006 + README-указатель
- [x] **Рестарт onefile-exe** — ✅ (очистка `_PYI*` через `restart_env`: второй инстанс
  не стартует из чужого `_MEIxxxx`)
- [x] **requirements.lock** — файл есть (зафиксированные версии зависимостей)

---

## Перспективное — что можно добавить

Приоритеты: **С** (критично, до 1 ч) → **Ж** (желательно, 2–6 ч) → **К** (перспектива, полдня+).
Сложность: **С** (одна функция/вкладка) / **М** (модуль/обёртка) / **Л** (новый подсистемный слой).

### С — критично (~3 часа)

| Задача | Описание | Сложность |
|---|---|---|
| **State Doctor** | ✅ CLI `arctune state`: сверка `tuner.current_values()` ⟷ `Logbook.latest_values()` с допуском step/2 («ожидался +160 МГц, фактически 0 — сброшено драйвером?»), exit 1 при дрейфе; версия драйвера хранится в `~/.arctune/driver_version` и сверяется между сессиями (смена = предупреждение «разгон мог сброситься»). Модуль `state_doctor.py`, чистая композиция, read-only |
| **CSV-экспорт из GUI** | ✅ Кнопка «Экспорт CSV…» на вкладке «Мониторинг»: воркер пишет буфер реплея (последние ~10 мин) через общую `export_telemetry`/`snapshot_csv_row` из `telemetry.py` — тот же формат, что CLI `telemetry --csv`; CLI переведён на общие функции (дубль колонок удалён) |
| **Вкладка «Об устройстве» в GUI** | ✅ Вкладка «Об устройстве»: секции «Устройство» (имя/ID/драйвер/прошивка/частота), «PCIe» (Gen/ширина/полоса/ReBAR/адрес), «Прошивка», «ECC» + кнопка «Обновить»; read-only `GpuContext.pci_state/firmware_info/ecc_state`, воркер собирает `DeviceReport` (секции независимы, сбой одной — в `errors`, не обнуляет остальные); сигналы `device_info_ready`/`device_report_ready` |
| **Постоянный watchdog** | ✅ Fallback-мониторинг вне авторазгона: `Watchdog` из `autotune/watchdog.py` живёт в poll-цикле воркера GUI — TDR (Event ID 4101), заморозка телеметрии, аварийная 85 °C; каждое событие — одна запись в logbook (дедуп по типу, сброс после нормализации) + пилюля статус-бара; авто-откат OC только с явной опцией (меню «Инструменты», `gui/safety.py`, по умолчанию выкл.) |
| **Отображение уровня загрузки GPU/памяти** | ✅ CLI (`telemetry --load`), GUI: карточки «Загрузка»/«Память занято» + 2 серии на графике вкладки «Мониторинг» и ячейки в оверлее (макеты 1×6/3×2/2×5); `load_source=igcl` (`ctlEngineGetActivity` + `ctlMemoryGetState`, проверено на B580); pdh/dxgi зарезервированы (см. docs/igcl-api.md) |

### Ж — желательно (~4.5 часа)

| Задача | Описание | Сложность |
|---|---|---|
| **Профили OC (save/load/apply)** | JSON-профили: `safe`, `aggressive`, `custom`. Комбинировать с logbook — «кто как разогнал до этого». CLI + GUI |
| **Расширенная `arctune info --detailed`** | Модель (`Arc B580`), архитектура (`PXE`), дата кода, ID, PCIe-адрес, ReBAR, ECC, firmware version, драйвер версия (через `ctlGetFirmwareProperties` + `GetSystemInfo`) |
| **Софт-кривая вентилятора** | Машина состояний: целевая температура → RPM с гистерезисом ±3 °C, аварийное >85 °C → 100 %, при выходе — `ctlFanSetDefaultMode`. Единственный способ «тихо в покое, громко под нагрузкой» |
| **Оповещения** | Windows Toast + Discord webhook (перегрев ядра/VR-фаз, лимит-флаги, итог авторазгона, событие watchdog). Модуль `notifier.py`, rate-limit повторов, webhook URL от пользователя |
| **Таблица памяти в GUI** | Total/used, bandwidth read/write (из `vramReadBandwidth`/`vramWriteBandwidth`), ECC status, модуль памяти (Samsung K4ZAF325BC-SC20, 12 ГБ, 192-бит) |

### К — перспектива (~13 часов)

| Задача | Описание | Сложность |
|---|---|---|
| **Стресс-нагрузчик внутри проекта** | Чтобы авторазгон не требовал внешний бенчмарк (сейчас `external_cmd` умеет запустить заданный процесс, но своего нагрузчика нет). Без нагрузки поиск честно останавливается «нет нагрузки» |
| **Бенч-интеграция** (`arctune bench run`) | Subprocess 3DMark/Unigine/OCCT → парсинг результата → `benchbook.jsonl`. Хрупкий парсинг: по одному бенчу за итерацию, честно маркировать версию бенча и сцену |
| **Prometheus-экспортёр** (`arctune exporter --port 9199`) | GET-метрики (Вт/°C/флаги как gauge): Grafana-дашборд, удалённый мониторинг с телефона в LAN. Веб-запись запрещена навсегда — только GET, кнопок нет |
| **Планировщик правил** | «При запуске игры → игровой профиль, после выхода → тихий». psutil-поллер поверх профилей, гарантированный откат в try/finally. Одноактное согласие на правило с полным предпросмотром, только безопасные домены |
| **История телеметрии на диск** | Сейчас график живёт 5 минут в памяти, после перезапуска всё стёрто — журнал сессий + графики за прошлые сессии |
| **Счётчики наработки** (`arctune stats`) | Часы под нагрузкой, кВтч, средние Вт за период: дельты `*_energy_counter_j` → `~/.arctune/stats.json`. Честные raw-джоули, периоды без данных помечать |
| **Сверка logbook с Event Viewer** | Если драйвер перезагружался (Event ID 4101/4109), флагнуть, что телеметрический буфер мог потерять данные |
| **Температурные алерты в GUI** | Safety.md требует держать <80 °C, а GUI молчит о троттлинге; пороги: ядро/память, уведомление о Power/Voltage Limited |
| **Контекст нагрузки** | «Нагрузка: OCCT.exe 94 %» вместо «activity растёт» — psutil (read-only) |
| **Оптимизация памяти: `ctlMemoryGetState/Bandwidth`** | Измеримая проверка разгона памяти, пропускная способность (4 обёртки) |
| **Фиксация частоты: `ctlGpuLockGet/Set`** | Предотращает дрожание буста для стабильных бенчмарков (2 обёртки) |
| **Домены частот: `ctlFrequencyDomain`** | Троттлинг-время как метрика «успевает ли карта», список доменов (PXE, PM4, Compute, Media, VFE) |
| **`ctlPowerTelemetryGetV2`** | Новая версия телеметрии — оценить, даёт ли больше полей на B580 |
| **Инсталлятор / иконка / app.manifest** | DPI-aware Windows-сборка (критично для GUI с масштабом 100–200 %) |
| **mypy strict** | Строгая типизация ctypes-кода — сложный, но важный шаг для проекта со структурами; базовый mypy уже в CI |
| **Разгонный дневник: отчёт MD/HTML** | Рендер готовых фактов (logbook + история авторазгона + снапшоты + device info) в карточку для форума/вики; приватность: имя ПК вырезать |
| **Дифф снапшотов** | «До/после разгона» — сравнение конфигов |
| **Оповещения: Telegram** | Аналогично Discord webhook, но для Telegram Bot API |
| **Парсинг драйвер-версии** | Сопоставление с последней стабильной (скачивание с Intel Arc, парсинг `driver_ver.txt`); алерт, если драйвер старше 3 месяцев; `arctune doctor --check-driver` |
| **Экспорт телеметрии в JSON** (`arctune export --format json`) | Вывод всех телеметрий в JSON (freq, power, temp, energy, vram, fan); файл `~/.arctune/snapshot.json` |
| **Health-check (`arctune health`)** | Проверка здоровья: драйвер отвечает (ping IGCL), ReBAR включён, ECC не сломан, память валидна; exit=0 всё ок, exit=1 предупреждение, exit=2 критично |

---

## Аудит IGCL API: что обёрнуто и что нет

### Обёрнуто (27 из 128 функций)

| Категория | Кол-во | Функции |
|---|---|---|
| **Инициализация и устройство** | 4 | `ctlInit`, `ctlClose`, `ctlEnumerateDevices`, `ctlGetDeviceProperties` |
| **Разгон V2** | 10 | `ctlOverclockGetProperties`, `WaiverSet`, `ResetToDefault`, `ReadVFCurve`, `WriteCustomVFCurve`, 5×`...SetV2` |
| **Телеметрия** | 1 | `ctlPowerTelemetryGet` |
| **Вентиляторы** | 7 | `ctlEnumFans`, `ctlFanGetProperties`, `ctlFanGetState`, `ctlFanSetFixedSpeedMode`, `ctlFanSetSpeedTableMode`, `ctlFanSetDefaultMode`, `ctlFanGetConfig` |
| **PCIe / прошивка / ECC** | 5 | `ctlPciGetProperties`, `ctlPciGetState`, `ctlGetFirmwareProperties`, `ctlEccGetProperties`, `ctlEccGetState` |

Пять V2 Get-функций вызываются из `tuner.py` динамически через `loader`, без обёрток в `api.py`.

### Не обёрнуто (96 из 128 функций)

| Категория | Кол-во | Функции | Статус |
|---|---|---|---|
| **Память** | 4 | `ctlEnumMemoryModules`, `ctlMemoryGetProperties`, `ctlMemoryGetState`, `ctlMemoryGetBandwidth` | Нужна реализация |
| **ECC set** | 1 | `ctlEccSetState` | Не используется на B580 |
| **Домены частот** | 3 | `ctlEnumFrequencyDomains`, `ctlFrequencyGetState`, `ctlFrequencyGetThrottleTime` | Диагностика тротлинга |
| **GpuLock** | 2 | `ctlOverclockGpuLockGet`, `ctlOverclockGpuLockSet` | Фиксация частоты |
| **Power domains** | 3 | `ctlEnumPowerDomains`, `ctlTemperatureGetState`, `ctlPowerGetEnergyCounter` | Доп. датчики |
| **Дисплейная подсистема** | ~80 | sharpness, EDID, genlock, ArcSync, RGB и т.п. | **Вне scope навсегда** |
| **`ctlAllowPCIeLinkSpeedUpdate`** | 1 | Обновление PCIe link speed | Не критично |

---

## Намеренно вне scope

Эти вещи проект **не** будет реализовывать — чтобы не создавать ложных ожиданий:

- **Страпы / тайминги памяти** — firmware применяет их при memory training, IGCL не даёт доступа
- **Прошивка / мод vBIOS** — для B580 нет модов с повышенным лимитом, риск кирпича
- **Обход лимита питания выше 120%** — аппаратный потолок (шунт на плате), требует пайки
- **Регулировка напряжения памяти (VRAM voltage)** — на B580 не поддерживается: `vramVoltageOffset` возвращает `supported=false` (min=max=0). Напряжение 1.35 В фиксировано на Samsung K4ZAF325BC-SC20
- **Дисплейная подсистема / LED / I2C-AUX** (~80 функций) — вне профиля «мониторинг + разгон»

---

## Сводка задач

| Приоритет | Задач (открыто) | Сложность | Время |
|---|---|---|---|
| **С** (критично) | 5 (1) | С (1 функция/вкладка) | **~1 ч** |
| **Ж** (желательно) | 5 (5) | С–М (модуль/обёртка) | **~4.5 ч** |
| **К** (перспектива) | 21 (21) | М–Л (новый слой) | **~20 ч** (оценка) |
| **Итого** | **31 (27)** |  | **~25.5 ч** |

Оценка времени для «К» пересчитана пропорционально числу задач (~1 ч/задачу);
в разделах выше у каждой задачи своя сложность.

| Сложность | Время | Описание |
|---|---|---|
| **С** | до 1 ч | малая (одна функция / одна вкладка) |
| **М** | 2–6 ч | средняя (новый модуль / обёртка над IGCL) |
| **Л** | полдня+ | крупная (новый подсистемный слой, CI) |

---

## Версия и релиз

- Версия: **0.1.0** — единый источник `src/arctuner/__init__.py` (`__version__`);
  `pyproject.toml` подключает её через `dynamic = ["version"]` → **✅**
- Semver: **MAJOR** — API-ломы; **MINOR** — новые фичи; **PATCH** — исправления — **✅**
- CHANGELOG.md: Keep a Changelog v1.0.0 — **✅**
- CI: `.github/workflows/ci.yml` — ruff + mypy + pytest (с покрытием) + сборка exe
  (python 3.10–3.12, windows-latest) — **✅, все шаги на месте (сверено 2026-11-03)**
- Release: `.github/workflows/release.yml` — при теге `v*` сверяет тег с
  `__version__` (расхождение = ошибка, не релиз), собирает exe и публикует
  GitHub Release с артефактами zip → **✅ workflow готов**
- `git tag` пуст, remote не настроен — релизы ещё не оформлялись (первый тег
  поставить после публикации репозитория) → **открыто**
