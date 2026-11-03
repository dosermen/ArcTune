"""Вкладка «Об устройстве»: PCIe/ReBAR, прошивка, ECC (read-only, как `arctune info`)."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from arctuner.gui.context import DeviceReport
from arctuner.igcl.api import DeviceInfo, ExtendedDeviceInfo


class DeviceTab(QWidget):
    """Показывает статичные свойства устройства и PCIe-линк (только чтение).

    Секции независимы: device_info (имя/драйвер) приходит одним сигналом,
    отчёт PCIe/прошивка/ECC — другим; сбой одной секции (ECC не поддерживается)
    виден в report.errors и не обнуляет остальные.
    """

    refresh_requested = Signal()  # перечитать всё с карты (reload_device в воркере)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        # Каждая секция: ключ → value-лейбл; порядок ключей = порядок строк.
        dev_box, self._dev = self._section(
            "Устройство",
            ("name", "Имя"),
            ("ids", "ID (vendor:device / subsystem)"),
            ("driver", "Драйвер"),
            ("firmware_ver", "Прошивка (версия IGCL)"),
            ("freq", "Частота / ядра"),
        )
        pci_box, self._pci = self._section(
            "PCIe",
            ("link", "Линк"),
            ("bandwidth", "Пропускная способность"),
            ("rebar", "ReBAR"),
            ("address", "Адрес"),
        )
        fw_box, self._fw = self._section(
            "Прошивка",
            ("fw_name", "Имя"),
            ("fw_version", "Версия"),
        )
        ecc_box, self._ecc = self._section(
            "ECC",
            ("ecc_state", "Состояние"),
            ("ecc_pending", "После перезагрузки"),
        )
        # Пока отчёт не пришёл — секции не гадают, а ждут данных.
        for rows in (self._pci, self._fw, self._ecc):
            for label in rows.values():
                label.setText("—")

        self._errors = QLabel("")
        self._errors.setObjectName("hintLabel")
        self._errors.setWordWrap(True)
        self._errors.hide()  # покажется, если какие-то секции не прочитались

        self._refresh_btn = QPushButton("Обновить")
        self._refresh_btn.setToolTip("Перечитать свойства устройства с карты (read-only).")
        self._refresh_btn.clicked.connect(self.refresh_requested.emit)

        top = QHBoxLayout()
        top.addStretch(1)
        top.addWidget(self._refresh_btn)

        lay = QVBoxLayout(self)
        lay.addLayout(top)
        lay.addWidget(dev_box)
        lay.addWidget(pci_box)
        lay.addWidget(fw_box)
        lay.addWidget(ecc_box)
        lay.addWidget(self._errors)
        lay.addStretch(1)

    # --- заполнение ---

    def set_device_info(self, info: DeviceInfo, extended: ExtendedDeviceInfo) -> None:
        """Заполняет секцию «Устройство» (device_info_ready)."""
        self._dev["name"].setText(info.name)
        self._dev["ids"].setText(
            f"{info.vendor_id:#06X}:{info.device_id:#06X} / "
            f"{info.subsystem_vendor_id:#06X}:{info.subsystem_id:#06X}"
        )
        self._dev["driver"].setText(extended.driver_version_str)
        self._dev["firmware_ver"].setText(extended.firmware_version_str)
        self._dev["freq"].setText(f"{info.frequency_mhz} МГц / {info.num_xe_cores} Xe-ядер")

    def set_report(self, report: DeviceReport) -> None:
        """Заполняет секции PCIe/прошивка/ECC (device_report_ready)."""
        pci = report.pci
        if pci is not None:
            self._pci["link"].setText(f"Gen {pci.gen} x{pci.width}")
            bw = f"{pci.max_bandwidth_mbps:g} МБ/с" if pci.max_bandwidth_mbps is not None else "—"
            self._pci["bandwidth"].setText(bw)
            if pci.rebar_enabled:
                rebar = "включён"
            elif pci.rebar_supported:
                rebar = "ВЫКЛЮЧЕН"
            else:
                rebar = "не поддержан"
            self._pci["rebar"].setText(rebar)
            self._pci["address"].setText(pci.address)

        fw = report.firmware
        if fw is not None:
            self._fw["fw_name"].setText(fw.name)
            self._fw["fw_version"].setText(fw.version)

        ecc = report.ecc
        if ecc is not None:
            state = "включён" if ecc.current == "enabled" else "выключен"
            self._ecc["ecc_state"].setText(f"{state} ({ecc.current})")
            if ecc.pending != ecc.current:
                self._ecc["ecc_pending"].setText(f"{ecc.pending} (после перезагрузки)")
            else:
                self._ecc["ecc_pending"].setText("—")

        if report.errors:
            self._errors.setText("Нет данных: " + "; ".join(report.errors))
            self._errors.show()
        else:
            self._errors.hide()

    # --- вспомогательное ---

    @staticmethod
    def _section(title: str, *keys: tuple[str, str]) -> tuple[QGroupBox, dict[str, QLabel]]:
        """Группа «подпись: значение» + карта ключ → value-лейбл (порядок строк сохранён)."""
        box = QGroupBox(title)
        grid = QGridLayout(box)
        rows: dict[str, QLabel] = {}
        for i, (key, caption) in enumerate(keys):
            caption_label = QLabel(caption)
            caption_label.setObjectName("hintLabel")
            value = QLabel("—")
            grid.addWidget(caption_label, i, 0)
            grid.addWidget(value, i, 1)
            rows[key] = value
        grid.setColumnStretch(1, 1)
        return box, rows


__all__ = ["DeviceTab"]
