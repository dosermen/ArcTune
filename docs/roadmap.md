# Roadmap

## Сделано

- [x] Разобран IGCL API из установленных бинарников Intel Graphics Software
- [x] Определены реальные точки входа разгона B580 (`ctlOverclock*`)
- [x] Зафиксированы границы возможного (нет страпов, нет обхода лимита питания)
- [x] Каркас проекта, документация, правила, git-репозиторий
- [x] Точные раскладки `_ctl_*_t` сверены с публичным `igcl_api.h` (v1-r1)
- [x] **Работающий read-only MVP, проверен на живой B580:**
      `ctlInit` -> `ctlEnumerateDevices` -> `ctlGetDeviceProperties` ->
      `ctlPowerTelemetryGet` -> `ctlOverclockGetProperties` -> `ctlClose`
- [x] Команды `doctor` / `telemetry` / `status` работают на реальной карте
- [x] Тесты layout структур проходят на точных раскладках

### Подтверждено живым API (Intel-данные с B580)

- `powerLimit`: **50...120 %** (потолок 120 % ~= 226 Вт — шунт, аппаратно)
- `vramMemSpeedLimit`: **19...22 Гбит/с** (номинал 19, софтверный запас до 22)
- `gpuFrequencyOffset`: **-300...+1000 МГц**, `temperatureLimit`: **60...100 °C**
- Датчики читаются: ядро ~1.0 В, память 1.35 В / ~19.75 ГТ/с (Samsung)
- **Ватты из счётчиков энергии**: `gpuEnergyCounter`/`totalCardEnergyCounter`
  supported (единицы = Дж); средняя мощность = ΔE/Δt. На живой карте:
  GPU ~68 Вт, вся карта ~120 Вт при 65 % от лимита питания

### Обёрнуто IGCL API (22 функции)

- **Инициализация и устройство** (4): `ctlInit`, `ctlClose`,
  `ctlEnumerateDevices`, `ctlGetDeviceProperties`
- **Разгон V2** (10): `ctlOverclockGetProperties`,
  `ctlOverclockWaiverSet`, `ctlOverclockResetToDefault`,
  `ctlOverclockReadVFCurve`, `ctlOverclockWriteCustomVFCurve`,
  `ctlOverclockGpuFrequencyOffsetSetV2`, `ctlOverclockGpuMaxVoltageOffsetSetV2`,
  `ctlOverclockPowerLimitSetV2`, `ctlOverclockTemperatureLimitSetV2`,
  `ctlOverclockVramMemSpeedLimitSetV2`
- **Телеметрия** (1): `ctlPowerTelemetryGet`
- **Вентиляторы** (6): `ctlEnumFans`, `ctlFanGetProperties`, `ctlFanGetState`,
  `ctlFanSetFixedSpeedMode`, `ctlFanSetSpeedTableMode`, `ctlFanSetDefaultMode`
- **Не обёрнуто (38)**: см. раздел «Перспективное / Новые данные из IGCL»

### Жёстко отказано драйвером (проверено записью, п. «Эксперименты»)

- **Память выше 21 Гбит/с недостижима**: 21 — работает и откатывается;
  заявленный драйвером 22 — отказ (`DATA_WRITE`, 0x40000013); 24 — отказ
  (вне диапазона). Выше — только vBIOS (вне scope).
- **Лимит температуры вне 60...100 °C невозможен**: 50/55/105/110 —
  `TEMPERATURE_OUTSIDE_RANGE` (0x44000005) от самого драйвера, не наша проверка.
- **Отрицательное смещение напряжения невозможно**: V2-функция декларирует
  только 0...100 % (min=0), легаси-функция в ±мВ отвечает `DEPRECATED_API`
  (0x44000009). Undervolt на B580 — только кривая V/F.
- **Waiver обязателен**: `ctlOverclockWaiverSet` перед записью напряжения,
  отрицательного смещения частоты и кривой V/F — иначе
  `WAIVER_NOT_SET` (0x44000008). Это была причина «ошибки при изменении» в GUI.
  Согласие пользователя — диалог подтверждения записи (текст честно упоминает waiver).

## Далее (запись)

- [x] `tuner.py` — запись с ДВУХУРОННЕВОЙ проверкой: жёсткие инварианты проекта
      + min/max/step/supported из `ctlOverclockGetProperties`
- [x] Откат к дефолту (`ctlOverclockResetToDefault`) + команда `reset`
- [x] Вентиляторы: чтение RPM/свойств + фиксация скорости (`fans --set`)
      * B580: только FIXED-режим (`supportedModes=0x2`), единица только RPM —
        таблицы temp/speed нет, честно отмечено
- [x] Кривая V/F: чтение (`ctlOverclockReadVFCurve`, 10 точек на B580)
- [x] Логирование изменений в журнал (`logbook.py`, JSONL) + откат командой
      `log --rollback` к исходным значениям
