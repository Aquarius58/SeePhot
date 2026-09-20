"""Optional multi-target photometry batch tab for SeePhot_Main.py."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)


BATCH_PLUGIN_VERSION = "0.1"
BATCH_BUSY_STATE = "BATCH_RUNNING"


@dataclass
class BatchTarget:
    target: object
    name: str
    object_type: str
    magnitude: str
    checked: bool = False
    status: str = "not selected"
    step: str = ""
    result_csv: Path | None = None
    outcome: str = ""
    message: str = ""


def _target_text(target: object, attr: str) -> str:
    value = getattr(target, attr, "")
    if value is None:
        return ""
    return str(value).strip()


def _target_key(target: object) -> tuple[str, str, str, str]:
    value_for = getattr(target, "value_for", None)
    oid = ""
    if callable(value_for):
        oid = str(value_for(("OID", "oid")) or "").strip()
    if oid:
        return ("oid", oid, "", "")
    return (
        "name-ra-dec",
        _target_text(target, "name").casefold(),
        _target_text(target, "ra"),
        _target_text(target, "dec"),
    )


def completed_batch_status(done: int, failed: int, warning: int = 0) -> str:
    """Return the aggregate status for a normally completed batch."""

    if done <= 0:
        return "ERR"
    if failed > 0 or warning > 0:
        return "WARN"
    return "OK"


def status_for_selection(status: str, checked: bool) -> str:
    """Return the queue status after changing a target checkbox."""

    if checked and status == "not selected":
        return "queued"
    if not checked and status == "queued":
        return "not selected"
    return status


def magnitude_sort_value(text: str) -> float:
    """Return the leading numeric magnitude for table sorting."""

    match = re.search(r"[+-]?\d+(?:\.\d+)?", text)
    return float(match.group(0)) if match is not None else float("inf")


class BatchTab(QWidget):
    def __init__(self, context: dict[str, object]) -> None:
        super().__init__()
        self.context = context
        self.targets: list[BatchTarget] = []
        self.current_index: int | None = None
        self.running = False
        self.current_signal_result: tuple[bool, str] | None = None
        self.sort_column: int | None = None
        self.sort_ascending = True
        self.run_scope_locked = False
        self.batch_mode = ""

        layout = QVBoxLayout(self)

        controls = QHBoxLayout()
        self.reload_button = QPushButton("Reload")
        self.select_all_button = QPushButton("Select All")
        self.select_none_button = QPushButton("Select None")
        self.clear_button = QPushButton("Clear")
        self.run_button = QPushButton("Run")
        for button in (
            self.reload_button,
            self.select_all_button,
            self.select_none_button,
            self.clear_button,
            self.run_button,
        ):
            controls.addWidget(button)
        controls.addStretch(1)
        layout.addLayout(controls)

        self.status_label = QLabel("No VSX targets available. Run Detect Variables first.")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(
            ["Run", "Target", "Type", "Mag", "Status", "Result CSV", "Action"]
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(6, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionsClickable(True)
        header.setSortIndicatorShown(True)
        header.sectionClicked.connect(self.sort_by_column)
        layout.addWidget(self.table, stretch=1)

        self.action_buttons = [
            self.reload_button,
            self.select_all_button,
            self.select_none_button,
            self.clear_button,
            self.run_button,
        ]
        self.reload_button.clicked.connect(self.reload_visible_targets)
        self.select_all_button.clicked.connect(lambda: self.set_all_checked(True))
        self.select_none_button.clicked.connect(lambda: self.set_all_checked(False))
        self.clear_button.clicked.connect(self.clear_targets)
        self.run_button.clicked.connect(self.run_or_resume)
        self.refresh_plugin_view()
        self.update_buttons()

        signal = self.context.get("automatic_lightcurve_finished")
        self.lightcurve_signal = signal if hasattr(signal, "connect") else None
        if hasattr(signal, "connect"):
            signal.connect(self.on_lightcurve_finished)

    def append_log(self, message: str) -> None:
        append_log = self.context.get("append_log")
        if callable(append_log):
            append_log(f"Batch: {message}")

    def update_buttons(self) -> None:
        has_targets = bool(self.targets)
        self.reload_button.setEnabled(not self.running)
        self.select_all_button.setEnabled(not self.running and has_targets)
        self.select_none_button.setEnabled(not self.running and has_targets)
        self.clear_button.setEnabled(not self.running and has_targets)
        self.run_button.setEnabled(
            not self.running
            and any(row.checked and row.status == "queued" for row in self.targets)
        )

    def set_status(self, message: str) -> None:
        self.status_label.setText(message)
        self.append_log(message)

    def load_visible_targets(self) -> None:
        getter = self.context.get("get_visible_vsx_targets")
        visible = list(getter()) if callable(getter) else []
        visible_by_key = {_target_key(target): target for target in visible}
        if self.run_scope_locked:
            for row in self.targets:
                target = visible_by_key.get(_target_key(row.target))
                if target is None:
                    continue
                row.target = target
                row.name = _target_text(target, "name") or "Target"
                row.object_type = _target_text(target, "object_type")
                row.magnitude = _target_text(target, "magnitude")
            self.apply_current_sort()
            self.refresh_table()
            self.update_buttons()
            return
        old_by_key = {_target_key(row.target): row for row in self.targets}
        refreshed: list[BatchTarget] = []
        for target in visible:
            key = _target_key(target)
            old = old_by_key.get(key)
            if old is not None:
                old.target = target
                old.name = _target_text(target, "name") or "Target"
                old.object_type = _target_text(target, "object_type")
                old.magnitude = _target_text(target, "magnitude")
                refreshed.append(old)
                continue
            refreshed.append(
                BatchTarget(
                    target=target,
                    name=_target_text(target, "name") or "Target",
                    object_type=_target_text(target, "object_type"),
                    magnitude=_target_text(target, "magnitude"),
                )
            )
        self.targets = refreshed
        self.apply_current_sort()
        self.refresh_table()
        self.update_buttons()

    def reload_visible_targets(self) -> None:
        """Explicitly leave the completed-run scope and reload visible VSX targets."""

        if self.running:
            return
        self.run_scope_locked = False
        self.refresh_plugin_view()

    def clear_targets(self) -> None:
        if self.running:
            return
        self.targets.clear()
        self.current_index = None
        self.run_scope_locked = False
        self.refresh_table()
        self.status_label.setText("No VSX targets available. Run Detect Variables first.")
        self.refresh_plugin_view()
        self.update_buttons()

    def refresh_plugin_view(self) -> None:
        if self.running:
            return
        self.load_visible_targets()
        if self.targets:
            checked_count = sum(1 for row in self.targets if row.checked)
            self.status_label.setText(
                f"VSX targets: {len(self.targets)} visible, {checked_count} selected for batch."
            )
        else:
            self.status_label.setText("No VSX targets available. Run Detect Variables first.")
        self.update_buttons()

    def reset_plugin_view(self) -> None:
        if self.running:
            return
        self.targets.clear()
        self.current_index = None
        self.run_scope_locked = False
        self.refresh_table()
        self.status_label.setText("No VSX targets available. Run Detect Variables first.")
        self.update_buttons()

    def set_all_checked(self, checked: bool) -> None:
        if self.running:
            return
        for row in self.targets:
            row.checked = checked
            row.status = status_for_selection(row.status, checked)
        self.refresh_table()
        self.refresh_plugin_view()

    def run_or_resume(self) -> None:
        if self.running:
            return
        if not self.targets:
            QMessageBox.warning(self, "Batch", "No VSX targets are available.")
            return
        self.targets = [row for row in self.targets if row.checked]
        if not self.targets:
            self.refresh_table()
            QMessageBox.warning(self, "Batch", "Select at least one target for the batch.")
            self.refresh_plugin_view()
            return
        self.run_scope_locked = True
        for row in self.targets:
            row.status = "queued"
            row.step = ""
            row.result_csv = None
            row.outcome = ""
            row.message = ""
        mode_getter = self.context.get("get_photometry_mode")
        self.batch_mode = str(mode_getter() if callable(mode_getter) else "")
        self.refresh_table()
        pending = self.next_pending_index()
        if pending is None:
            self.status_label.setText("No queued targets remain.")
            self.update_buttons()
            return

        self.running = True
        self.update_buttons()
        self.set_status(f"Batch started: {self.count_done()} done / {len(self.targets)} total.")
        QTimer.singleShot(0, self.start_next_target)

    def finish_batch(self, message: str) -> None:
        self.running = False
        self.current_index = None
        self.current_signal_result = None
        self.set_status(message)
        self.update_buttons()

    def next_pending_index(self) -> int | None:
        for index, row in enumerate(self.targets):
            if row.checked and row.status == "queued":
                return index
        return None

    def count_done(self) -> int:
        return sum(1 for row in self.targets if row.status == "done")

    def start_next_target(self) -> None:
        if not self.running:
            return
        index = self.next_pending_index()
        if index is None:
            done = self.count_done()
            failed = self.count_failed()
            warning = self.count_warning()
            status = completed_batch_status(done, failed, warning)
            status_setter = self.context.get("set_batch_result_status")
            if callable(status_setter):
                status_setter(status)
            self.finish_batch(
                f"Batch finished ({status}): {done} done, {warning} warning, {failed} failed."
            )
            return

        self.current_index = index
        row = self.targets[index]
        row.status = "running"
        row.step = "select target"
        row.message = ""
        self.refresh_row(index)
        self.status_label.setText(f"Running {index + 1}/{len(self.targets)}: {row.name}")
        self.append_log(f"Starting target {index + 1}/{len(self.targets)}: {row.name}")

        selector = self.context.get("select_target_object")
        start_measurement = self.context.get("start_batch_measurement")
        start_light_curve = self.context.get("start_light_curve")
        create_light_curve = self.context.get("create_light_curve")
        runner = (
            (lambda: start_measurement(self.batch_mode))
            if callable(start_measurement)
            else (start_light_curve if callable(start_light_curve) else create_light_curve)
        )
        if not callable(selector) or not callable(runner):
            self.mark_current_failed("Main app batch API is incomplete.")
            QTimer.singleShot(0, self.after_current_target)
            return

        try:
            self.current_signal_result = None
            if not selector(row.target):
                self.mark_current_failed("Target selection failed.")
                QTimer.singleShot(0, self.after_current_target)
                return
            row.step = (
                "single measurement"
                if self.batch_mode == "single_measurement"
                else "create light curve"
            )
            self.refresh_row(index)
            runner()
            if not self.running or self.current_index != index:
                return
            if self.current_signal_result is None:
                self.mark_current_failed("Light curve finished without completion signal.")
            else:
                success, message = self.current_signal_result
                self.finish_current_target(success, message)
            self.current_signal_result = None
            QTimer.singleShot(0, self.after_current_target)
        except Exception as exc:
            self.mark_current_failed(str(exc))
            self.current_signal_result = None
            QTimer.singleShot(0, self.after_current_target)

    def on_lightcurve_finished(self, success: bool, message: str) -> None:
        if not self.running or self.current_index is None:
            return
        self.current_signal_result = (success, str(message))

    def finish_current_target(self, success: bool, message: str) -> None:
        if self.current_index is None:
            return
        row = self.targets[self.current_index]
        if success:
            path = Path(message)
            outcome = "OK"
            details = ""
            describer = self.context.get("describe_batch_result")
            if callable(describer):
                described = describer(path)
                if isinstance(described, tuple) and len(described) == 2:
                    outcome, details = str(described[0]), str(described[1])
            is_error = outcome.upper() == "ERROR"
            row.status = "failed" if is_error else "done"
            row.step = "failed" if is_error else "finished"
            row.result_csv = path
            row.outcome = outcome.upper()
            row.message = details
            if is_error:
                self.append_log(f"ERROR: Target {row.name}: {details}; diagnostic={path}")
            else:
                self.append_log(f"Finished target {row.name}: {path}")
        else:
            row.status = "failed"
            row.step = "failed"
            row.outcome = "ERROR"
            row.message = message
            self.append_log(f"ERROR: Target {row.name} failed: {message}")
        self.refresh_row(self.current_index)

    def after_current_target(self) -> None:
        if not self.running:
            return
        self.current_index = None
        QTimer.singleShot(0, self.start_next_target)

    def mark_current_failed(self, message: str) -> None:
        if self.current_index is None:
            return
        row = self.targets[self.current_index]
        row.status = "failed"
        row.step = "failed"
        row.outcome = "ERROR"
        row.message = message
        self.refresh_row(self.current_index)
        self.append_log(f"ERROR: Target {row.name} failed: {message}")

    def count_failed(self) -> int:
        return sum(1 for row in self.targets if row.status == "failed")

    def count_warning(self) -> int:
        return sum(1 for row in self.targets if row.outcome == "WARN")

    def refresh_table(self) -> None:
        self.table.setRowCount(len(self.targets))
        for index in range(len(self.targets)):
            self.refresh_row(index)

    def sort_by_column(self, column: int) -> None:
        """Sort targets safely without separating row widgets from target data."""

        if self.running or column < 0 or column >= self.table.columnCount():
            return
        if self.sort_column == column:
            self.sort_ascending = not self.sort_ascending
        else:
            self.sort_column = column
            self.sort_ascending = True
        self.apply_current_sort()
        order = (
            Qt.SortOrder.AscendingOrder
            if self.sort_ascending
            else Qt.SortOrder.DescendingOrder
        )
        self.table.horizontalHeader().setSortIndicator(column, order)
        self.refresh_table()

    def apply_current_sort(self) -> None:
        """Apply the selected header sort to the internal target list."""

        if self.running or self.sort_column is None:
            return
        self.targets.sort(
            key=lambda row: self.target_sort_key(row, self.sort_column),
            reverse=not self.sort_ascending,
        )

    @staticmethod
    def target_sort_key(row: BatchTarget, column: int) -> object:
        status_order = {
            "running": 0,
            "queued": 1,
            "done": 2,
            "failed": 3,
            "not selected": 4,
        }
        values: dict[int, object] = {
            0: (not row.checked, row.name.casefold()),
            1: row.name.casefold(),
            2: row.object_type.casefold(),
            3: (magnitude_sort_value(row.magnitude), row.magnitude.casefold()),
            4: (status_order.get(row.status, 99), row.status.casefold()),
            5: "" if row.result_csv is None else str(row.result_csv).casefold(),
            6: (row.result_csv is None, row.name.casefold()),
        }
        return values.get(column, row.name.casefold())

    def refresh_row(self, index: int) -> None:
        row = self.targets[index]
        values = [
            "",
            row.name,
            row.object_type,
            row.magnitude,
            self.status_text(row),
            "" if row.result_csv is None else str(row.result_csv),
        ]
        for column, value in enumerate(values):
            if column == 0:
                checkbox = self.table.cellWidget(index, column)
                if not isinstance(checkbox, QCheckBox):
                    checkbox = QCheckBox()
                    checkbox.stateChanged.connect(
                        lambda _state, row_index=index: self.on_row_check_changed(row_index)
                    )
                    self.table.setCellWidget(index, column, checkbox)
                checkbox.blockSignals(True)
                checkbox.setChecked(row.checked)
                checkbox.setEnabled(not self.running)
                checkbox.blockSignals(False)
                continue
            item = self.table.item(index, column)
            if item is None:
                item = QTableWidgetItem()
                self.table.setItem(index, column, item)
            item.setText(value)
            if column == 5:
                item.setToolTip(value)

        button = self.table.cellWidget(index, 6)
        if not isinstance(button, QPushButton):
            button = QPushButton("Show")
            button.clicked.connect(lambda _checked=False, row_index=index: self.show_row(row_index))
            self.table.setCellWidget(index, 6, button)
        button.setEnabled(row.result_csv is not None and row.result_csv.exists())

    def status_text(self, row: BatchTarget) -> str:
        if row.outcome:
            return f"{row.outcome}: {row.message}" if row.message else row.outcome
        if row.message:
            return f"{row.status}: {row.message}"
        if row.step:
            return f"{row.status}: {row.step}"
        return row.status

    def on_row_check_changed(self, index: int) -> None:
        if self.running or index < 0 or index >= len(self.targets):
            return
        checkbox = self.table.cellWidget(index, 0)
        self.targets[index].checked = isinstance(checkbox, QCheckBox) and checkbox.isChecked()
        self.targets[index].status = status_for_selection(
            self.targets[index].status,
            self.targets[index].checked,
        )
        self.refresh_row(index)
        checked_count = sum(1 for row in self.targets if row.checked)
        self.status_label.setText(
            f"VSX targets: {len(self.targets)} visible, {checked_count} selected for batch."
        )
        self.update_buttons()

    def show_row(self, index: int) -> None:
        if index < 0 or index >= len(self.targets):
            return
        row = self.targets[index]
        if row.result_csv is None or not row.result_csv.exists():
            QMessageBox.warning(self, "Batch Result", "Result CSV not found.")
            return
        shower = self.context.get("show_batch_result")
        if not callable(shower):
            shower = self.context.get("plot_light_curve_from_csv")
        if not callable(shower):
            QMessageBox.warning(self, "Batch Result", "Main app does not expose result display.")
            return
        try:
            shower(row.result_csv)
        except Exception as exc:
            QMessageBox.warning(self, "Batch Result", str(exc))
            self.append_log(f"ERROR: Result display failed for {row.name}: {exc}")


def create_batch_tab(context: dict[str, object]) -> object:
    return BatchTab(context)
