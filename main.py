import json
import sys
from pathlib import Path

from PySide6.QtCore import Qt, QThread, QObject, Signal, QMutex, QMutexLocker
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QFileDialog, QFrame, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar,
    QPushButton, QSizePolicy, QToolButton, QVBoxLayout, QWidget,
)
from qt_material import apply_stylesheet

from core import (
    SUPPORTED_EXT, AddonItem, detect_packs, format_bytes, get_ffmpeg_path,
    merge_pack_entries, process_addon,
)
from version import VERSION


STYLE_DROP_NORMAL = """
    QLabel {
        border: 2px dashed #888;
        border-radius: 10px;
        font-size: 13px;
        padding: 16px;
        background: transparent;
    }
    QLabel:hover {
        border-color: #5bbf3a;
        background: rgba(91, 191, 58, 0.05);
    }
"""
STYLE_DROP_HOVER = """
    border: 2px dashed #5bbf3a;
    border-radius: 10px;
    font-size: 13px;
    padding: 16px;
    background: rgba(91, 191, 58, 0.1);
"""
STYLE_ADDON_ITEM = """
    QFrame#addonItem {
        border: none;
        border-bottom: 1px solid rgba(128, 128, 128, 0.15);
        border-radius: 0;
    }
    QFrame#addonItem:hover {
        background: rgba(91, 191, 58, 0.04);
    }
"""
STYLE_BTN_SM = "padding: 3px 12px; font-size: 12px;"
STYLE_BTN_FILE = "padding: 4px 14px; font-size: 12px;"
STYLE_SECTION_LABEL = "font-weight: 600; font-size: 12px; color: #888; padding: 2px 0;"
STYLE_BADGE_ON = "color: #5bbf3a; font-weight: bold; font-size: 11px; padding: 1px 4px; border: 1px solid #5bbf3a; border-radius: 3px;"
STYLE_BADGE_OFF = "color: #555; font-size: 11px; padding: 1px 4px;"


class DropZone(QFrame):
    files_dropped = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setMinimumHeight(100)
        self.setCursor(Qt.PointingHandCursor)
        self.setFrameShape(QFrame.StyledPanel)
        self.setStyleSheet(STYLE_DROP_NORMAL)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(6)

        self._label = QLabel("Drop .mcaddon / .mcpack / .zip or folders containing packs")
        self._label.setAlignment(Qt.AlignCenter)
        self._label.setStyleSheet("border: none; font-size: 13px;")
        layout.addWidget(self._label)

        btn_row = QHBoxLayout()
        btn_row.addStretch()

        self._browse_files_btn = QPushButton("Browse Files")
        self._browse_files_btn.setStyleSheet(STYLE_BTN_FILE)
        self._browse_files_btn.setCursor(Qt.ArrowCursor)
        self._browse_files_btn.clicked.connect(self._on_browse_files)
        btn_row.addWidget(self._browse_files_btn)

        btn_row.addSpacing(8)

        self._browse_folder_btn = QPushButton("Browse Folder")
        self._browse_folder_btn.setStyleSheet(STYLE_BTN_FILE)
        self._browse_folder_btn.setCursor(Qt.ArrowCursor)
        self._browse_folder_btn.clicked.connect(self._on_browse_folder)
        btn_row.addWidget(self._browse_folder_btn)

        layout.addLayout(btn_row)

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self.setStyleSheet(STYLE_DROP_HOVER)

    def dragLeaveEvent(self, event):
        self.setStyleSheet(STYLE_DROP_NORMAL)

    def dropEvent(self, event: QDropEvent):
        self.setStyleSheet(STYLE_DROP_NORMAL)
        paths = []
        for url in event.mimeData().urls():
            if url.isLocalFile():
                p = Path(url.toLocalFile())
                if p.is_dir() or p.suffix.lower() in SUPPORTED_EXT:
                    paths.append(p)
        if paths:
            self.files_dropped.emit(paths)

    def _on_browse_files(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "Select Addon Files", "",
            "Addon Files (*.mcaddon *.zip *.mcpack);;All Files (*.*)",
        )
        if files:
            self.files_dropped.emit([Path(f) for f in files])

    def _on_browse_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Folder Containing Packs")
        if folder:
            self.files_dropped.emit([Path(folder)])

    def set_enabled(self, enabled: bool):
        self.setEnabled(enabled)
        self._browse_files_btn.setEnabled(enabled)
        self._browse_folder_btn.setEnabled(enabled)