- [x] Экспорт телеметрии в CSV (`telemetry --csv`) + режим наблюдения `--watch`
- [x] Кривая V/F: экспорт в CSV (`vf --csv`) и валидированная запись (`vf --apply`)
      — границы [600..1250 мВ, 300..3300 МГц] + монотонность + сверка числа
      точек со стоковой; стоковая кривая B580 (745..1195 мВ) проходит
- [x] `autofind.py` — автопоиск стабильной точки (undervolt/частота) с
      телеметрическими гардами и ГАРАНТИЕЙ отката; детекция нагрузки через
      activity_counter; тесты логики на фейковом API

### GUI (PySide6)

- [x] `gui/` — приложение поверх того же `igcl/` слоя (extra `gui`, entry-point
      `arctune-gui`): 5 вкладок — Мониторинг / Разгон / Вентиляторы / V/F / Журнал
- [x] Все вызовы IGCL изолированы в `GpuWorker` (QObject в `QThread`): окно не
      подвисает, запись остаётся в `tuner.py` (двухуровневые проверки)
- [x] Живые графики QtCharts (окно 5 мин), карточки телеметрии, диапазоны спинбоксов
      берутся из `ctlOverclockGetProperties`
- [x] Жёсткие потолки GUI: питание ≤120%, память ≤20 Гбит/с (выше — только CLI `--force`)
- [x] Подтверждения записи (диалог с предпросмотром, по умолчанию «Отмена») + журнал
- [x] `packaging/arctune-gui.spec` → windowed `dist/arctune-gui.exe` (~48 МБ),
      проверен на живой B580
- [x] Тесты GUI offscreen (без железа): вкладки, попадание данных, CSV V/F, полнота
      карты записи
- [x] Мощность в Ваттах в CLI и GUI из счётчиков энергии (`PowerMeter`, ΔE/Δt);
      первый тик — база, пропуски/откат счётчика — перебаза без выдуманных значений
- [x] Ревью GUI: исправлено 15 багов отображения (спам ошибок при отсутствии карты,
      локаль спинбоксов, адаптивные оси графика, накопление строк вентиляторов,
      предпросмотр отката, busy-блокировка кнопок и т.д.)

## Перспективное

Приоритеты: 1 — автоматизация разгона, 2 — наблюдаемость, 3 — новые данные IGCL,
4 — удобство CLI/GUI.

**Аудит IGCL API (128 функций): 22 обёрнуто, 38 не обёрнуто.**
Обёрнуты: инициализация (4), разгон V2 (10), телеметрия (1), вентиляторы (6).
Необёрнуто: Get-версии (1), память (4), vram-поля телеметрии (8), ECC (3),
PCIe/firmware (4), домены (3), GpuLock (2), дисплей (~80, вне scope).
Список разобран по сложности в разделе ниже.

### 1. Автоматизация разгона

- [ ] **Стресс-нагрузчик внутри проекта** — чтобы autofind не требовал внешний
      бенчмарк (сейчас честно останавливается «нет нагрузки»); сделал бы автопоиск
      push-button с валидной проверкой стабильности
- [ ] **GUI-версия autofind** — в `GpuWorker` нет слота, самая умная фича проекта
      доступна только из CLI
- [ ] **Температурные алерты в GUI** — safety.md требует держать <80 °C, а GUI
      молчит о троттлинге; пороги: ядро/память, уведомление о Power/Voltage Limited

### 2. Наблюдаемость

- [ ] **История телеметрии: журнал сессий на диск + графики за прошлые сессии** —
      сейчас график живёт 5 минут в памяти, после перезапуска всё стёрто
- [ ] Экспорт телеметрии в CSV из GUI (в worker есть только `export_vf`)
- [ ] **`telemetry --json` / `status --json`** — парсинг скриптами/мониторингом
- [ ] **`status` с текущими значениями** — метод `tuner.current_values()` уже
      написан, в CLI не выведен
- [ ] Diff снапшотов («до/после разгона», сравнение конфигов)

### 3. Новые данные из IGCL (раскрыв по сложности)

#### 3.1 Быстрая реализация (уже читаются, нужно только извлечь)

- [ ] **Поля `vram*Limited` (power/temp/current/voltage/utilization)** — 5 флагов
      из `CtlPowerTelemetry`, которые драйвер уже заполняет; показывают «в чём
      упёрлась память» — диагностический ключ (извлекать в `TelemetrySnapshot`)
- [ ] **`vramEnergyCounter`** — Вт памяти (считается аналогично gpu/totalCard)
- [ ] **`vramRead/WriteBandwidthCounter`** — пропускная способность чтения/записи
- [ ] **`renderComputeActivityCounter` / `mediaActivityCounter`** — детализация
      активности CPU/GPU (тоже уже читаются `globalActivityCounter`)
