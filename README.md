# ArcTune

Python-обёртка над **IGCL (Intel Graphics Control Library)** для мониторинга и разгона
дискретных видеокарт **Intel Arc** (проверено на **Arc B580 / Battlemage**).

Проект даёт программный доступ к телеметрии и настройкам разгона, которые есть в
Intel Graphics Software, но из кода: лимит питания, напряжение/частота ядра, скорость
памяти, кривая V/F, кривая вентиляторов — плюс автоматический поиск стабильной точки.

> ⚠️ **Предупреждение.** Разгон и изменение лимитов видеокарты — на ваш страх и риск.
> Проект не аффилирован с Intel. Использование может аннулировать гарантию и, при
> злоупотреблении, привести к нестабильности или повреждению железа. Читайте
> [docs/safety.md](docs/safety.md) **до** любых изменений настроек.

---

## Статус

| | |
|---|---|
| Стадия | 🚧 **Alpha / в разработке** |
| Платформа | Windows 10/11 x64 |
| Железо | Intel Arc (дискретная), проверяется на B580 |
| Драйвер | Intel Arc Graphics 32.0.101.9xxx+ |
| Лицензия | MIT |

Сейчас реализовано и проверено на живой B580:

- ✅ **Read-only телеметрия** — датчики, причины ограничения, вентиляторы, кривая V/F
- ✅ **Запись настроек** — лимит питания / частота / напряжение / температура / память,
  с двухуровневой проверкой и откатом
- ✅ **Журнал + откат**, экспорт телеметрии в CSV

Дальше — автопоиск стабильной точки и GUI (см. [ROADMAP](docs/roadmap.md)).

---

## Как это работает

Intel Graphics Software управляет разгоном Arc через нативную библиотеку `IGCL.dll`
(публичный API Intel, header `igcl_api.h`). Слой выше — .NET-обёртка
`IntelGraphicsSoftware.Wrapper.IGCL.dll`. Мы обращаемся к `IGCL.dll` напрямую через
`ctypes`, минуя GUI.

```
┌─────────────────────────────┐
│   ArcTune (Python)          │  ← этот проект
│   ctypes → IGCL.dll         │
├─────────────────────────────┤
│   IGCL.dll                  │  публичный API Intel (ctl* функции)
├─────────────────────────────┤
│   igdkmdnd64.sys            │  ядерный драйвер Arc (B580)
└─────────────────────────────┘
```

Полный список задействованных функций и структур — в [docs/igcl-api.md](docs/igcl-api.md).

---

## Быстрый старт

```powershell
# 1. Клонируйте и войдите в папку
cd F:\AI\INTEL

# 2. Создайте виртуальное окружение
python -m venv .venv
.venv\Scripts\Activate.ps1

# 3. Установите зависимости
pip install -e ".[dev]"

# 4. Проверьте, что драйвер и IGCL видны
python -m arctuner doctor

# 5. Прочитать телеметрию (read-only, безопасно)
python -m arctuner telemetry
```

Подробнее — в [docs/development.md](docs/development.md).

---

## Структура проекта

```
.
├── src/arctuner/
│   ├── igcl/            # низкоуровневая обёртка над IGCL (ctypes)
│   │   ├── loader.py    #   загрузка IntelControlLib.dll/ControlLib.dll, поиск функций
│   │   ├── types.py     #   C-структуры (_ctl_*_t) и enum'ы (по igcl_api.h)
│   │   └── api.py       #   Python-функции ctl* + преобразование ошибок
│   ├── telemetry.py     # чтение датчиков (В, МГц, °C, %, причины троттлинга)
│   ├── tuner.py         # установка настроек (write, двухуровневые проверки)
│   ├── logbook.py       # журнал изменений + расчёт отката (JSONL)
│   ├── autofind.py      # автоматический поиск стабильной точки (TODO)
│   └── cli.py           # командная строка
├── tests/               # тесты (unit + hardware)
├── docs/                # документация
└── examples/            # примеры использования
```

---

## Команды

```powershell
python -m arctuner doctor              # проверка драйвера/IGCL (read-only)
python -m arctuner telemetry           # снимок датчиков + причины ограничения
python -m arctuner telemetry --csv out.csv   # + сохранить снимок в CSV
python -m arctuner telemetry -w 1      # наблюдать каждую секунду
python -m arctuner status              # диапазоны пунктов разгона (min/max/step)
python -m arctuner fans                # скорость/свойства вентиляторов
python -m arctuner log                 # история изменений настроек

# ⚠️ ниже — запись (меняет карту, требует подтверждения):
python -m arctuner set --power-limit 110    # лимит питания, %
python -m arctuner set --vram-speed 20      # память, Гбит/с (≤20 без --force)
python -m arctuner fans --set 1500          # зафиксировать вентиляторы, RPM
python -m arctuner log --rollback           # откат к исходным значениям
python -m arctuner reset --oc --fans        # сброс OC + вентиляторы в авто
```

---

## Документация

| Документ | Что внутри |
|---|---|
| [docs/safety.md](docs/safety.md) | **Правила безопасности. Читать первым.** |
| [docs/architecture.md](docs/architecture.md) | Архитектура, слои, дизайн-решения |
| [docs/igcl-api.md](docs/igcl-api.md) | Разобранный IGCL API: функции, структуры, поля |
| [docs/overclocking.md](docs/overclocking.md) | Методика разгона Arc B580 и тонкости |
| [docs/development.md](docs/development.md) | Настройка окружения, запуск, тесты |
| [docs/roadmap.md](docs/roadmap.md) | Планы и что уже сделано |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Правила участия и стиль кода |

---

## Чего проект **не** делает

Честно о границах возможного — чтобы не тратить ваше время впустую:

- **Не меняет тайминги памяти (страпы).** Их применяет firmware GPU при обучении памяти
  (memory training). IGCL не экспонирует тайминги ни в одной версии API.
- **Не прошивает vBIOS.** Для B580 модифицированных vBIOS с повышенным лимитом не существует.
- **Не обходит аппаратный лимит питания.** Лимит ~226 Вт (120% от 190 Вт) считается по шунту
  на плате; софт его не снимает (см. [overclocking.md](docs/overclocking.md)).

---

## Лицензия

MIT — см. [LICENSE](LICENSE).
