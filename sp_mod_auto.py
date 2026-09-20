"""Optional multi-folder CFA/Stack batch for SeePhot_Main.py."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
)


PLUGIN_BUTTON_LABEL = "Configure..."
AUTOMATIC_PLUGIN_VERSION = "0.4"


class AutomaticDialog(QDialog):
    """Run identical CFA channel extraction and stacking for multiple folders."""

    def __init__(self, context: dict[str, object], parent) -> None:
        super().__init__(parent)
        self.context = context
        self.setWindowTitle("SeePhot CFA/Stack Batch")
        self.resize(760, 560)
        self.stage = "idle"
        self.running = False
        self.pending_sources: list[Path] = []
        self.current_source: Path | None = None
        self.completed_results: list[tuple[Path, str]] = []
        self.failed_results: list[tuple[Path, str]] = []
        self.run_channels: tuple[str, ...] = ()
        self.run_plan_mode = "time"
        self.run_plan_values = (100, 1000)
        self.run_plan_suffixes: tuple[str, ...] = ()
        self.run_grouping_behavior = "continuous"
        self.run_allow_overwrite = False
        self.run_total = 0
        self.last_source_dialog_directory = Path.home()

        layout = QVBoxLayout(self)

        self.sources_group = QGroupBox("Original Seestar CFA directories")
        sources_layout = QVBoxLayout(self.sources_group)
        self.source_list = QListWidget()
        self.source_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.source_list.setAlternatingRowColors(True)
        sources_layout.addWidget(self.source_list)

        source_buttons = QHBoxLayout()
        self.add_source_button = QPushButton("Add Folder...")
        self.add_source_button.clicked.connect(self.choose_source_directory)
        self.remove_source_button = QPushButton("Remove Selected")
        self.remove_source_button.clicked.connect(self.remove_selected_sources)
        self.clear_sources_button = QPushButton("Clear")
        self.clear_sources_button.clicked.connect(self.clear_sources)
        source_buttons.addWidget(self.add_source_button)
        source_buttons.addWidget(self.remove_source_button)
        source_buttons.addWidget(self.clear_sources_button)
        source_buttons.addStretch(1)
        sources_layout.addLayout(source_buttons)
        layout.addWidget(self.sources_group)

        self.cfa_group = QGroupBox("CFA Channels / Stack")
        cfa_layout = QGridLayout(self.cfa_group)
        cfa_layout.setColumnStretch(3, 1)

        self.channel_l_check = QCheckBox("L")
        self.channel_l_check.setChecked(True)
        self.channel_g_check = QCheckBox("G")
        channel_row = QHBoxLayout()
        channel_row.addWidget(self.channel_l_check)
        channel_row.addWidget(self.channel_g_check)
        channel_row.addStretch(1)
        cfa_layout.addWidget(QLabel("Channels"), 0, 0)
        cfa_layout.addLayout(channel_row, 0, 1, 1, 3)

        self.plan_mode_combo = QComboBox()
        self.plan_mode_combo.addItem("Seconds", "time")
        self.plan_mode_combo.addItem("Frames", "frames")
        self.plan_mode_combo.currentIndexChanged.connect(self.update_plan_spinboxes)
        cfa_layout.addWidget(QLabel("Grouping"), 1, 0)
        cfa_layout.addWidget(self.plan_mode_combo, 1, 1)

        self.grouping_behavior_combo = QComboBox()
        self.grouping_behavior_combo.addItem("Continuous", "continuous")
        self.grouping_behavior_combo.addItem("Gap-aware", "gap_aware")
        self.grouping_behavior_combo.setCurrentIndex(
            self.grouping_behavior_combo.findData("gap_aware")
        )
        self.grouping_behavior_combo.currentIndexChanged.connect(self.update_grouping_behavior_tooltip)

        self.plan_one_spin = QSpinBox()
        self.plan_one_spin.setRange(1, 100000)
        self.plan_two_spin = QSpinBox()
        self.plan_two_spin.setRange(1, 100000)
        self.plan_one_spin.setFixedWidth(120)
        self.plan_two_spin.setFixedWidth(120)
        self.grouping_behavior_combo.setFixedWidth(120)
        self.plan_one_check = QCheckBox()
        self.plan_two_check = QCheckBox()
        self.plan_all_check = QCheckBox("ALL")
        self.plan_one_check.setChecked(True)
        self.plan_two_check.setChecked(False)
        self.plan_all_check.setChecked(False)
        cfa_layout.addWidget(QLabel("Group 1"), 2, 0)
        cfa_layout.addWidget(self.plan_one_spin, 2, 1)
        cfa_layout.addWidget(self.plan_one_check, 2, 2)
        cfa_layout.addWidget(QLabel("Group 2"), 3, 0)
        cfa_layout.addWidget(self.plan_two_spin, 3, 1)
        cfa_layout.addWidget(self.plan_two_check, 3, 2)
        cfa_layout.addWidget(QLabel("Group 3"), 4, 0)
        cfa_layout.addWidget(self.plan_all_check, 4, 1, 1, 2)

        behavior_label = QLabel("Mode")
        behavior_label.setToolTip("Choose how observation gaps affect every stack group in this batch.")
        cfa_layout.addWidget(behavior_label, 5, 0)
        cfa_layout.addWidget(self.grouping_behavior_combo, 5, 1, 1, 2)

        self.overwrite_check = QCheckBox("Overwrite existing stack result folders")
        self.overwrite_check.setChecked(True)
        cfa_layout.addWidget(self.overwrite_check, 6, 0, 1, 4)
        layout.addWidget(self.cfa_group)

        self.status_label = QLabel(
            "Add one or more CFA source directories, then select channels and stack groups."
        )
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMinimumHeight(120)
        layout.addWidget(self.log_view, stretch=1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self.start_button = QPushButton("Start All")
        buttons.addButton(self.start_button, QDialogButtonBox.ButtonRole.ActionRole)
        self.start_button.clicked.connect(self.start_run)
        self.close_button = buttons.button(QDialogButtonBox.StandardButton.Close)
        self.close_button.clicked.connect(self.reject)
        layout.addWidget(buttons)

        stack_signal = context.get("automatic_stack_finished")
        self.stack_signal = stack_signal if hasattr(stack_signal, "connect") else None
        if hasattr(stack_signal, "connect"):
            stack_signal.connect(self.on_stack_finished)
        self.finished.connect(self.cleanup_signals)
        self.update_plan_spinboxes()
        self.update_grouping_behavior_tooltip()

    def cleanup_signals(self, *_args) -> None:
        if hasattr(self.stack_signal, "disconnect"):
            try:
                self.stack_signal.disconnect(self.on_stack_finished)
            except TypeError:
                pass

    def log(self, message: str) -> None:
        self.log_view.append(message)
        append_log = self.context.get("append_log")
        if callable(append_log):
            append_log(f"CFA/Stack Batch: {message}")

    def set_status(self, message: str) -> None:
        self.status_label.setText(message)
        self.log(message)

    def choose_source_directory(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self,
            "Add Original Seestar CFA Directory",
            str(self.last_source_dialog_directory),
        )
        if selected:
            self.add_source_path(Path(selected))

    def add_source_path(self, path: Path) -> bool:
        source_path = path.expanduser().absolute()
        if not source_path.is_dir():
            self.set_status(f"Source directory not found: {source_path}")
            return False
        existing_paths = {str(path) for path in self.source_paths()}
        if str(source_path) in existing_paths:
            self.set_status(f"Source directory is already listed: {source_path}")
            return False
        self.source_list.addItem(str(source_path))
        self.last_source_dialog_directory = source_path.parent
        self.status_label.setText(f"{self.source_list.count()} source folder(s) selected.")
        return True

    def source_paths(self) -> list[Path]:
        return [Path(self.source_list.item(index).text()) for index in range(self.source_list.count())]

    def remove_selected_sources(self) -> None:
        for item in self.source_list.selectedItems():
            self.source_list.takeItem(self.source_list.row(item))
        self.status_label.setText(f"{self.source_list.count()} source folder(s) selected.")

    def clear_sources(self) -> None:
        self.source_list.clear()
        self.status_label.setText("No source directories selected.")

    def update_plan_spinboxes(self) -> None:
        if self.plan_mode_combo.currentData() == "frames":
            values = (10, 100)
            suffix = " Frames"
        else:
            values = (100, 1000)
            suffix = " s"
        for spin, value in ((self.plan_one_spin, values[0]), (self.plan_two_spin, values[1])):
            spin.blockSignals(True)
            spin.setSuffix(suffix)
            spin.setValue(value)
            spin.blockSignals(False)

    def current_grouping_behavior(self) -> str:
        return str(self.grouping_behavior_combo.currentData())

    def update_grouping_behavior_tooltip(self) -> None:
        text = "Gap-aware: Recommended for light-curve photometry. A large observation gap ends the current stack." if self.current_grouping_behavior() == "gap_aware" else "Continuous: stack groups may continue across an observation gap."
        self.grouping_behavior_combo.setToolTip(text)

    def selected_channels(self) -> tuple[str, ...]:
        channels: list[str] = []
        if self.channel_l_check.isChecked():
            channels.append("L")
        if self.channel_g_check.isChecked():
            channels.append("G")
        return tuple(channels)

    def available_plan_choices(self) -> list[tuple[str, str]]:
        mode = self.plan_mode_combo.currentData()
        one = self.plan_one_spin.value()
        two = self.plan_two_spin.value()
        if mode == "frames":
            return [
                (f"{one}img", f"{one} Frames"),
                (f"{two}img", f"{two} Frames"),
                ("all", "ALL"),
            ]
        return [
            (f"{one}sec", f"{one} s"),
            (f"{two}sec", f"{two} s"),
            ("all", "ALL"),
        ]

    def selected_plan_suffixes(self) -> tuple[str, ...]:
        plan_choices = self.available_plan_choices()
        suffixes: list[str] = []
        if self.plan_one_check.isChecked():
            suffixes.append(plan_choices[0][0])
        if self.plan_two_check.isChecked():
            suffixes.append(plan_choices[1][0])
        if self.plan_all_check.isChecked():
            suffixes.append(plan_choices[2][0])
        return tuple(suffixes)

    def set_run_controls_enabled(self, enabled: bool) -> None:
        self.sources_group.setEnabled(enabled)
        self.cfa_group.setEnabled(enabled)
        self.start_button.setEnabled(enabled)
        self.close_button.setEnabled(enabled)

    def reject(self) -> None:
        if self.running:
            self.set_status("CFA/Stack batch is still running; the dialog cannot be closed yet.")
            return
        super().reject()

    def start_run(self) -> None:
        sources = self.source_paths()
        channels = self.selected_channels()
        plan_suffixes = self.selected_plan_suffixes()
        if not sources:
            self.set_status("Add at least one source directory first.")
            return
        if not channels:
            self.set_status("Select at least one CFA channel.")
            return
        if not plan_suffixes:
            self.set_status("Select at least one stack group.")
            return
        if not callable(self.context.get("start_cfa_stack")):
            self.set_status("CFA Channels / Stack hook is not available from the main app.")
            return

        self.pending_sources = list(sources)
        self.current_source = None
        self.completed_results = []
        self.failed_results = []
        self.run_channels = channels
        self.run_plan_mode = str(self.plan_mode_combo.currentData())
        self.run_plan_values = (self.plan_one_spin.value(), self.plan_two_spin.value())
        self.run_plan_suffixes = plan_suffixes
        self.run_grouping_behavior = self.current_grouping_behavior()
        self.run_allow_overwrite = self.overwrite_check.isChecked()
        self.run_total = len(sources)
        self.running = True
        self.set_run_controls_enabled(False)
        self.log(
            "Starting CFA/Stack batch: "
            f"sources={self.run_total}, channels={', '.join(self.run_channels)}, "
            f"groups={', '.join(self.run_plan_suffixes)}, "
            f"behavior={self.grouping_behavior_combo.currentText()}, "
            f"overwrite={'yes' if self.run_allow_overwrite else 'no'}."
        )
        self.start_next_source()

    def start_next_source(self) -> None:
        if not self.running:
            return
        if not self.pending_sources:
            self.finish_run()
            return

        self.current_source = self.pending_sources.pop(0)
        current_number = self.run_total - len(self.pending_sources)
        self.stage = "stacking"
        self.set_status(
            f"[{current_number}/{self.run_total}] Running CFA Channels / Stack: "
            f"{self.current_source}"
        )
        start_cfa_stack = self.context.get("start_cfa_stack")
        assert callable(start_cfa_stack)
        try:
            started = start_cfa_stack(
                str(self.current_source),
                self.run_channels,
                self.run_plan_mode,
                self.run_plan_values,
                self.run_plan_suffixes,
                self.run_allow_overwrite,
                self.run_grouping_behavior,
            )
        except Exception as exc:
            self.record_start_failure(str(exc))
            return
        # Normal start failures emit automatic_stack_finished synchronously. Keep
        # a fallback for a hook that returns False without emitting the signal.
        if not started and self.stage == "stacking":
            self.record_start_failure("Stack worker could not be started.")

    def record_start_failure(self, message: str) -> None:
        if self.current_source is None:
            return
        source = self.current_source
        self.failed_results.append((source, message))
        self.log(f"[ERROR] {source}: {message}")
        self.current_source = None
        self.stage = "between_sources"
        QTimer.singleShot(0, self.start_next_source)

    def on_stack_finished(self, success: bool, message: str, payload: object) -> None:
        if not self.running or self.stage != "stacking" or self.current_source is None:
            return

        source = self.current_source
        if success:
            result_dir = ""
            if isinstance(payload, dict):
                result_dir = str(payload.get("result_dir") or "")
            if not result_dir:
                result_dir = message
            self.completed_results.append((source, result_dir))
            self.log(f"[OK] {source} -> {result_dir}")
        else:
            self.failed_results.append((source, message))
            self.log(f"[ERROR] {source}: {message}")

        self.current_source = None
        self.stage = "between_sources"
        QTimer.singleShot(0, self.start_next_source)

    def finish_run(self) -> None:
        completed = len(self.completed_results)
        failed = len(self.failed_results)
        self.running = False
        self.stage = "idle"
        self.current_source = None
        self.set_run_controls_enabled(True)
        self.select_last_successful_result()
        summary = (
            f"CFA/Stack batch finished: {completed} succeeded, {failed} failed, "
            f"{self.run_total} total."
        )
        self.set_status(summary)
        for source, message in self.failed_results:
            self.log(f"[FAILED] {source}: {message}")
        self.show_completion_dialog(completed, failed)

    def show_completion_dialog(self, completed: int, failed: int) -> None:
        message = (
            f"CFA/Stack processing finished.\n\n"
            f"Successful: {completed}\n"
            f"Failed: {failed}\n"
            f"Total: {self.run_total}"
        )
        if failed:
            message += "\n\nSee the log for details about failed folders."
            QMessageBox.warning(self, "CFA/Stack Batch Finished", message)
        else:
            QMessageBox.information(self, "CFA/Stack Batch Finished", message)

    def select_last_successful_result(self) -> None:
        if not self.completed_results:
            return
        _source, result_dir = self.completed_results[-1]
        set_source_directory = self.context.get("set_source_directory")
        if not callable(set_source_directory):
            self.log("[WARN] Last stack result could not be selected in FITS Directory: hook unavailable.")
            return
        if set_source_directory(result_dir):
            self.log(f"Selected last successful stack result in FITS Directory: {result_dir}")
        else:
            self.log(f"[WARN] Last stack result could not be selected in FITS Directory: {result_dir}")


def open_automatic_dialog(context: dict[str, object], parent) -> None:
    """Open the CFA/Stack Batch dialog."""

    append_log = context.get("append_log")
    if callable(append_log):
        append_log("CFA/Stack Batch requested.")

    dialog = AutomaticDialog(context, parent)
    dialog.exec()
