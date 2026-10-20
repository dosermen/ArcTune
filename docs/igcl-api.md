# Разобранный IGCL API для Arc B580

Документ основан на анализе установленных бинарников Intel Graphics Software
(`IntelGraphicsSoftware.Wrapper.IGCL.dll`, драйвер `igdkmdnd64.sys` v32.0.101.9150).
Перечисленные здесь функции — **реальные точки входа**, используемые самим Intel
Graphics Software для управления дискретной Arc.

IGCL — **публичный** API Intel (заголовок `igcl_api.h`), поэтому всё сказанное здесь
можно использовать из собственного кода без реверса.

---

## Стек вызовов

```
ArcTune (Python / ctypes)
        ↓
IGCL.dll            ← публичный API, ctl* функции
        ↓
igdkmdnd64.sys      ← ядерный драйвер Arc (B580)
```

Нативная точка входа — `IGCL.dll`. В системе также лежит .NET-обёртка
`IntelGraphicsSoftware.Wrapper.IGCL.dll`, но мы обращаемся к `IGCL.dll` напрямую.

---

## Что фактически используется из IGCL (32 ctl*-функции)

Всего ArcTune обращается к 32 уникальным `ctl*`-функциям. В `IgclApi` 24 метода; два
из них (`get_pci_state`, `get_ecc_state`) вызывают по две ctl-функции — итого 26
вызовов, плюс `ctlOverclockWriteCustomVFCurve` (настраивается в `api.py`, вызывается
из `tuner.py`). Ещё пять V2 Get-функций `tuner.py` вызывает динамически. Ниже — 27
функций через обёртки/`tuner.py`; пять Get-функций перечислены отдельно.

- **Инициализация и устройство (4):** `ctlInit`, `ctlClose`, `ctlEnumerateDevices`,
  `ctlGetDeviceProperties`
- **Разгон (10):** `ctlOverclockGetProperties`, `ctlOverclockWaiverSet`,
  `ctlOverclockResetToDefault`, `ctlOverclockReadVFCurve`,
  `ctlOverclockWriteCustomVFCurve`, `ctlOverclockGpuFrequencyOffsetSetV2`,
  `ctlOverclockGpuMaxVoltageOffsetSetV2`, `ctlOverclockPowerLimitSetV2`,
  `ctlOverclockTemperatureLimitSetV2`, `ctlOverclockVramMemSpeedLimitSetV2`
- **Телеметрия (1):** `ctlPowerTelemetryGet`
- **Вентиляторы (7):** `ctlEnumFans`, `ctlFanGetProperties`, `ctlFanGetState`,
  `ctlFanSetFixedSpeedMode`, `ctlFanSetSpeedTableMode`, `ctlFanSetDefaultMode`,
  `ctlFanGetConfig`
- **PCIe / прошивка / ECC (5):** `ctlPciGetProperties`, `ctlPciGetState`,
  `ctlGetFirmwareProperties`, `ctlEccGetProperties`, `ctlEccGetState`

Отдельно `tuner.py` динамически вызывает пять V2 Get-функций
(`ctlOverclockGpuFrequencyOffsetGetV2`, `...GpuMaxVoltageOffsetGetV2`,
`...PowerLimitGetV2`, `...TemperatureLimitGetV2`, `...VramMemSpeedLimitGetV2`)
через `loader`, чтобы прочитать фактическое текущее значение. В `api.py` для них
нет отдельных обёрток; если функции нет в драйвере, чтение возвращает `None`.

---

## Функции разгона (`ctlOverclock*`)

