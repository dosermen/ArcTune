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

## Функции разгона (`ctlOverclock*`)

| Функция | Назначение |
|---|---|
| `ctlOverclockGetProperties` | свойства OC-объекта: поддержка, min/max/step/default |
| `ctlOverclockGpuFrequencyOffsetGetV2` / `...SetV2` | частотное смещение ядра |
| `ctlOverclockGpuMaxVoltageOffsetGetV2` / `...SetV2` | смещение максимального напряжения ядра |
| `ctlOverclockPowerLimitGetV2` / `...SetV2` | лимит питания (на B580 максимум 120%) |
| `ctlOverclockTemperatureLimitGetV2` / `...SetV2` | лимит температуры |
| `ctlOverclockVramFrequencyOffsetGet` / `...Set` | частотное смещение памяти |
| `ctlOverclockVramMemSpeedLimitGetV2` / `...SetV2` | скорость памяти (Гбит/с) |
| `ctlOverclockVramVoltageOffsetGet` / `...Set` | напряжение памяти (см. примечание ниже) |
| `ctlOverclockReadVFCurve` | чтение кривой напряжение/частота |
| `ctlOverclockWriteCustomVFCurve` | запись пользовательской кривой V/F |
| `ctlOverclockWaiverSet` | установка waiver (обход предупреждения) |
| `ctlOverclockResetToDefault` | сброс к дефолту |

> **Примечание про напряжение памяти.** Функция `ctlOverclockVramVoltageOffset*`
> существует в API, но в UI Intel Graphics Software поля нет. Поддерживается ли она на
> B580 — проверяется в рантайме через `get_IsSupported` / `get_IsReadOnly`. Не
> предполагайте, что запись сработает: драйвер может блокировать.

---

## Телеметрия (`ctlPowerTelemetryGet`)

Возвращает структуру `_ctl_power_telemetry_t`. Полезные поля:

**Причины ограничения (что мешает карте прямо сейчас):**
- `GpuPowerLimited`, `GpuVoltageLimited`, `GpuTemperatureLimited`,
  `GpuCurrentLimited`, `GpuUtilizationLimited`
- `VramPowerLimited`, `VramVoltageLimited`, `VramTemperatureLimited`, …

**Датчики:**
- Энергия: `GpuEnergyCounter`, `VramEnergyCounter`, `TotalCardEnergyCounter`
- Напряжение: `GpuVoltage`, `VramVoltage`
- Частота: `GpuCurrentClockFrequency`, `VramCurrentClockFrequency`, `VramCurrentEffectiveFrequency`
- Температура: `GpuCurrentTemperature`, `VramCurrentTemperature`
- Активность: `GlobalActivityCounter`, `RenderComputeActivityCounter`, `MediaActivityCounter`
- Пропускная способность памяти: `VramReadBandwidthCounter`, `VramWriteBandwidthCounter`

Единицы измерения кодируются enum'ом: `FrequencyMHz`, `VoltageVolts`, `PowerWatts`,
`TemperatureCelsius`, `MemoryGbps`, `PowerMilliWatts`, `VoltageMillivolts`, `MemoryBytes`.

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

| Функция | Назначение |
|---|---|
| `ctlEnumFans` | перечислить вентиляторы |
| `ctlFanGetProperties` | свойства вентилятора |
| `ctlFanGetConfig` | текущая конфигурация |
| `ctlFanSetDefaultMode` | режим по умолчанию |
| `ctlFanSetSpeedTableMode` | **кривая вентиляторов** (таблица температура→скорость) |

---

## Память

| Функция | Назначение |
|---|---|
| `ctlEnumMemoryModules` | перечислить модули памяти |
| `ctlMemoryGetProperties` | свойства (макс. частота, ReBAR) |
| `ctlMemoryGetState` | текущая частота / состояние |
| `ctlMemoryGetBandwidth` | пропускная способность |

---

## Прочие полезные

| Функция | Назначение |
|---|---|
| `ctlEnumerateDevices` | список GPU (найти B580) |
| `ctlGetDeviceProperties` | свойства устройства (DeviceID и т.п.) |
| `ctlEccGetProperties` / `ctlEccGetState` / `ctlEccSetState` | ECC |
| `ctlPciGetProperties` / `ctlPciGetState` | PCIe-состояние |
| `ctlAllowPCIeLinkSpeedUpdate` | обновление PCIe link speed |
| `ctlGetFirmwareProperties` | версия firmware |

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
