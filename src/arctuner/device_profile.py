"""Профили устройств Arc: жёсткие рамки (уровень-1) по device_id.

Каждая запись в tuner.py проходит двухуровневую проверку:
  1. Уровень-1 (этот модуль) — жёсткие рамки проекта, специфичные для модели.
  2. Уровень-2 (IGCL, ``_check_igcl_range``) — min/max/step/supported, которые
     драйвер объявил для КОНКРЕТНОГО устройства через ctlOverclockGetProperties.

Уровень-2 device-aware по построению: драйвер не даст выйти за аппаратный
предел. Уровень-1 существует как дополнительная консервативная рамка, выведенная
проектом из живого железа.

B580 (device_id 0xE20B) — проверена на живом железе, рамки зафиксированы.
Прочие/неизвестные дискретные Arc — уровень-1 рамок НЕТ: запись ограничивается
честным min/max/step из IGCL (выбор пользователя: «доверять IGCL для не-B580»).
Это не «доверие на слово»: драйвер сам не выйдет за аппаратные пределы шины
питания/памяти.

Инварианты AGENTS.md выполняются для ВСЕХ карт:
  - страпы памяти и vBIOS не поддерживаются вовсе;
  - обход аппаратного лимита питания невозможен (уровень-2 уважает драйвер);
  - каждая запись явная и журналируется.
"""

from __future__ import annotations

from dataclasses import dataclass

#: PCI device_id дискретной Battlemage B580.
DEVICE_ID_B580 = 0xE20B

# Проверенные рамки B580 (живые записи + ctlOverclockGetProperties).
B580_MIN_POWER_PERCENT = 50.0
B580_MAX_POWER_PERCENT = 120.0  # аппаратный лимит ≈226 Вт (шунт 2 мОм)
B580_VRAM_MIN_GBPS = 19.0
B580_VRAM_NOMINAL_GBPS = 20.0  # номинал чипа Samsung SC20; выше — явное решение
B580_VRAM_MAX_GBPS = 22.0  # максимум, декларируемый драйвером


@dataclass(frozen=True)
class DeviceProfile:
    """Жёсткие рамки (уровень-1) для одной модели GPU.

    Поля ``*_power_percent`` и ``vram_*_gbps`` равны ``None`` для карт без
    проверенных проектом рамок — тогда tuner не накладывает уровня-1 и
    полагается на min/max/step из IGCL (уровень-2).

    Attributes:
        name: человекочитаемое имя профиля.
        device_id: PCI device_id (``None`` у GENERIC — для любой неизвестной).
        tested: ``True`` — профиль проверен на живом железе проекта.
        min_power_percent: нижняя граница лимита питания, % (``None`` → IGCL).
        max_power_percent: потолок лимита питания, % (``None`` → IGCL).
        vram_min_gbps: нижняя граница скорости памяти, Гбит/с (``None`` → IGCL).
        vram_nominal_gbps: номинал чипа; выше — «сверх номинала», требует
            ``allow_beyond_nominal`` (``None`` → концепция отсутствует).
        vram_max_gbps: потолок скорости памяти, Гбит/с (``None`` → IGCL).
    """

    name: str
    device_id: int | None
    tested: bool
    min_power_percent: float | None
    max_power_percent: float | None
    vram_min_gbps: float | None
    vram_nominal_gbps: float | None
    vram_max_gbps: float | None


#: Проверенный профиль B580 (единственный с зафиксированными рамками).
B580_PROFILE = DeviceProfile(
    name="Intel Arc B580",
    device_id=DEVICE_ID_B580,
    tested=True,
    min_power_percent=B580_MIN_POWER_PERCENT,
    max_power_percent=B580_MAX_POWER_PERCENT,
    vram_min_gbps=B580_VRAM_MIN_GBPS,
    vram_nominal_gbps=B580_VRAM_NOMINAL_GBPS,
    vram_max_gbps=B580_VRAM_MAX_GBPS,
)

#: Профиль для любой неизвестной/не-проверенной карты: уровень-1 рамок нет,
#: запись ограничивается только честным диапазоном IGCL (уровень-2).
GENERIC_PROFILE = DeviceProfile(
    name="Intel Arc (неизвестная модель)",
    device_id=None,
    tested=False,
    min_power_percent=None,
    max_power_percent=None,
    vram_min_gbps=None,
    vram_nominal_gbps=None,
    vram_max_gbps=None,
)

_KNOWN_PROFILES: dict[int, DeviceProfile] = {
    DEVICE_ID_B580: B580_PROFILE,
}


def profile_for_device_id(device_id: int) -> DeviceProfile:
    """Возвращает профиль по ``device_id``.

    Неизвестное устройство получает :data:`GENERIC_PROFILE` — уровень-1 рамок
    нет, доверяем min/max/step из IGCL (уровень-2).

    Args:
        device_id: PCI device_id устройства.

    Returns:
        Профиль для этого device_id (B580 — проверенный, прочие — GENERIC).
    """
    return _KNOWN_PROFILES.get(device_id, GENERIC_PROFILE)


def profile_status_note(device_id: int) -> str:
    """Краткая честная пометка о статусе профиля для отображения пользователю.

    Пустая строка для проверенного устройства (B580). Для прочих карт —
    предупреждение, что потолки не верифицированы проектом и запись
    ограничивается только рамками драйвера (инвариант AGENTS.md: честно
    писать, если что-то не поддерживается).

    Args:
        device_id: PCI device_id устройства.

    Returns:
        Строка-пометка (``""`` для B580, иначе текст предупреждения).
    """
    if profile_for_device_id(device_id).tested:
        return ""
    return "не проверено — потолки из драйвера, осторожно с разгоном"
