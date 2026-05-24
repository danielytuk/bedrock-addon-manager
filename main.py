import json
import sys
from pathlib import Path

from PySide6.QtCore import Qt, QThread, QObject, Signal, QMutex, QMutexLocker
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import (
    QApplication, QFileDialog, QFrame, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar,
    QPushButton, QToolButton, QVBoxLayout, QWidget,
)
from qt_material import apply_stylesheet

from core import SUPPORTED_EXT, AddonItem, detect_packs, format_bytes, get_ffmpeg_path, process_addon
from version import VERSION


class DropZone(QLabel):
    files_dropped = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setAlignment(Qt.AlignCenter)
        self.setText("Drop .mcaddon / .zip / .mcpack files here\nor click to browse")
        self.setMinimumHeight(130)
        self.setCursor(Qt.PointingHandCursor)
        self.setFrameShape(QFrame.StyledPanel)
        self._normal = """
            QLabel {
                border: 2px dashed #888;
                border-radius: 12px;
                font-size: 14px;
                padding: 20px;
                background: transparent;
            }
            QLabel:hover {
                border-color: #5bbf3a;
                background: rgba(91, 191, 58, 0.05);
            }
        """
        self._hover = "border: 2px dashed #5bbf3a; border-radius: 12px; font-size: 14px; padding: 20px; background: rgba(91, 191, 58, 0.1);"
        self.setStyleSheet(self._normal)

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self.setStyleSheet(self._hover)

    def dragLeaveEvent(self, event):
        self.setStyleSheet(self._normal)

    def dropEvent(self, event: QDropEvent):
        self.setStyleSheet(self._normal)
        paths = [
            Path(url.toLocalFile()) for url in event.mimeData().urls()
            if url.isLocalFile() and Path(url.toLocalFile()).suffix.lower() in SUPPORTED_EXT
        ]
        if paths:
            self.files_dropped.emit(paths)

    def mousePressEvent(self, event):
        files, _ = QFileDialog.getOpenFileNames(
            self, "Select Addon Files", "",
            "Addon Files (*.mcaddon *.zip *.mcpack);;All Files (*.*)",
        )
        if files:
            self.files_dropped.emit([Path(f) for f in files])


class AddonItemWidget(QFrame):
    remove_clicked = Signal(AddonItem)

    def __init__(self, addon: AddonItem, parent=None):
        super().__init__(parent)
        self.addon = addon
        self.setFrameShape(QFrame.NoFrame)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(12)
        name_label = QLabel(addon.file_name)
        name_label.setStyleSheet("font-weight: 600;")
        layout.addWidget(name_label, 1)
        rp_label = QLabel("RP")
        rp_label.setStyleSheet(f"color: {'#5bbf3a' if addon.has_rp else '#666'}; font-weight: bold;")
        layout.addWidget(rp_label)
        bp_label = QLabel("BP")
        bp_label.setStyleSheet(f"color: {'#5bbf3a' if addon.has_bp else '#666'}; font-weight: bold;")
        layout.addWidget(bp_label)
        size_label = QLabel(format_bytes(addon.file_size))
        size_label.setStyleSheet("color: #999;")
        layout.addWidget(size_label)
        remove_btn = QPushButton("\u00d7")
        remove_btn.setFixedSize(28, 28)
        remove_btn.setToolTip("Remove this addon")
        remove_btn.clicked.connect(lambda: self.remove_clicked.emit(self.addon))
        layout.addWidget(remove_btn)