| Функция | Назначение | В ArcTune |
|---|---|---|
| `ctlOverclockGetProperties` | свойства OC-объекта: поддержка, min/max/step/default | обёрнута |
| `ctlOverclockGpuFrequencyOffsetGetV2` / `...SetV2` | частотное смещение ядра | Get вызывается из `tuner.py`; Set обёрнут |
| `ctlOverclockGpuMaxVoltageOffsetGetV2` / `...SetV2` | смещение максимального напряжения ядра | Get вызывается из `tuner.py`; Set обёрнут |
| `ctlOverclockPowerLimitGetV2` / `...SetV2` | лимит питания (на B580 максимум 120%) | Get вызывается из `tuner.py`; Set обёрнут |
| `ctlOverclockTemperatureLimitGetV2` / `...SetV2` | лимит температуры | Get вызывается из `tuner.py`; Set обёрнут |
| `ctlOverclockVramFrequencyOffsetGet` / `...Set` | частотное смещение памяти | не обёрнута |
| `ctlOverclockVramMemSpeedLimitGetV2` / `...SetV2` | скорость памяти (Гбит/с) | Get вызывается из `tuner.py`; Set обёрнут |
| `ctlOverclockVramVoltageOffsetGet` / `...Set` | напряжение памяти (см. примечание ниже) | не обёрнута |
| `ctlOverclockReadVFCurve` | чтение кривой напряжение/частота | обёрнута |
| `ctlOverclockWriteCustomVFCurve` | запись пользовательской кривой V/F | обёрнута |
| `ctlOverclockWaiverSet` | установка waiver (обход предупреждения) | обёрнута |
| `ctlOverclockResetToDefault` | сброс к дефолту | обёрнута |

> **Примечание про напряжение памяти.** Функция `ctlOverclockVramVoltageOffset*`
> существует в API, но на B580 `vramVoltageOffset` возвращает `supported=false`
> (`min=max=0`), Intel помечает пункт deprecated, а legacy-функция отвечает
> `UNSUPPORTED_FEATURE`. Это не рабочая настройка на B580.

---

## Телеметрия (`ctlPowerTelemetryGet`)

Возвращает структуру `_ctl_power_telemetry_t`. Полезные поля:

**Причины ограничения (что мешает карте прямо сейчас):**
- `gpuPowerLimited`, `gpuVoltageLimited`, `gpuTemperatureLimited`,
  `gpuCurrentLimited`, `gpuUtilizationLimited`
- `vramPowerLimited`, `vramVoltageLimited`, `vramTemperatureLimited`, …

На B580 флаги `vram*Limited` помечены deprecated и всегда `false` — «в чём
упёрлась память» драйвер не сообщает.

**Датчики (поля, которые реально читаются на B580):**
- Энергия: `gpuEnergyCounter`, `vramEnergyCounter`, `totalCardEnergyCounter` (Дж)
- Напряжение: `gpuVoltage`, `vramVoltage`
- Частота: `gpuCurrentClockFrequency`, `vramCurrentClockFrequency`,
  `vramCurrentEffectiveFrequency`
- Температура: `gpuCurrentTemperature`, `vramCurrentTemperature`, `gpuVrTemp`,
  `vramVrTemp`, `saVrTemp`
- Активность: `globalActivityCounter`, `renderComputeActivityCounter`,
  `mediaActivityCounter`
- Питание: `gpuPowerPercent`, `gpuOverVoltagePercent`
- Пропускная способность памяти: `vramReadBandwidth`, `vramWriteBandwidth`
  (единицы `BANDWIDTH_MBPS`)

> **Про bandwidth-поля.** В структуре есть и `vramReadBandwidthCounter` /
> `vramWriteBandwidthCounter`, но на живой B580 они помечены unsupported.
> Рабочие поля — без суффикса `Counter`: `vramReadBandwidth` / `vramWriteBandwidth`.

Единицы измерения кодируются enum'ом: `FREQUENCY_MHZ`, `VOLTAGE_VOLTS`, `POWER_WATTS`,
`TEMPERATURE_CELSIUS`, `MEM_SPEED_GBPS`, `POWER_MILLIWATTS`, `VOLTAGE_MILLIVOLTS`,
`MEMORY_BYTES`, `BANDWIDTH_MBPS`.

---

## Структуры (модель данных)

| Структура | Роль |
|---|---|
| `_ctl_oc_properties_t` | свойства объекта разгона (поддержка, диапазоны) |
| `_ctl_oc_control_info_t` | описание одного регулируемого пункта |
| `_ctl_oc_telemetry_item_t` | элемент телеметрии (значение + единица) |
| `_ctl_power_telemetry_t` | снимок телеметрии питания |
| `_ctl_voltage_frequency_point_t` | точка кривой V/F (voltage, frequency) |
| `_ctl_vf_curve_type_t`, `_ctl_vf_curve_details_t` | тип/описание кривой V/F |
| `_ctl_fan_properties_t`, `_ctl_fan_config_t`, `_ctl_fan_speed_table_t` | вентиляторы |