- [ ] **Границы VF-кривой** — `gpuVFCurveVoltageLimit` / `gpuVFCurveFrequencyLimit`
      уже читаются `read_oc_properties()`, но скрыты в CLI/GUI

#### 3.2 Средняя сложность (нужна обёртка 1 функции)

- [ ] **`ctlFanGetConfig`** — текущий режим вентилятора (auto/fixed/table)
- [ ] **`ctlPciGetState` / `ctlGetFirmwareProperties`** — проверка ReBAR/x16
      (частая причина «разгон не помогает»), версии firmware/vBIOS
- [ ] **`ctlEccGetState` / `ctlEccSetState`** — статус ECC, важен при разгоне
      памяти (ошибки, которые маскируются тестами); также `ctlEccGetProperties`
- [ ] **`ctlOverclockGpuLockGet/Set`** — фиксация частоты для стабильных бенчей
      (предотращает дрожание буста)
- [ ] **`ctlEnumFrequencyDomains` / `FrequencyGetState` / `FrequencyGetThrottleTime`**
      — троттлинг-время как метрика «успевает ли карта»

#### 3.3 Средняя сложность (нужна обёртка 3–4 функции)

- [ ] **`ctlEnumPowerDomains` / `ctlTemperatureGetState` / `ctlPowerGetEnergyCounter`**
      — доп. датчики (потенциально hotspot памяти)
- [ ] **Память: `ctlEnumMemoryModules` / `ctlMemoryGetProperties` / `ctlMemoryGetState`**
      — измеримая проверка разгона памяти (сама overclocking.md ссылается на
      `ctlMemoryGetState` как на эталон; сейчас прирост 19→20 Гбит/с принимается
      на веру). Примечание: >21 Гбит/с недостижим (см. «Жёстко отказано»).
      `ctlMemoryGetBandwidth` — пропускная способность (4-я функция)

#### 3.4 Оценить на железе

- [ ] **`ctlPowerTelemetryGetV2`** — новая версия телеметрии; оценить, даёт ли
      больше полей на B580 (текущая V1 отдаёт gpuPowerPercent, counters, limited-флаги)

### 4. Удобство CLI / GUI

- [ ] **Профили** (save/load/apply набора OC-настроек) — в CLI и GUI
      (строится поверх текущих значений + журнала)
- [ ] `--device N` для двухкарточных систем (сейчас всегда первая дискретная Arc)
- [ ] Полный инвентарь `ctl*` в `doctor` (7 обязательных функций — мало для
      диагностики старого драйвера)
- [ ] GUI: вкладка об устройстве (PCIe, firmware, ECC) — после обёртки функций
- [ ] GUI: тёмная тема, треё, горячие клавиши (масштаб UI уже есть)

### Мелкие правки (chore)

- [ ] **Баг: `arctune log` без `--rollback` падает с TypeError** — `cmd_log` не
      делает `return`, `main()` получает `None` (печать проходит, падение после)
- [ ] Мёртвый флаг `telemetry --once` — удалить или задействовать
- [ ] `development.md` описывает несуществующий `telemetry --verbose`
- [ ] Устаревшие пометки «(TODO)» у autofind в README.md / architecture.md
- [ ] Skip-заглушка `test_enumerate_b580` в test_smoke.py — удалить
      (покрыто test_hardware.py)
- [ ] Примеры записи с safety-гардами (обещаны в examples/README.md)
- [ ] Инсталлятор / иконка / app.manifest (DPI-aware) для Windows-сборки

## Намеренно вне scope

Эти вещи проект **не** будет реализовывать — чтобы не создавать ложных ожиданий:

- [ ] **Страпы / тайминги памяти** — firmware применяет их при memory training,
      IGCL не даёт доступа
- [ ] **Прошивка / мод vBIOS** — для B580 нет модов с повышенным лимитом, риск кирпича
- [ ] **Обход лимита питания выше 120%** — аппаратный потолок (шунт на плате),
      требует пайки, вне программной области
- [ ] **Регулировка напряжения памяти (VRAM voltage)** — на B580 не поддерживается:
      `vramVoltageOffset`/`vramFrequencyOffset` возвращают `supported=false`
      (min=max=0), Intel пометил их «Deprecated / No Longer Supported» в
      `igcl_api.h`, а legacy-функция `ctlOverclockVramVoltageOffsetGet` отвечает
      `CTL_RESULT_ERROR_UNSUPPORTED_FEATURE`. В телеметрии `vram_voltage_v = 1.35 В`
      — это факт, а не настройка: у 6× Samsung K4ZAF325BC-SC20 напряжение фиксировано.
      Соответствующих элементов нет ни в CLI, ни в GUI.
- [ ] **Дисплейная подсистема / LED / I2C-AUX** (~80 функций IGCL: sharpness, EDID,
      genlock, ArcSync, RGB и т.п.) — вне профиля «мониторинг + разгон»;
      в `igcl_api.h` они разобраны, но не обёрнуты намеренно