class AddonItemWidget(QFrame):
    remove_clicked = Signal(AddonItem)

    def __init__(self, addon: AddonItem, parent=None):
        super().__init__(parent)
        self.addon = addon
        self.setObjectName("addonItem")
        self.setStyleSheet(STYLE_ADDON_ITEM)
        self.setFrameShape(QFrame.NoFrame)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 3, 8, 3)
        layout.setSpacing(8)

        name_label = QLabel(addon.file_name)
        name_label.setStyleSheet("border: none; font-weight: 600; font-size: 13px;")
        layout.addWidget(name_label, 1)

        if addon.has_rp:
            rp_badge = QLabel("RP")
            rp_badge.setStyleSheet(STYLE_BADGE_ON)
            layout.addWidget(rp_badge)
        if addon.has_bp:
            bp_badge = QLabel("BP")
            bp_badge.setStyleSheet(STYLE_BADGE_ON)
            layout.addWidget(bp_badge)

        size_label = QLabel(format_bytes(addon.file_size))
        size_label.setStyleSheet("border: none; color: #999; font-size: 12px;")
        layout.addWidget(size_label)

        remove_btn = QPushButton("\u00d7")
        remove_btn.setFixedSize(24, 24)
        remove_btn.setToolTip("Remove this addon")
        remove_btn.setStyleSheet("font-size: 14px; font-weight: bold; padding: 0; border-radius: 12px;")
        remove_btn.clicked.connect(lambda: self.remove_clicked.emit(self.addon))
        layout.addWidget(remove_btn)


class ExportWorker(QObject):
    progress = Signal(int, str)
    addon_done = Signal(str, bool, str)
    finished = Signal()

    def __init__(
        self,
        addons: list[AddonItem],
        output_dir: Path,
        ffmpeg_path: Path | None = None,
        server_mode: bool = False,
        existing_rp: list[dict] | None = None,
        existing_bp: list[dict] | None = None,
    ):
        super().__init__()
        self.addons = addons
        self.output_dir = output_dir
        self.ffmpeg_path = ffmpeg_path
        self.server_mode = server_mode
        self.existing_rp = existing_rp or []
        self.existing_bp = existing_bp or []
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

                    process_addon(
                        addon, self.output_dir, on_progress, cancel_flag,
                        self.ffmpeg_path, self.server_mode,
                    )

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
                merged_rp = merge_pack_entries(self.existing_rp, resource_entries)
                merged_bp = merge_pack_entries(self.existing_bp, behavior_entries)
                if merged_rp:
                    (self.output_dir / "world_resource_packs.json").write_text(json.dumps(merged_rp, indent=2), "utf-8")
                if merged_bp:
                    (self.output_dir / "world_behavior_packs.json").write_text(json.dumps(merged_bp, indent=2), "utf-8")
                self.progress.emit(100, "Export complete!")
            else:
                self.progress.emit(0, "Cancelled")

            self.finished.emit()

        except Exception as e:
            self.progress.emit(0, f"Export error: {e}")
            self.finished.emit()