class ExportWorker(QObject):
    progress = Signal(int, str)
    addon_done = Signal(str, bool, str)
    finished = Signal()

    def __init__(self, addons: list[AddonItem], output_dir: Path, ffmpeg_path: Path | None = None):
        super().__init__()
        self.addons = addons
        self.output_dir = output_dir
        self.ffmpeg_path = ffmpeg_path
        self._mutex = QMutex()
        self._cancelled = False

    def cancel(self):
        with QMutexLocker(self._mutex):
            self._cancelled = True

    def run(self):
        try:
            self.output_dir.mkdir(parents=True, exist_ok=True)
            resource_entries = []
            behavior_entries = []
            total = len(self.addons)

            for idx, addon in enumerate(self.addons):
                cancel_flag = [False]

                def check_cancel():
                    with QMutexLocker(self._mutex):
                        if self._cancelled:
                            cancel_flag[0] = True

                self.progress.emit(int(idx / total * 100), f"Processing {addon.file_name}...")

                try:
                    def on_progress(pct: int, msg: str):
                        check_cancel()
                        self.progress.emit(int((idx + pct / 100) / total * 100), f"[{addon.file_name}] {msg}")

                    process_addon(addon, self.output_dir, on_progress, cancel_flag, self.ffmpeg_path)

                    if cancel_flag[0]:
                        break

                    if addon.new_rp_uuid:
                        resource_entries.append({"pack_id": addon.new_rp_uuid, "version": [1, 0, 0]})
                    if addon.new_bp_uuid:
                        behavior_entries.append({"pack_id": addon.new_bp_uuid, "version": [1, 0, 0]})
                    self.addon_done.emit(addon.file_name, True, "Patched successfully")

                except Exception as e:
                    self.addon_done.emit(addon.file_name, False, str(e))

            if not self._cancelled:
                if resource_entries:
                    (self.output_dir / "world_resource_packs.json").write_text(json.dumps(resource_entries, indent=2), "utf-8")
                if behavior_entries:
                    (self.output_dir / "world_behavior_packs.json").write_text(json.dumps(behavior_entries, indent=2), "utf-8")
                self.progress.emit(100, "Export complete!")
            else:
                self.progress.emit(0, "Cancelled")

            self.finished.emit()

        except Exception as e:
            self.progress.emit(0, f"Export error: {e}")
            self.finished.emit()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"Bedrock Addon Manager \u2014 {VERSION}")
        self.setMinimumSize(820, 620)
        self.addons: list[AddonItem] = []
        self.output_dir: Path = Path.home() / "Desktop" / "BedrockOutput"
        self.theme_dark = True
        self.ffmpeg_path = get_ffmpeg_path()
        self.ffmpeg_ok = self.ffmpeg_path is not None
        self._export_thread: QThread | None = None
        self._setup_ui()
        self._connect_signals()
        self._update_export_button()

    def _setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setSpacing(10)
        root.setContentsMargins(16, 12, 16, 12)

        header = QHBoxLayout()
        title = QLabel("Bedrock Addon Manager")
        title.setStyleSheet("font-size: 20px; font-weight: 700;")
        header.addWidget(title)
        ver_label = QLabel(VERSION)
        ver_label.setStyleSheet("color: #888; font-size: 11px; margin-left: 4px;")
        header.addWidget(ver_label)
        header.addStretch()

        ffmpeg_label = QLabel("FFmpeg: OK" if self.ffmpeg_ok else "FFmpeg: not found (audio will be skipped)")
        ffmpeg_label.setStyleSheet(f"color: {'#5bbf3a' if self.ffmpeg_ok else '#ffa500'}; font-size: 11px;")
        header.addWidget(ffmpeg_label)

        self.theme_btn = QToolButton()
        self.theme_btn.setText("\u2601")
        self.theme_btn.setToolTip("Toggle dark/light theme")
        self.theme_btn.setFixedSize(36, 36)
        header.addWidget(self.theme_btn)
        root.addLayout(header)

        self.drop_zone = DropZone()
        root.addWidget(self.drop_zone)

        list_header = QHBoxLayout()
        list_header.addWidget(QLabel("Imported Addons"))
        list_header.addStretch()
        self.count_label = QLabel("0 addons")
        self.count_label.setStyleSheet("color: #999;")
        list_header.addWidget(self.count_label)
        list_header.addSpacing(12)
        self.clear_btn = QPushButton("Clear All")
        self.clear_btn.setStyleSheet("padding: 4px 16px;")
        list_header.addWidget(self.clear_btn)
        root.addLayout(list_header)

        self.addon_list = QListWidget()
        self.addon_list.setMinimumHeight(160)
        self.addon_list.setAlternatingRowColors(True)
        self.addon_list.setSelectionMode(QListWidget.NoSelection)
        root.addWidget(self.addon_list, 1)

        folder_row = QHBoxLayout()
        folder_row.addWidget(QLabel("Output folder:"))
        self.folder_label = QLabel(str(self.output_dir))
        self.folder_label.setStyleSheet("color: #999;")
        self.folder_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        folder_row.addWidget(self.folder_label, 1)
        self.browse_btn = QPushButton("Browse")
        self.browse_btn.setStyleSheet("padding: 4px 16px;")
        folder_row.addWidget(self.browse_btn)
        root.addLayout(folder_row)

        self.export_btn = QPushButton("Export & Patch All")
        self.export_btn.setMinimumHeight(42)
        self.export_btn.setStyleSheet("font-weight: 700; font-size: 14px;")
        root.addWidget(self.export_btn)

        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(True)
        root.addWidget(self.progress_bar)

        self.status_label = QLabel("Ready")
        self.status_label.setStyleSheet("color: #999;")
        root.addWidget(self.status_label)

        self.log_output = QPlainTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setMaximumHeight(140)
        self.log_output.setVisible(False)
        root.addWidget(self.log_output)

    def _connect_signals(self):
        self.drop_zone.files_dropped.connect(self.on_files_dropped)
        self.clear_btn.clicked.connect(self.on_clear)
        self.browse_btn.clicked.connect(self.on_browse_output)
        self.export_btn.clicked.connect(self.on_export)
        self.theme_btn.clicked.connect(self.on_toggle_theme)

    def _update_export_button(self):
        self.export_btn.setEnabled(len(self.addons) > 0)
        n = len(self.addons)
        self.count_label.setText(f"{n} addon{'s' if n != 1 else ''}")

    def _add_list_item(self, addon: AddonItem):
        item = QListWidgetItem(self.addon_list)
        widget = AddonItemWidget(addon)
        widget.remove_clicked.connect(self._on_remove_addon)
        item.setSizeHint(widget.sizeHint())
        self.addon_list.addItem(item)
        self.addon_list.setItemWidget(item, widget)

    def _on_remove_addon(self, addon: AddonItem):
        for i in range(self.addon_list.count()):
            item = self.addon_list.item(i)
            w = self.addon_list.itemWidget(item)
            if isinstance(w, AddonItemWidget) and w.addon is addon:
                self.addon_list.takeItem(i)
                self.addons.remove(addon)
                self._update_export_button()
                self.log(f"Removed {addon.file_name}")
                return

    def log(self, message: str):
        self.log_output.appendPlainText(message)
        self.log_output.setVisible(True)

    def on_files_dropped(self, paths: list[Path]):
        for path in paths:
            if any(a.file_path == path for a in self.addons):
                self.log(f"Skipped duplicate: {path.name}")
                continue
            try:
                info = detect_packs(path)
                if not info["has_rp"] and not info["has_bp"]:
                    self.log(f"Skipped {path.name}: no valid pack manifests found")
                    continue
                addon = AddonItem(
                    file_path=path,
                    has_rp=info["has_rp"],
                    has_bp=info["has_bp"],
                    rp_manifest_path=info["rp_manifest_path"] or "",
                    bp_manifest_path=info["bp_manifest_path"] or "",
                    rp_original_uuid=info["rp_original_uuid"] or "",
                    bp_original_uuid=info["bp_original_uuid"] or "",
                    pack_name=info["pack_name"] or "",
                    pack_description=info["pack_description"] or "",
                )
                self.addons.append(addon)
                self._add_list_item(addon)
                self._update_export_button()
                self.log(f"Imported {path.name}")
            except Exception as e:
                self.log(f"Failed to import {path.name}: {e}")

    def on_clear(self):
        self.addons.clear()
        self.addon_list.clear()
        self._update_export_button()
        self.log("Cleared all addons")

    def on_browse_output(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Output Folder", str(self.output_dir))
        if folder:
            self.output_dir = Path(folder)
            self.folder_label.setText(str(self.output_dir))

    def on_export(self):
        if not self.addons:
            QMessageBox.warning(self, "No Addons", "Import at least one addon first.")
            return

        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.export_btn.setEnabled(False)
        self.clear_btn.setEnabled(False)
        self.drop_zone.setEnabled(False)
        self.progress_bar.setValue(0)
        self.log_output.clear()
        self.log_output.setVisible(True)
        self.log("Starting export...")

        self._export_thread = QThread(self)
        self._export_worker = ExportWorker(list(self.addons), self.output_dir, self.ffmpeg_path)
        self._export_worker.moveToThread(self._export_thread)

        self._export_thread.started.connect(self._export_worker.run)
        self._export_worker.finished.connect(self._export_thread.quit)
        self._export_worker.finished.connect(self._export_worker.deleteLater)
        self._export_thread.finished.connect(self._export_thread.deleteLater)
        self._export_worker.progress.connect(self._on_export_progress)
        self._export_worker.addon_done.connect(self._on_addon_done)
        self._export_worker.finished.connect(self._on_export_finished)
        self._export_thread.start()

    def _on_export_progress(self, pct: int, msg: str):
        self.progress_bar.setValue(pct)
        self.status_label.setText(msg)
        self.log(msg)

    def _on_addon_done(self, name: str, success: bool, detail: str):
        self.log(f"{'\u2713' if success else '\u2717'} {name}: {detail}")

    def _on_export_finished(self):
        self.export_btn.setEnabled(True)
        self.clear_btn.setEnabled(True)
        self.drop_zone.setEnabled(True)
        self._export_thread = None
        self._export_worker = None
        self.log("Export finished.")

    def on_toggle_theme(self):
        self.theme_dark = not self.theme_dark
        apply_stylesheet(QApplication.instance(), "dark_teal.xml" if self.theme_dark else "light_teal.xml")
        self.theme_btn.setText("\u2601" if self.theme_dark else "\u2600")


def main():
    app = QApplication(sys.argv)
    app.setOrganizationName("BedrockAddonManager")
    app.setApplicationName("Bedrock Addon Manager")
    app.setStyle("Fusion")
    apply_stylesheet(app, "dark_teal.xml")
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