---

## Пункты настройки (высокоуровневая модель)

Слой IGCC оборачивает OC-функции в именованные пункты, доступные через
`GetAllOverclockingItems`. Каждый пункт несёт `Min`, `Max`, `Step`, `Default`,
`IsSupported`, `IsReadOnly`, `CanControl`, `Unit`, `IsRelative`.

Известные имена пунктов:
- `GpuFrequencyOffset`, `GpuVoltageOffset`, `PowerLimit`, `TemperatureLimit`
- `VramFrequencyOffset`, `VramVoltageOffset`, `VramMemSpeedLimit`
- `VFCurve`, `FrameLimit`, `OnBoost`, `GpuPerformanceBoost`

---

## Вентиляторы

| Функция | Назначение | В ArcTune |
|---|---|---|
| `ctlEnumFans` | перечислить вентиляторы | обёрнута |
| `ctlFanGetProperties` | свойства вентилятора (`supportedModes`, `maxRPM`, `canControl`) | обёрнута |
| `ctlFanGetState` | текущая скорость (RPM) | обёрнута |
| `ctlFanGetConfig` | текущая конфигурация (режим, fixed RPM) | обёрнута |
| `ctlFanSetFixedSpeedMode` | фиксация скорости в RPM | обёрнута |
| `ctlFanSetDefaultMode` | режим по умолчанию (авто) | обёрнута |
| `ctlFanSetSpeedTableMode` | таблица температура→скорость | обёрнута, но **на B580 не работает** |

> **Почему нет кривой вентиляторов.** На живой B580 `supportedModes=0x2` — драйвер
> поддерживает только FIXED. `ctlFanSetSpeedTableMode` присутствует в API и обёрнута,
> но таблица temp/speed на этой карте недоступна, поэтому софт-кривая остаётся
> планом, а не текущей возможностью.

---

## Память

| Функция | Назначение | В ArcTune |
|---|---|---|
| `ctlEnumMemoryModules` | перечислить модули памяти | не обёрнута |
| `ctlMemoryGetProperties` | свойства (макс. частота, ReBAR) | не обёрнута |
| `ctlMemoryGetState` | текущая частота / состояние | не обёрнута |
| `ctlMemoryGetBandwidth` | пропускная способность | не обёрнута |

Текущую эффективную частоту памяти ArcTune читает не через `ctlMemoryGetState`, а из
телеметрии: `vramCurrentEffectiveFrequency` (см. `arctune telemetry`).

---

## Прочие полезные

| Функция | Назначение | В ArcTune |
|---|---|---|
| `ctlEnumerateDevices` | список GPU (найти B580) | обёрнута |
| `ctlGetDeviceProperties` | свойства устройства (DeviceID и т.п.) | обёрнута |
| `ctlEccGetProperties` / `ctlEccGetState` | ECC (на B580 `UNSUPPORTED_FEATURE`) | обёрнуты |
| `ctlEccSetState` | включение/выключение ECC | не обёрнута |
| `ctlPciGetProperties` / `ctlPciGetState` | PCIe-состояние и ReBAR | обёрнуты |
| `ctlAllowPCIeLinkSpeedUpdate` | обновление PCIe link speed | не обёрнута |
| `ctlGetFirmwareProperties` | версия firmware (на B580 `UNKNOWN`) | обёрнута |

---

## Чего в API **нет**

Подтверждено анализом — важно, чтобы не строить иллюзий:

- **Тайминги памяти / страпы.** Ни одна функция не даёт доступа к tCL/tRCD/tRP.
  Firmware применяет тайминги при memory training, до уровня IGCL они не доходят.
- **Запись vBIOS.** Не через этот API.
- **Разблокировка питания выше 120%.** `ctlOverclockPowerLimitSetV2` упрётся в
  значение, ограниченное драйвером/vBIOS (аппаратный потолок по шунту).

---

## Ссылки

- Заголовок публичного API: `igcl_api.h` (есть в open-source проектах Intel, напр.
  репозитории Level Zero / compute-runtime)
- Документация по IOCTL, если нужен уровень ниже: см. [architecture.md](architecture.md)