class JsonImportPanel(QFrame):
    entries_changed = Signal(str, list)  # kind ("rp"|"bp"), entries

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rp_entries: list[dict] = []
        self._bp_entries: list[dict] = []
        self._rp_path: str = ""
        self._bp_path: str = ""
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(4)

        title = QLabel("World JSONs (import existing to merge)")
        title.setStyleSheet(STYLE_SECTION_LABEL)
        layout.addWidget(title)

        rp_row = QHBoxLayout()
        rp_row.setSpacing(6)
        self._rp_label = QLabel("resource_packs: 0 entries")
        self._rp_label.setStyleSheet("font-size: 12px; border: none;")
        rp_row.addWidget(self._rp_label)
        rp_row.addStretch()
        self._rp_import_btn = QPushButton("Import")
        self._rp_import_btn.setStyleSheet(STYLE_BTN_SM)
        self._rp_import_btn.clicked.connect(lambda: self._import_file("rp"))
        rp_row.addWidget(self._rp_import_btn)
        self._rp_clear_btn = QPushButton("Clear")
        self._rp_clear_btn.setStyleSheet(STYLE_BTN_SM)
        self._rp_clear_btn.clicked.connect(lambda: self._clear("rp"))
        rp_row.addWidget(self._rp_clear_btn)
        self._rp_paste_btn = QPushButton("Paste")
        self._rp_paste_btn.setStyleSheet(STYLE_BTN_SM)
        self._rp_paste_btn.setCheckable(True)
        self._rp_paste_btn.clicked.connect(lambda: self._toggle_paste("rp"))
        rp_row.addWidget(self._rp_paste_btn)
        layout.addLayout(rp_row)

        bp_row = QHBoxLayout()
        bp_row.setSpacing(6)
        self._bp_label = QLabel("behavior_packs: 0 entries")
        self._bp_label.setStyleSheet("font-size: 12px; border: none;")
        bp_row.addWidget(self._bp_label)
        bp_row.addStretch()
        self._bp_import_btn = QPushButton("Import")
        self._bp_import_btn.setStyleSheet(STYLE_BTN_SM)
        self._bp_import_btn.clicked.connect(lambda: self._import_file("bp"))
        bp_row.addWidget(self._bp_import_btn)
        self._bp_clear_btn = QPushButton("Clear")
        self._bp_clear_btn.setStyleSheet(STYLE_BTN_SM)
        self._bp_clear_btn.clicked.connect(lambda: self._clear("bp"))
        bp_row.addWidget(self._bp_clear_btn)
        self._bp_paste_btn = QPushButton("Paste")
        self._bp_paste_btn.setStyleSheet(STYLE_BTN_SM)
        self._bp_paste_btn.setCheckable(True)
        self._bp_paste_btn.clicked.connect(lambda: self._toggle_paste("bp"))
        bp_row.addWidget(self._bp_paste_btn)
        layout.addLayout(bp_row)

        self._paste_area = QPlainTextEdit()
        self._paste_area.setPlaceholderText("Paste JSON array (e.g. [{\"pack_id\": \"...\", \"version\": [1, 0, 0]}])")
        self._paste_area.setMaximumHeight(80)
        self._paste_area.setVisible(False)
        layout.addWidget(self._paste_area)

        apply_row = QHBoxLayout()
        apply_row.setSpacing(6)
        apply_row.addStretch()
        self._apply_rp_btn = QPushButton("Apply to Resource Packs")
        self._apply_rp_btn.setStyleSheet(STYLE_BTN_SM)
        self._apply_rp_btn.clicked.connect(lambda: self._apply_paste("rp"))
        apply_row.addWidget(self._apply_rp_btn)
        self._apply_bp_btn = QPushButton("Apply to Behavior Packs")
        self._apply_bp_btn.setStyleSheet(STYLE_BTN_SM)
        self._apply_bp_btn.clicked.connect(lambda: self._apply_paste("bp"))
        apply_row.addWidget(self._apply_bp_btn)
        layout.addLayout(apply_row)

    def _toggle_paste(self, kind: str):
        if kind == "rp":
            visible = self._rp_paste_btn.isChecked()
            self._bp_paste_btn.setChecked(False)
        else:
            visible = self._bp_paste_btn.isChecked()
            self._rp_paste_btn.setChecked(False)
        self._paste_area.setVisible(visible)

    def _import_file(self, kind: str):
        path, _ = QFileDialog.getOpenFileName(self, "Select world_packs.json", "", "JSON Files (*.json);;All Files (*.*)")
        if not path:
            return
        try:
            data = json.loads(Path(path).read_text("utf-8"))
            if not isinstance(data, list):
                raise ValueError("JSON must be an array")
            for entry in data:
                if not isinstance(entry, dict) or "pack_id" not in entry:
                    raise ValueError("Each entry must have a 'pack_id' field")
            if kind == "rp":
                self._rp_entries = data
                self._rp_path = path
                self._update_label("rp")
            else:
                self._bp_entries = data
                self._bp_path = path
                self._update_label("bp")
            self.entries_changed.emit(kind, data)
        except Exception as e:
            QMessageBox.warning(self, "Import Error", f"Failed to import JSON:\n{e}")

    def _apply_paste(self, kind: str):
        text = self._paste_area.toPlainText().strip()
        if not text:
            return
        try:
            data = json.loads(text)
            if not isinstance(data, list):
                raise ValueError("JSON must be an array")
            for entry in data:
                if not isinstance(entry, dict) or "pack_id" not in entry:
                    raise ValueError("Each entry must have a 'pack_id' field")
            if kind == "rp":
                self._rp_entries = data
                self._rp_path = "(pasted)"
                self._update_label("rp")
            else:
                self._bp_entries = data
                self._bp_path = "(pasted)"
                self._update_label("bp")
            self.entries_changed.emit(kind, data)
            self._paste_area.setPlainText("")
        except Exception as e:
            QMessageBox.warning(self, "Parse Error", f"Failed to parse JSON:\n{e}")

    def _clear(self, kind: str):
        if kind == "rp":
            self._rp_entries = []
            self._rp_path = ""
            self._update_label("rp")
            self.entries_changed.emit("rp", [])
        else:
            self._bp_entries = []
            self._bp_path = ""
            self._update_label("bp")
            self.entries_changed.emit("bp", [])

    def _update_label(self, kind: str):
        if kind == "rp":
            n = len(self._rp_entries)
            src = f" ({Path(self._rp_path).name})" if self._rp_path else ""
            self._rp_label.setText(f"resource_packs: {n} entry{'s' if n != 1 else ''}{src}")
        else:
            n = len(self._bp_entries)
            src = f" ({Path(self._bp_path).name})" if self._bp_path else ""
            self._bp_label.setText(f"behavior_packs: {n} entry{'s' if n != 1 else ''}{src}")

    @property
    def rp_entries(self) -> list[dict]:
        return list(self._rp_entries)

    @property
    def bp_entries(self) -> list[dict]:
        return list(self._bp_entries)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"Bedrock Addon Manager \u2014 {VERSION}")
        self.setMinimumSize(780, 640)
        self.addons: list[AddonItem] = []
        self.output_dir: Path = Path.home() / "Desktop" / "BedrockOutput"
        self.theme_dark = True
        self.ffmpeg_path = get_ffmpeg_path()
        self.ffmpeg_ok = self.ffmpeg_path is not None
        self.server_mode = False
        self.existing_rp: list[dict] = []
        self.existing_bp: list[dict] = []
        self._export_thread: QThread | None = None

        self._setup_ui()
        self._connect_signals()
        self._update_export_button()

    def _setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setSpacing(8)
        root.setContentsMargins(12, 8, 12, 8)

        self._build_header(root)
        self._build_drop_zone(root)
        self._build_list_section(root)
        self._build_settings(root)
        self._build_export_section(root)
        self._build_status_section(root)

    def _build_header(self, root: QVBoxLayout):
        header = QHBoxLayout()
        header.setSpacing(8)

        title = QLabel("Bedrock Addon Manager")
        title.setStyleSheet("font-size: 18px; font-weight: 700; border: none;")
        header.addWidget(title)

        ver_label = QLabel(VERSION)
        ver_label.setStyleSheet("color: #888; font-size: 11px; border: none; padding-top: 4px;")
        header.addWidget(ver_label)

        header.addStretch()

        ffmpeg_label = QLabel("FFmpeg: OK" if self.ffmpeg_ok else "FFmpeg: not found")
        ffmpeg_label.setStyleSheet(
            f"color: {'#5bbf3a' if self.ffmpeg_ok else '#ffa500'}; font-size: 11px; border: none;"
        )
        header.addWidget(ffmpeg_label)

        self.theme_btn = QToolButton()
        self.theme_btn.setText("\u2601")
        self.theme_btn.setToolTip("Toggle dark/light theme")
        self.theme_btn.setFixedSize(32, 32)
        header.addWidget(self.theme_btn)

        root.addLayout(header)

    def _build_drop_zone(self, root: QVBoxLayout):
        self.drop_zone = DropZone()
        root.addWidget(self.drop_zone)

    def _build_list_section(self, root: QVBoxLayout):
        list_header = QHBoxLayout()
        list_header.setSpacing(8)

        self.list_title = QLabel("Addons")
        self.list_title.setStyleSheet("font-weight: 600; font-size: 13px; border: none;")
        list_header.addWidget(self.list_title)

        self.count_label = QLabel("0 addons")
        self.count_label.setStyleSheet("color: #999; font-size: 12px; border: none;")
        list_header.addWidget(self.count_label)

        list_header.addStretch()

        self.clear_btn = QPushButton("Clear All")
        self.clear_btn.setStyleSheet("padding: 3px 14px; font-size: 12px;")
        list_header.addWidget(self.clear_btn)

        root.addLayout(list_header)

        self.addon_list = QListWidget()
        self.addon_list.setMinimumHeight(120)
        self.addon_list.setAlternatingRowColors(True)
        self.addon_list.setSelectionMode(QListWidget.NoSelection)
        self.addon_list.setFrameShape(QFrame.StyledPanel)
        root.addWidget(self.addon_list, 1)

    def _build_settings(self, root: QVBoxLayout):
        settings_frame = QFrame()
        settings_frame.setFrameShape(QFrame.NoFrame)
        settings_layout = QVBoxLayout(settings_frame)
        settings_layout.setContentsMargins(0, 4, 0, 4)
        settings_layout.setSpacing(4)

        section = QLabel("Output")
        section.setStyleSheet(STYLE_SECTION_LABEL)
        settings_layout.addWidget(section)

        folder_row = QHBoxLayout()
        folder_row.setSpacing(6)
        folder_label_title = QLabel("Folder:")
        folder_label_title.setStyleSheet("font-size: 12px; border: none;")
        folder_row.addWidget(folder_label_title)

        self.folder_label = QLabel(str(self.output_dir))
        self.folder_label.setStyleSheet("color: #999; font-size: 12px; border: none;")
        self.folder_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        folder_row.addWidget(self.folder_label, 1)
        self.browse_btn = QPushButton("Browse")
        self.browse_btn.setStyleSheet(STYLE_BTN_SM)
        folder_row.addWidget(self.browse_btn)
        settings_layout.addLayout(folder_row)

        self.server_checkbox = QCheckBox("This is a server (output to resource_packs / behavior_packs folders)")
        self.server_checkbox.setStyleSheet("font-size: 12px; spacing: 6px;")
        settings_layout.addWidget(self.server_checkbox)

        self.json_panel = JsonImportPanel()
        settings_layout.addWidget(self.json_panel)

        root.addWidget(settings_frame)

    def _build_export_section(self, root: QVBoxLayout):
        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setFixedHeight(24)
        root.addWidget(self.progress_bar)

        self.export_btn = QPushButton("Export & Patch All")
        self.export_btn.setMinimumHeight(40)
        self.export_btn.setStyleSheet("font-weight: 700; font-size: 14px;")
        root.addWidget(self.export_btn)

    def _build_status_section(self, root: QVBoxLayout):
        self.status_label = QLabel("Ready")
        self.status_label.setStyleSheet("color: #999; font-size: 12px; border: none;")
        root.addWidget(self.status_label)

        self.log_output = QPlainTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setMaximumHeight(100)
        self.log_output.setVisible(False)
        root.addWidget(self.log_output)

    def _connect_signals(self):
        self.drop_zone.files_dropped.connect(self.on_files_dropped)
        self.clear_btn.clicked.connect(self.on_clear)
        self.browse_btn.clicked.connect(self.on_browse_output)
        self.server_checkbox.toggled.connect(self._on_server_toggled)
        self.json_panel.entries_changed.connect(self._on_json_entries_changed)
        self.export_btn.clicked.connect(self.on_export)
        self.theme_btn.clicked.connect(self.on_toggle_theme)

    def _on_server_toggled(self, checked: bool):
        self.server_mode = checked

    def _on_json_entries_changed(self, kind: str, entries: list[dict]):
        if kind == "rp":
            self.existing_rp = list(entries)
        else:
            self.existing_bp = list(entries)

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
        if not self.log_output.isVisible():
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
                    is_directory=path.is_dir(),
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
        self.drop_zone.set_enabled(False)
        self.progress_bar.setValue(0)
        self.log_output.clear()
        self.log_output.setVisible(True)
        self.log("Starting export...")

        self._export_thread = QThread(self)
        self._export_worker = ExportWorker(
            list(self.addons),
            self.output_dir,
            self.ffmpeg_path,
            self.server_mode,
            self.existing_rp,
            self.existing_bp,
        )
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
        self.drop_zone.set_enabled(True)
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
