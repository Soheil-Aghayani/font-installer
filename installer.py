import json
import sys
import html
import os
import platform
import datetime
from collections import deque
from pathlib import Path
from PyQt5.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)
from PyQt5.QtCore import Qt, QTimer, QThread, pyqtSignal, QPropertyAnimation, QPoint, QEasingCurve
from PyQt5.QtGui import QColor, QTextCursor, QFont, QFontDatabase
from font_manager import FONT_EXTENSIONS, FontManager, OperationResult

# --- CONFIGURATION ---
RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
ASSET_DIR = RESOURCE_DIR / "asset"
FONT_INDEX_FILE = FontManager.default_data_dir() / "font-index.sqlite"
FONT_METADATA_FILE = FontManager.default_data_dir() / "font-metadata.json"

# Packaged builds keep editable settings outside the temporary PyInstaller
# extraction directory. Development builds continue to use config.json next
# to the script for backward compatibility.
if getattr(sys, "frozen", False):
    if platform.system() == "Windows":
        config_root = Path(os.environ.get("APPDATA", str(Path.home() / "AppData/Roaming")))
    else:
        config_root = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
    CONFIG_FILE = config_root / "soheil-os" / "font-installer" / "config.json"
else:
    CONFIG_FILE = Path(__file__).resolve().parent / "config.json"

EXTENSIONS = FONT_EXTENSIONS


class FontMetadataCache:
    """Persist font family names while invalidating entries after file changes."""

    def __init__(self, path):
        self.path = Path(path)
        self.records = {}
        self.dirty = False
        self.load()

    def load(self):
        if not self.path.exists():
            return
        try:
            with self.path.open("r", encoding="utf-8") as metadata_file:
                records = json.load(metadata_file)
            if isinstance(records, dict):
                self.records = records
        except (OSError, json.JSONDecodeError):
            self.records = {}

    def get_family(self, font_path):
        resolved_path = Path(font_path).resolve()
        try:
            stat = resolved_path.stat()
        except OSError:
            return None

        record = self.records.get(str(resolved_path))
        if not isinstance(record, dict):
            return None
        if record.get("size") != stat.st_size or record.get("mtime_ns") != stat.st_mtime_ns:
            return None
        family = record.get("family")
        return family if isinstance(family, str) and family else None

    def set_family(self, font_path, family):
        if not family:
            return
        resolved_path = Path(font_path).resolve()
        try:
            stat = resolved_path.stat()
        except OSError:
            return
        self.records[str(resolved_path)] = {
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
            "family": str(family),
        }
        self.dirty = True

    def save(self):
        if not self.dirty:
            return
        temporary_file = self.path.with_name(f".{self.path.name}.tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with temporary_file.open("w", encoding="utf-8") as metadata_file:
                json.dump(self.records, metadata_file, indent=2, ensure_ascii=False)
            os.replace(temporary_file, self.path)
            self.dirty = False
        except (OSError, TypeError, ValueError):
            try:
                temporary_file.unlink(missing_ok=True)
            except OSError:
                pass

GLYPH_SAMPLE_TEXT = (
    "UPPERCASE\nABCDEFGHIJKLMNOPQRSTUVWXYZ\n\n"
    "lowercase\nabcdefghijklmnopqrstuvwxyz\n\n"
    "Accents\nÁ À Â Ä Ç É È Ê Ë Í Î Ï Ñ Ó Ö Ô Ú Ü Ý ß\n\n"
    "Numbers & symbols\n0123456789  ! ? @ # $ % & * ( ) [ ] { } < > + - = / \\ _ ~\n\n"
    "Persian / Arabic\nآ ا ب پ ت ث ج چ ح خ د ذ ر ز ژ س ش ص ض ط ظ ع غ ف ق ک گ ل م ن و ه ی\n"
    "۰۱۲۳۴۵۶۷۸۹"
)


def make_preview_font(family, size):
    font = QFont(family or "Segoe UI", size)
    font.setHintingPreference(QFont.PreferFullHinting)
    return font


def apply_preview_font(editor, family, size, border, accent="#58a6ff"):
    """Apply a font to a QTextEdit, including text already in its document."""
    preview_font = make_preview_font(family, size)
    editor.setFont(preview_font)
    editor.document().setDefaultFont(preview_font)
    editor.setObjectName("fontPreviewEditor")
    safe_family = str(family or "Segoe UI").replace("\\", "\\\\").replace('"', '\\"')
    editor.setStyleSheet(
        f"QTextEdit#fontPreviewEditor {{ background-color: #161b22; "
        f"border: 1px solid {border}; padding: 10px; border-radius: 5px; "
        f"font-family: \"{safe_family}\"; font-size: {size}pt; }}"
        f"QScrollBar:vertical {{ background: #0d1117; width: 14px; "
        f"margin: 3px 2px; border: 1px solid {border}; border-radius: 6px; }}"
        f"QScrollBar::handle:vertical {{ background: {accent}; min-height: 36px; "
        "margin: 2px; border-radius: 5px; }"
        f"QScrollBar::handle:vertical:hover {{ background: #ffffff; }}"
        f"QScrollBar::handle:vertical:pressed {{ background: {accent}; }}"
        "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical "
        "{ height: 0px; background: none; border: none; }"
        "QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical "
        "{ background: none; }"
        f"QScrollBar:horizontal {{ background: #0d1117; height: 12px; "
        f"margin: 2px 3px; border: 1px solid {border}; border-radius: 6px; }}"
        f"QScrollBar::handle:horizontal {{ background: {accent}; min-width: 36px; "
        "margin: 2px; border-radius: 5px; }"
        f"QScrollBar::handle:horizontal:hover {{ background: #ffffff; }}"
        f"QScrollBar::handle:horizontal:pressed {{ background: {accent}; }}"
        "QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal "
        "{ width: 0px; background: none; border: none; }"
        "QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal "
        "{ background: none; }"
    )


def fit_dialog_to_screen(dialog, preferred_width, preferred_height):
    """Keep a dialog usable on smaller displays while respecting its layout."""
    screen = dialog.screen() or QApplication.primaryScreen()
    if screen is None:
        return
    available = screen.availableGeometry()
    minimum_width = dialog.minimumSizeHint().width()
    minimum_height = dialog.minimumSizeHint().height()
    available_width = max(1, available.width() - 24)
    available_height = max(1, available.height() - 48)
    dialog.resize(
        max(minimum_width, min(preferred_width, available_width)),
        max(minimum_height, min(preferred_height, available_height)),
    )


class FontPreviewDialog(QDialog):
    def __init__(self, font_path, parent=None):
        super().__init__(parent)
        self.host = parent
        self.font_path = font_path
        self.setWindowTitle(f"Font Preview - {font_path.name}")
        self.resize(900, 760)
        
        # Theme integration
        bg = parent.themes[parent.current_theme]["bg"] if parent else "#0d1117"
        fg = parent.themes[parent.current_theme]["fg"] if parent else "#c9d1d9"
        border = parent.themes[parent.current_theme]["border"] if parent else "#30363d"
        self.preview_accent = parent.themes[parent.current_theme].get("input", "#58a6ff") if parent else "#58a6ff"
        self.preview_border = border
        self.setStyleSheet(f"background-color: {bg}; color: {fg}; border: 1px solid {border};")
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)
        
        # Load font using an absolute path for reliability.
        abs_path = str(font_path.absolute())
        font_id = QFontDatabase.addApplicationFont(abs_path)
        self.font_id = font_id
        
        families = []
        if font_id != -1:
            families = QFontDatabase.applicationFontFamilies(font_id)
            if families:
                family = families[0]
            else:
                family = "Unknown (No families found)"
        else:
            family = "Error: Failed to load font file"
        self.family = family if font_id != -1 and families else "Segoe UI"
        self.default_preview_size = 32
        self.default_preview_per = getattr(parent, "preview_text_per", "آ ب پ ت ث ج چ ح خ")
        self.default_preview_eng = getattr(
            parent,
            "preview_text_eng",
            "The quick brown fox jumps over the lazy dog.",
        )

        # Header Info
        header_layout = QHBoxLayout()
        info_label = QLabel(
            f"Font Family: <span style='color: #58a6ff;'>{html.escape(family)}</span>"
            f"<br><small style='color: #8b949e;'>{html.escape(font_path.name)} · "
            f"{html.escape(font_path.suffix.upper()[1:])}</small>"
        )
        info_label.setTextFormat(Qt.RichText)
        info_label.setStyleSheet("font-size: 16px; font-family: 'Segoe UI'; border: none;")
        header_layout.addWidget(info_label)
        layout.addLayout(header_layout)

        controls = QHBoxLayout()
        controls.addWidget(QLabel("Preview size:"))
        self.size_spin = QSpinBox()
        self.size_spin.setRange(10, 96)
        self.size_spin.setValue(self.default_preview_size)
        self.size_spin.setSuffix(" pt")
        self.size_spin.setToolTip("Change the live preview size")
        controls.addWidget(self.size_spin)
        controls.addStretch(1)

        self.reset_button = QPushButton("Reset Samples")
        self.save_defaults_button = QPushButton("Save as Defaults")
        controls.addWidget(self.reset_button)
        controls.addWidget(self.save_defaults_button)
        layout.addLayout(controls)

        samples_scroll = QScrollArea()
        samples_scroll.setWidgetResizable(True)
        samples_scroll.setMinimumHeight(0)
        samples_scroll.setFrameShape(QFrame.NoFrame)
        samples_container = QWidget()
        samples_container.setStyleSheet(f"background-color: {bg};")
        samples_layout = QVBoxLayout(samples_container)
        samples_layout.setContentsMargins(0, 0, 0, 0)
        samples_layout.setSpacing(15)
        samples_scroll.setWidget(samples_container)
        layout.addWidget(samples_scroll, 1)

        self.english_sample = self.create_sample_editor(
            samples_layout,
            "English Sample (editable):",
            self.default_preview_eng,
            Qt.LeftToRight,
            Qt.AlignLeft,
            border,
        )
        self.persian_sample = self.create_sample_editor(
            samples_layout,
            "Persian / Arabic Sample (editable):",
            self.default_preview_per,
            Qt.RightToLeft,
            Qt.AlignRight,
            border,
        )
        self.glyph_sample = self.create_sample_editor(
            samples_layout,
            "Alphabet, accents, symbols, and Arabic glyph sheet:",
            GLYPH_SAMPLE_TEXT,
            Qt.LeftToRight,
            Qt.AlignLeft,
            border,
        )
        # Reapply after the themed dialog has propagated its stylesheet to the editors.
        self.update_preview_size(self.default_preview_size)

        self.status_label = QLabel("Edit the samples above to test your own text.")
        self.status_label.setStyleSheet("color: #8b949e; font-size: 11px; border: none;")
        layout.addWidget(self.status_label)

        close_btn = QLineEdit(f"Font ID {font_id} · Press ESC to return")
        close_btn.setReadOnly(True)
        close_btn.setAlignment(Qt.AlignCenter)
        close_btn.setStyleSheet("background: transparent; color: #8b949e; border: none; font-size: 11px;")
        layout.addWidget(close_btn)

        self.size_spin.valueChanged.connect(self.update_preview_size)
        self.reset_button.clicked.connect(self.reset_samples)
        self.save_defaults_button.clicked.connect(self.save_as_defaults)
        fit_dialog_to_screen(self, 900, 760)

    def create_sample_editor(self, layout, title, text, direction, alignment, border):
        label = QLabel(title)
        label.setStyleSheet("color: #e3b341; font-weight: bold; font-size: 14px; border: none;")
        layout.addWidget(label)

        editor = QTextEdit()
        editor.setAcceptRichText(False)
        editor.setLayoutDirection(direction)
        editor.setAlignment(alignment)
        editor.setPlainText(text)
        apply_preview_font(
            editor,
            self.family,
            self.default_preview_size,
            border,
            self.preview_accent,
        )
        editor.setMinimumHeight(105)
        layout.addWidget(editor, 1)
        return editor

    def update_preview_size(self, size):
        for editor in (self.english_sample, self.persian_sample, self.glyph_sample):
            apply_preview_font(
                editor,
                self.family,
                size,
                self.preview_border,
                self.preview_accent,
            )

    def reset_samples(self):
        self.english_sample.setPlainText(self.default_preview_eng)
        self.persian_sample.setPlainText(self.default_preview_per)
        self.glyph_sample.setPlainText(GLYPH_SAMPLE_TEXT)
        self.size_spin.setValue(self.default_preview_size)
        self.status_label.setText("Samples reset.")

    def save_as_defaults(self):
        if not self.host:
            return
        self.host.preview_text_eng = self.english_sample.toPlainText()
        self.host.preview_text_per = self.persian_sample.toPlainText()
        self.host.save_config()
        self.status_label.setText("English and Persian samples saved as application defaults.")

    def closeEvent(self, event):
        if self.font_id != -1:
            QFontDatabase.removeApplicationFont(self.font_id)
            self.font_id = -1
        super().closeEvent(event)

class FontCompareDialog(QDialog):
    def __init__(self, font1_path, font2_path, parent=None):
        super().__init__(parent)
        self.host = parent
        self.setWindowTitle(f"Comparison: {font1_path.name} vs {font2_path.name}")
        self.resize(1120, 760)
        
        bg = parent.themes[parent.current_theme]["bg"] if parent else "#0d1117"
        fg = parent.themes[parent.current_theme]["fg"] if parent else "#c9d1d9"
        border = parent.themes[parent.current_theme]["border"] if parent else "#30363d"
        self.preview_accent = parent.themes[parent.current_theme].get("input", "#58a6ff") if parent else "#58a6ff"
        self.preview_border = border
        self.setStyleSheet(f"background-color: {bg}; color: {fg}; border: 1px solid {border};")
        
        layout = QVBoxLayout(self)
        self.font_ids = []
        self.preview_views = []
        self.default_preview_eng = getattr(
            parent,
            "preview_text_eng",
            "The quick brown fox jumps over the lazy dog.",
        )
        self.default_preview_per = getattr(
            parent,
            "preview_text_per",
            "آ ب پ ت ث ج چ ح خ",
        )
        self.default_comparison_text = f"{self.default_preview_eng}\n\n{self.default_preview_per}"
        
        # Comparison Header
        header = QLabel(
            f"Comparing: <span style='color: #58a6ff;'>{html.escape(font1_path.name)}</span> "
            f"(Left) vs <span style='color: #ff7b72;'>{html.escape(font2_path.name)}</span> (Right)"
        )
        header.setTextFormat(Qt.RichText)
        header.setAlignment(Qt.AlignCenter)
        header.setStyleSheet("font-size: 18px; margin-bottom: 10px; border: none;")
        layout.addWidget(header)

        controls = QHBoxLayout()
        controls.addWidget(QLabel("Preview size:"))
        self.size_spin = QSpinBox()
        self.size_spin.setRange(10, 96)
        self.size_spin.setValue(28)
        self.size_spin.setSuffix(" pt")
        self.size_spin.setToolTip("Change the live comparison size")
        controls.addWidget(self.size_spin)

        controls.addWidget(QLabel("Sample:"))
        self.preset_combo = QComboBox()
        self.preset_combo.addItem("English + Persian", self.default_comparison_text)
        self.preset_combo.addItem("English", self.default_preview_eng)
        self.preset_combo.addItem("Persian / Arabic", self.default_preview_per)
        self.preset_combo.addItem("Alphabet & symbols", GLYPH_SAMPLE_TEXT)
        controls.addWidget(self.preset_combo, 1)

        self.reset_button = QPushButton("Reset")
        controls.addWidget(self.reset_button)
        layout.addLayout(controls)

        sample_label = QLabel("Comparison text (editable; both fonts update together):")
        sample_label.setStyleSheet("color: #e3b341; font-weight: bold; border: none;")
        layout.addWidget(sample_label)

        self.sample_editor = QTextEdit()
        self.sample_editor.setAcceptRichText(False)
        self.sample_editor.setLayoutDirection(Qt.LeftToRight)
        self.sample_editor.setPlainText(self.default_comparison_text)
        self.sample_editor.setMinimumHeight(105)
        self.sample_editor.setStyleSheet(
            f"background-color: #161b22; border: 1px solid {border}; "
            "padding: 10px; border-radius: 5px;"
        )
        layout.addWidget(self.sample_editor)

        grid_layout = QHBoxLayout()

        def create_pane(font_path, side_color):
            pane = QWidget()
            pane_layout = QVBoxLayout(pane)

            fid = QFontDatabase.addApplicationFont(str(font_path.absolute()))
            if fid != -1:
                self.font_ids.append(fid)
            families = QFontDatabase.applicationFontFamilies(fid) if fid != -1 else []
            family = families[0] if families else "Segoe UI"

            label = QLabel(
                f"{html.escape(family)}<br><small>{html.escape(font_path.name)}</small>"
            )
            label.setTextFormat(Qt.RichText)
            label.setStyleSheet(f"color: {side_color}; font-weight: bold; font-size: 16px; border: none;")
            pane_layout.addWidget(label)

            txt = QTextEdit()
            txt.setReadOnly(True)
            txt.setAcceptRichText(False)
            txt.setLayoutDirection(Qt.LeftToRight)
            txt.setPlainText(self.default_comparison_text)
            apply_preview_font(
                txt,
                family,
                self.size_spin.value(),
                border,
                self.preview_accent,
            )
            pane_layout.addWidget(txt, 1)
            self.preview_views.append((txt, family))
            return pane

        grid_layout.addWidget(create_pane(font1_path, "#58a6ff"))
        grid_layout.addWidget(create_pane(font2_path, "#ff7b72"))
        
        layout.addLayout(grid_layout)
        # Reapply after the panes inherit the dialog stylesheet.
        self.update_preview_size(self.size_spin.value())

        self.status_label = QLabel(
            "Edit the sample or choose a preset to compare the same text in both fonts."
        )
        self.status_label.setStyleSheet("color: #8b949e; font-size: 11px; border: none;")
        layout.addWidget(self.status_label)

        self.size_spin.valueChanged.connect(self.update_preview_size)
        self.preset_combo.currentIndexChanged.connect(self.apply_preset)
        self.sample_editor.textChanged.connect(self.update_comparison_text)
        self.reset_button.clicked.connect(self.reset_comparison)
        fit_dialog_to_screen(self, 1120, 760)

    def apply_preset(self, index):
        preset_text = self.preset_combo.itemData(index)
        if preset_text is None:
            return
        self.sample_editor.setPlainText(preset_text)
        self.status_label.setText(f"Preset loaded: {self.preset_combo.itemText(index)}")

    def update_comparison_text(self):
        text = self.sample_editor.toPlainText()
        for preview, _family in self.preview_views:
            preview.setPlainText(text)

    def update_preview_size(self, size):
        for preview, family in self.preview_views:
            apply_preview_font(
                preview,
                family,
                size,
                self.preview_border,
                self.preview_accent,
            )

    def reset_comparison(self):
        self.size_spin.setValue(28)
        self.preset_combo.setCurrentIndex(0)
        self.sample_editor.setPlainText(self.default_comparison_text)
        self.status_label.setText("Comparison reset.")

    def closeEvent(self, event):
        for font_id in self.font_ids:
            QFontDatabase.removeApplicationFont(font_id)
        self.font_ids.clear()
        self.preview_views.clear()
        super().closeEvent(event)


class CatalogIndexWorker(QThread):
    """Build the asset index without blocking the catalog window."""

    records_ready = pyqtSignal(object)
    error_signal = pyqtSignal(str)

    def __init__(self, asset_dir, cache_path, parent=None):
        super().__init__(parent)
        self.asset_dir = Path(asset_dir)
        self.cache_path = Path(cache_path)

    def run(self):
        try:
            records = FontManager.indexed_fonts(self.asset_dir, self.cache_path)
        except Exception as error:  # pragma: no cover - defensive worker boundary
            self.error_signal.emit(str(error))
            return
        self.records_ready.emit(records)


class FontCatalogDialog(QDialog):
    """Searchable visual catalog for the font library."""

    def __init__(self, font_paths, parent=None, asset_records=None, initial_query=""):
        super().__init__(parent)
        self.host = parent
        self.font_paths = list(font_paths)
        self.asset_records = list(asset_records or [])
        self.initial_query = initial_query
        self.index_worker = None
        self.catalog_loading = False
        self._closing = False
        self.asset_digests = {
            Path(record.path).resolve(): record.digest
            for record in self.asset_records
        }
        self.font_ids = {}
        self.font_families = {}
        self.family_labels = {}
        self.metadata_cache = FontMetadataCache(FONT_METADATA_FILE)
        self.metadata_queue = []
        self.metadata_total = 0
        self.metadata_loaded = 0
        self.metadata_rebuild_needed = False
        self.metadata_timer = QTimer(self)
        self.metadata_timer.setInterval(10)
        self.metadata_timer.timeout.connect(self.process_metadata_queue)
        self.card_render_queue = []
        self.card_render_index = 0
        self.card_render_row = 0
        self.card_render_column = 0
        self.card_render_group = None
        self.card_render_group_mode = "none"
        self.card_render_timer = QTimer(self)
        self.card_render_timer.setInterval(0)
        self.card_render_timer.timeout.connect(self.render_card_batch)
        self.status_cache = {}
        self.status_labels = {}
        self.status_queue = []
        self.status_ready = False
        self.status_timer = QTimer(self)
        self.status_timer.setInterval(10)
        self.status_timer.timeout.connect(self.process_status_queue)
        self.operation_completed_count = 0
        self.selected_paths = set()
        self.unique_font_count = 0
        self.duplicate_extra_count = 0
        self.visible_paths = []
        self.preview_labels = {}
        self.empty_state_label = None
        self.duplicate_groups = {}
        self.duplicate_paths = set()

        self.setWindowTitle("Font Catalog")
        self.resize(1200, 820)

        theme = parent.themes[parent.current_theme] if parent else {
            "bg": "#0d1117",
            "fg": "#c9d1d9",
            "input": "#58a6ff",
            "border": "#30363d",
        }
        self.setStyleSheet(f"""
            QDialog {{ background-color: {theme['bg']}; color: {theme['fg']}; }}
            QLineEdit, QComboBox, QSpinBox {{
                background-color: #161b22;
                color: {theme['fg']};
                border: 1px solid {theme['border']};
                border-radius: 4px;
                padding: 6px;
            }}
            QPushButton {{
                background-color: #21262d;
                color: {theme['fg']};
                border: 1px solid {theme['border']};
                border-radius: 4px;
                padding: 7px 12px;
            }}
            QPushButton:hover {{ background-color: {theme['border']}; }}
            QPushButton:disabled {{ color: #484f58; }}
            QFrame#fontCard {{
                background-color: #161b22;
                border: 1px solid {theme['border']};
                border-radius: 7px;
            }}
            QScrollArea {{ border: none; }}
        """)

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(14, 14, 14, 14)
        root_layout.setSpacing(10)

        toolbar = QGridLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Search family or filename...")

        self.status_filter = QComboBox()
        self.status_filter.addItem("All statuses", "all")
        self.status_filter.addItem("Installed", "installed")
        self.status_filter.addItem("Missing", "missing")
        self.status_filter.addItem("Conflicts", "conflict")
        self.status_filter.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.status_filter.setMinimumWidth(125)
        self.status_filter.setMaximumWidth(170)

        self.format_filter = QComboBox()
        self.format_filter.addItem("All formats", "all")
        self.format_filter.addItem("TrueType (.ttf)", ".ttf")
        self.format_filter.addItem("OpenType (.otf)", ".otf")
        self.format_filter.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.format_filter.setMinimumWidth(145)
        self.format_filter.setMaximumWidth(170)

        self.duplicates_only = QCheckBox("Duplicates only")
        self.duplicates_only.setToolTip("Show only byte-identical asset copies")

        self.group_filter = QComboBox()
        self.group_filter.addItem("No grouping", "none")
        self.group_filter.addItem("Group by family", "family")
        self.group_filter.addItem("Group by folder", "folder")
        self.group_filter.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.group_filter.setMinimumWidth(145)
        self.group_filter.setMaximumWidth(180)
        self.group_filter.setToolTip("Organize visible cards by family or source folder")

        size_label = QLabel("Preview:")
        self.preview_size = QSpinBox()
        self.preview_size.setRange(12, 56)
        self.preview_size.setValue(26)
        self.preview_size.setSuffix(" pt")
        self.preview_size.setToolTip("Change the preview text size")
        toolbar.addWidget(self.search_edit, 0, 0, 1, 4)
        toolbar.addWidget(self.status_filter, 1, 0)
        toolbar.addWidget(self.format_filter, 1, 1)
        toolbar.addWidget(self.group_filter, 1, 2)
        toolbar.addWidget(self.duplicates_only, 1, 3)
        toolbar.addWidget(size_label, 2, 0)
        toolbar.addWidget(self.preview_size, 2, 1)
        toolbar.setColumnStretch(0, 1)
        toolbar.setColumnStretch(1, 1)
        toolbar.setColumnStretch(2, 1)
        toolbar.setColumnStretch(3, 1)
        root_layout.addLayout(toolbar)

        self.count_label = QLabel()
        self.count_label.setWordWrap(True)
        self.count_label.setMinimumWidth(0)
        self.count_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.count_label.setStyleSheet("color: #8b949e; padding: 2px 0;")
        root_layout.addWidget(self.count_label)

        self.metadata_status = QLabel()
        self.metadata_status.setWordWrap(True)
        self.metadata_status.setMinimumWidth(0)
        self.metadata_status.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.metadata_status.setStyleSheet("color: #8b949e; font-size: 11px; padding: 0 0 2px;")
        root_layout.addWidget(self.metadata_status)

        self.status_status = QLabel()
        self.status_status.setWordWrap(True)
        self.status_status.setMinimumWidth(0)
        self.status_status.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.status_status.setStyleSheet("color: #8b949e; font-size: 11px; padding: 0 0 2px;")
        root_layout.addWidget(self.status_status)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.catalog_canvas = QWidget()
        self.catalog_canvas.setStyleSheet(f"background-color: {theme['bg']};")
        self.catalog_grid = QGridLayout(self.catalog_canvas)
        self.catalog_grid.setContentsMargins(4, 4, 4, 4)
        self.catalog_grid.setHorizontalSpacing(10)
        self.catalog_grid.setVerticalSpacing(10)
        self.scroll_area.setWidget(self.catalog_canvas)
        root_layout.addWidget(self.scroll_area, 1)

        footer = QVBoxLayout()
        scope_row = QHBoxLayout()
        scope_row.addWidget(QLabel("Install scope:"))
        self.scope_combo = QComboBox()
        self.scope_combo.addItem("User", "user")
        self.scope_combo.addItem("System", "system")
        current_scope = parent.install_scope if parent else "user"
        self.scope_combo.setCurrentIndex(0 if current_scope == "user" else 1)
        self.scope_combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.scope_combo.setMinimumWidth(80)
        self.scope_combo.setMaximumWidth(140)
        scope_row.addWidget(self.scope_combo)
        scope_row.addStretch(1)

        self.operation_label = QLabel()
        self.operation_label.setWordWrap(True)
        self.operation_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.operation_label.setStyleSheet("color: #e3b341;")
        scope_row.addWidget(self.operation_label, 1)
        footer.addLayout(scope_row)

        self.select_visible_button = QPushButton("Select Visible")
        self.clear_selection_button = QPushButton("Clear Selection")
        self.clear_hidden_button = QPushButton("Clear Hidden")
        self.refresh_button = QPushButton("Refresh")
        self.install_button = QPushButton("Install Selected")
        self.uninstall_button = QPushButton("Uninstall Selected")
        self.duplicate_button = QPushButton("Manage Duplicates")
        self.undo_button = QPushButton("Undo Last")
        self.cancel_button = QPushButton("Cancel Operation")
        self.cancel_button.setEnabled(False)
        self.close_button = QPushButton("Close")
        action_grid = QGridLayout()
        for index, button in enumerate((
            self.select_visible_button,
            self.clear_selection_button,
            self.clear_hidden_button,
            self.refresh_button,
            self.install_button,
            self.uninstall_button,
            self.duplicate_button,
            self.undo_button,
            self.cancel_button,
            self.close_button,
        )):
            action_grid.addWidget(button, index // 2, index % 2)
        footer.addLayout(action_grid)
        root_layout.addLayout(footer)

        self.search_edit.textChanged.connect(self.rebuild_cards)
        self.status_filter.currentIndexChanged.connect(self.rebuild_cards)
        self.format_filter.currentIndexChanged.connect(self.rebuild_cards)
        self.duplicates_only.stateChanged.connect(self.rebuild_cards)
        self.group_filter.currentIndexChanged.connect(self.rebuild_cards)
        self.preview_size.valueChanged.connect(self.update_preview_size)
        self.select_visible_button.clicked.connect(self.select_visible)
        self.clear_selection_button.clicked.connect(self.clear_selection)
        self.clear_hidden_button.clicked.connect(self.clear_hidden_selection)
        self.refresh_button.clicked.connect(self.refresh_catalog)
        self.install_button.clicked.connect(self.install_selected)
        self.uninstall_button.clicked.connect(self.uninstall_selected)
        self.duplicate_button.clicked.connect(self.manage_duplicates)
        self.undo_button.clicked.connect(self.undo_last)
        self.cancel_button.clicked.connect(self.cancel_operation)
        self.close_button.clicked.connect(self.close)

        if self.font_paths:
            self.load_duplicate_groups()
            self.rebuild_cards()
        else:
            self.count_label.setText("Waiting for the asset index...")
            self.metadata_status.setText("Preparing catalog...")
        fit_dialog_to_screen(self, 1200, 820)

    def family_for(self, font_path):
        if font_path in self.font_families:
            return self.font_families[font_path]

        family = self.metadata_cache.get_family(font_path) or font_path.stem
        self.font_families[font_path] = family
        return family

    def set_catalog_controls_enabled(self, enabled):
        for widget in (
            self.search_edit,
            self.status_filter,
            self.format_filter,
            self.duplicates_only,
            self.group_filter,
            self.preview_size,
            self.scope_combo,
            self.select_visible_button,
            self.clear_selection_button,
            self.clear_hidden_button,
            self.refresh_button,
            self.install_button,
            self.uninstall_button,
            self.duplicate_button,
            self.undo_button,
        ):
            widget.setEnabled(enabled)
        self.close_button.setEnabled(True)

    def reset_catalog_data(self):
        self.card_render_timer.stop()
        self.card_render_queue.clear()
        self.metadata_timer.stop()
        self.metadata_queue.clear()
        self.status_timer.stop()
        self.status_queue.clear()
        for font_id in self.font_ids.values():
            QFontDatabase.removeApplicationFont(font_id)
        self.font_paths = []
        self.asset_records = []
        self.asset_digests.clear()
        self.font_ids.clear()
        self.font_families.clear()
        self.family_labels.clear()
        self.preview_labels.clear()
        self.empty_state_label = None
        self.status_cache.clear()
        self.status_labels.clear()
        self.status_ready = False
        self.visible_paths = []
        self.duplicate_groups = {}
        self.duplicate_paths = set()
        self.unique_font_count = 0
        self.duplicate_extra_count = 0
        while self.catalog_grid.count():
            item = self.catalog_grid.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        self.update_count_label()

    def start_indexing(self):
        if self.index_worker and self.index_worker.isRunning():
            return
        self.reset_catalog_data()
        self.catalog_loading = True
        self.set_catalog_controls_enabled(False)
        self.count_label.setText("Indexing the asset library...")
        self.metadata_status.setText("Reading font files and building the index...")
        self.status_status.clear()
        self.operation_label.setText("Loading catalog data...")
        worker = CatalogIndexWorker(ASSET_DIR, FONT_INDEX_FILE, self)
        self.index_worker = worker
        worker.records_ready.connect(self.load_indexed_records)
        worker.error_signal.connect(self.handle_index_error)
        worker.finished.connect(
            lambda finished_worker=worker: self.indexing_finished(finished_worker)
        )
        worker.start()

    def load_indexed_records(self, records):
        if self._closing:
            return
        self.asset_records = list(records or [])
        self.font_paths = [record.path for record in self.asset_records]
        self.asset_digests = {
            Path(record.path).resolve(): record.digest
            for record in self.asset_records
        }
        self.catalog_loading = False
        self.set_catalog_controls_enabled(True)
        self.operation_label.clear()
        self.selected_paths.intersection_update(self.font_paths)
        if not self.font_paths:
            self.load_duplicate_groups()
            self.update_count_label()
            self.metadata_status.setText("No asset fonts found.")
            self.status_status.clear()
            return
        self.load_duplicate_groups()
        if self.initial_query and not self.search_edit.text():
            self.search_edit.setText(self.initial_query)
        self.initial_query = ""
        self.rebuild_cards()

    def handle_index_error(self, message):
        if self._closing:
            return
        self.catalog_loading = False
        self.set_catalog_controls_enabled(False)
        self.refresh_button.setEnabled(True)
        self.metadata_status.setText(f"Catalog indexing failed: {message}")
        self.count_label.setText("Catalog unavailable.")
        self.operation_label.setText("Use Refresh to try indexing again.")

    def indexing_finished(self, worker):
        if self.index_worker is worker:
            self.index_worker = None
        worker.deleteLater()

    def schedule_metadata_loading(self):
        self.metadata_timer.stop()
        self.metadata_queue = [
            font_path for font_path in self.font_paths
            if font_path not in self.font_ids
        ]
        self.metadata_total = len(self.metadata_queue)
        self.metadata_loaded = 0
        self.metadata_rebuild_needed = (
            bool(self.metadata_queue)
            and (
                self.group_filter.currentData() == "family"
                or bool(self.search_edit.text().strip())
            )
        )
        if self.metadata_queue:
            self.metadata_status.setText(
                f"Loading font metadata... 0/{self.metadata_total}"
            )
            self.metadata_timer.start()
        else:
            self.metadata_status.clear()

    def process_metadata_queue(self):
        """Resolve a small batch of font families without blocking the catalog."""
        batch_size = 4
        for _ in range(batch_size):
            if not self.metadata_queue:
                break
            font_path = self.metadata_queue.pop(0)
            cached_family = self.metadata_cache.get_family(font_path)
            font_id = QFontDatabase.addApplicationFont(str(font_path.absolute()))
            families = QFontDatabase.applicationFontFamilies(font_id) if font_id != -1 else []
            family = families[0] if families else (cached_family or font_path.stem)
            if font_id != -1:
                self.font_ids[font_path] = font_id
            if family != self.font_families.get(font_path):
                self.metadata_rebuild_needed = self.metadata_rebuild_needed or (
                    self.group_filter.currentData() == "family"
                )
            self.font_families[font_path] = family
            if families and family != cached_family:
                self.metadata_cache.set_family(font_path, family)
            self.update_card_metadata(font_path, family)
            self.metadata_loaded += 1

        if self.metadata_queue:
            self.metadata_status.setText(
                f"Loading font metadata... {self.metadata_loaded}/{self.metadata_total}"
            )
            return

        self.metadata_timer.stop()
        self.metadata_cache.save()
        if self.metadata_rebuild_needed:
            self.metadata_rebuild_needed = False
            self.rebuild_cards()
        else:
            self.metadata_status.setText("Font metadata ready.")

    def update_card_metadata(self, font_path, family):
        family_label = self.family_labels.get(font_path)
        if family_label is not None:
            family_label.setText(family)
        preview_data = self.preview_labels.get(font_path)
        if preview_data is not None:
            preview, _old_family = preview_data
            preview.setFont(make_preview_font(family, self.preview_size.value()))
            self.preview_labels[font_path] = (preview, family)

    def load_duplicate_groups(self):
        if not self.font_paths:
            self.duplicate_groups = {}
        elif self.asset_records:
            groups = {}
            for record in self.asset_records:
                groups.setdefault(record.digest, []).append(record.path)
            self.duplicate_groups = {
                digest: paths for digest, paths in groups.items() if len(paths) > 1
            }
        else:
            self.duplicate_groups = FontManager.duplicate_groups(ASSET_DIR, FONT_INDEX_FILE)
        self.duplicate_paths = {
            path for paths in self.duplicate_groups.values() for path in paths
        }
        self.duplicate_extra_count = sum(
            max(0, len(paths) - 1) for paths in self.duplicate_groups.values()
        )
        self.unique_font_count = max(0, len(self.font_paths) - self.duplicate_extra_count)

    def status_for(self, font_path):
        if font_path in self.status_cache:
            return self.status_cache[font_path]
        if not self.status_ready:
            return "loading", "Checking installed status..."

        return self.calculate_status(font_path)

    def calculate_status(self, font_path):
        """Calculate and cache installed/conflict status for one asset font."""

        locations = self.host.font_manager.installed_paths(font_path) if self.host else {}
        exact = []
        conflicts = []
        digest_func = self.host.font_manager.file_digest if self.host else FontManager.sha256
        try:
            source_digest = self.asset_digests.get(Path(font_path).resolve())
            if source_digest is None:
                source_digest = digest_func(font_path)
        except OSError:
            source_digest = None

        if source_digest is not None:
            for scope, installed_path in locations.items():
                try:
                    if digest_func(installed_path) == source_digest:
                        exact.append(scope)
                    else:
                        conflicts.append(scope)
                except OSError:
                    conflicts.append(scope)

        if conflicts:
            status = ("conflict", f"Conflict: {', '.join(conflicts)}")
        elif exact:
            status = ("installed", f"Installed: {', '.join(exact)}")
        else:
            status = ("missing", "Not installed")
        if font_path in self.duplicate_paths:
            status = (status[0], f"{status[1]} · Duplicate copy")
        self.status_cache[font_path] = status
        return status

    @staticmethod
    def status_color(status_key):
        return {
            "installed": "#3fb950",
            "conflict": "#e3b341",
            "missing": "#ff7b72",
            "loading": "#8b949e",
        }.get(status_key, "#8b949e")

    def schedule_status_loading(self):
        self.status_timer.stop()
        self.status_queue = [
            font_path for font_path in self.font_paths
            if font_path not in self.status_cache
        ]
        if self.status_ready and not self.status_queue:
            self.status_status.clear()
            return
        self.status_ready = False
        self.status_status.setText(
            f"Checking installed status... 0/{len(self.status_queue)}"
        )
        if self.status_queue:
            self.status_timer.start()

    def process_status_queue(self):
        """Resolve installed status in small batches after the catalog is visible."""
        batch_size = 16
        for _ in range(batch_size):
            if not self.status_queue:
                break
            font_path = self.status_queue.pop(0)
            self.calculate_status(font_path)
            self.update_card_status(font_path)

        if self.status_queue:
            completed = len(self.font_paths) - len(self.status_queue)
            self.status_status.setText(
                f"Checking installed status... {completed}/{len(self.font_paths)}"
            )
            return

        self.status_timer.stop()
        self.status_ready = True
        self.status_status.setText("Installed status ready.")
        if self.status_filter.currentData() != "all":
            self.rebuild_cards()

    def update_card_status(self, font_path):
        status_data = self.status_cache.get(font_path)
        status_label = self.status_labels.get(font_path)
        if status_data is None or status_label is None:
            return
        status_key, status_text = status_data
        status_label.setText(status_text)
        status_label.setStyleSheet(f"color: {self.status_color(status_key)}; font-size: 11px;")

    def sample_text(self):
        english = getattr(self.host, "preview_text_eng", "The quick brown fox jumps over the lazy dog.")
        persian = getattr(self.host, "preview_text_per", "آ ب پ ت ث ج چ ح خ")
        english_line = english.splitlines()[0][:42] if english else "Aa Bb Cc 0123456789"
        persian_line = persian.splitlines()[0][:34] if persian else "آ ب پ ت ث ج چ ح خ"
        return f"{english_line}\n{persian_line}"

    def rebuild_cards(self):
        query = self.search_edit.text().casefold().strip()
        status_filter = self.status_filter.currentData()
        format_filter = self.format_filter.currentData()
        group_mode = self.group_filter.currentData()
        self.metadata_timer.stop()
        self.metadata_queue.clear()
        self.metadata_rebuild_needed = False
        self.status_timer.stop()
        self.status_queue.clear()
        self.visible_paths = []
        self.preview_labels.clear()
        self.family_labels.clear()
        self.status_labels.clear()
        self.empty_state_label = None

        while self.catalog_grid.count():
            item = self.catalog_grid.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

        for font_path in self.font_paths:
            family = self.family_for(font_path)
            status_key, _ = self.status_for(font_path)
            haystack = f"{family} {font_path.name} {font_path.parent.name}".casefold()
            if query and query not in haystack:
                continue
            if status_filter != "all" and self.status_ready and status_key != status_filter:
                continue
            if format_filter != "all" and font_path.suffix.lower() != format_filter:
                continue
            if self.duplicates_only.isChecked() and font_path not in self.duplicate_paths:
                continue
            self.visible_paths.append(font_path)

        if group_mode != "none":
            if group_mode == "family":
                self.visible_paths.sort(
                    key=lambda path: (self.family_for(path).casefold(), str(path).casefold())
                )
            else:
                self.visible_paths.sort(
                    key=lambda path: (path.parent.name.casefold(), str(path).casefold())
                )

        self.card_render_timer.stop()
        self.card_render_queue = list(self.visible_paths)
        self.card_render_index = 0
        self.card_render_row = 0
        self.card_render_column = 0
        self.card_render_group = None
        self.card_render_group_mode = group_mode
        if not self.visible_paths and self.font_paths:
            self.empty_state_label = QLabel(
                "Resolving font family names; results will update shortly..."
                if query and len(self.font_ids) < len(self.font_paths)
                else "No fonts match the current filters."
            )
            self.empty_state_label.setWordWrap(True)
            self.empty_state_label.setStyleSheet("color: #8b949e; padding: 18px;")
            self.catalog_grid.addWidget(self.empty_state_label, 0, 0, 1, 3)
        for column in range(3):
            self.catalog_grid.setColumnStretch(column, 1)

        self.update_count_label()
        self.select_visible_button.setEnabled(bool(self.visible_paths) and not self.catalog_loading)
        if self.card_render_queue:
            self.metadata_status.setText(
                f"Loading catalog cards... 0/{len(self.card_render_queue)}"
            )
            self.card_render_timer.start()
        else:
            self.schedule_metadata_loading()
            self.schedule_status_loading()

    def update_count_label(self):
        hidden_selected = len(self.selected_paths.difference(self.visible_paths))
        selection_text = f"{len(self.selected_paths)} selected"
        if hidden_selected:
            selection_text += f" ({hidden_selected} hidden)"
        duplicate_text = (
            f"{self.duplicate_extra_count} duplicate copies"
            if self.duplicate_extra_count
            else "no duplicate copies"
        )
        self.count_label.setText(
            f"Showing {len(self.visible_paths)} of {len(self.font_paths)} files "
            f"· {self.unique_font_count} unique · {selection_text} · {duplicate_text}"
        )
        has_selection = bool(self.selected_paths)
        has_hidden_selection = bool(hidden_selected)
        self.clear_hidden_button.setEnabled(has_hidden_selection and not self.catalog_loading)
        self.install_button.setEnabled(has_selection and not self.catalog_loading)
        self.uninstall_button.setEnabled(has_selection and not self.catalog_loading)

    def render_card_batch(self):
        """Add a small card batch so the catalog remains responsive while filling."""
        batch_size = 16
        total = len(self.card_render_queue)
        for _ in range(batch_size):
            if self.card_render_index >= total:
                break

            font_path = self.card_render_queue[self.card_render_index]
            if self.card_render_group_mode == "none":
                row, column = divmod(self.card_render_index, 3)
            else:
                group_name = (
                    self.family_for(font_path)
                    if self.card_render_group_mode == "family"
                    else (font_path.parent.name or "Root")
                )
                if group_name != self.card_render_group:
                    if self.card_render_column:
                        self.card_render_row += 1
                        self.card_render_column = 0
                    group_label = QLabel(group_name)
                    group_label.setStyleSheet(
                        "color: #e3b341; font-size: 13px; font-weight: bold; "
                        "padding: 8px 2px 2px;"
                    )
                    self.catalog_grid.addWidget(
                        group_label,
                        self.card_render_row,
                        0,
                        1,
                        3,
                    )
                    self.card_render_row += 1
                    self.card_render_group = group_name
                row = self.card_render_row
                column = self.card_render_column

            self.catalog_grid.addWidget(self.create_card(font_path), row, column)
            self.card_render_index += 1
            if self.card_render_group_mode != "none":
                self.card_render_column += 1
                if self.card_render_column == 3:
                    self.card_render_row += 1
                    self.card_render_column = 0

        if self.card_render_index < total:
            self.metadata_status.setText(
                f"Loading catalog cards... {self.card_render_index}/{total}"
            )
            return

        self.card_render_timer.stop()
        self.card_render_queue.clear()
        self.schedule_metadata_loading()
        self.schedule_status_loading()

    def create_card(self, font_path):
        family = self.family_for(font_path)
        status_key, status_text = self.status_for(font_path)
        card = QFrame()
        card.setObjectName("fontCard")
        card.setMinimumWidth(260)
        card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(10, 10, 10, 10)
        card_layout.setSpacing(7)

        header = QHBoxLayout()
        checkbox = QCheckBox()
        checkbox.setChecked(font_path in self.selected_paths)
        checkbox.setToolTip("Select this font for a bulk operation")
        checkbox.stateChanged.connect(
            lambda state, path=font_path: self.set_selected(path, state == Qt.Checked)
        )
        header.addWidget(checkbox)

        family_label = QLabel(family)
        family_label.setStyleSheet("font-size: 15px; font-weight: bold; color: #f0f6fc;")
        family_label.setWordWrap(True)
        family_label.setMinimumWidth(0)
        family_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        header.addWidget(family_label, 1)
        card_layout.addLayout(header)
        self.family_labels[font_path] = family_label

        file_label = QLabel(f"{font_path.name} · {font_path.suffix.upper()[1:]}")
        file_label.setStyleSheet("color: #8b949e; font-size: 11px;")
        file_label.setWordWrap(True)
        file_label.setMinimumWidth(0)
        file_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        file_label.setToolTip(str(font_path))
        card_layout.addWidget(file_label)

        preview = QLabel(self.sample_text())
        preview.setFont(make_preview_font(family, self.preview_size.value()))
        preview.setAlignment(Qt.AlignCenter)
        preview.setWordWrap(True)
        preview.setMinimumHeight(82)
        preview.setMinimumWidth(0)
        preview.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        preview.setStyleSheet("color: #c9d1d9; background-color: #0d1117; border-radius: 4px; padding: 8px;")
        card_layout.addWidget(preview)
        self.preview_labels[font_path] = (preview, family)

        status_label = QLabel(status_text)
        status_label.setStyleSheet(
            f"color: {self.status_color(status_key)}; font-size: 11px;"
        )
        card_layout.addWidget(status_label)
        self.status_labels[font_path] = status_label

        preview_button = QPushButton("Open Preview")
        preview_button.clicked.connect(lambda _, path=font_path: self.open_preview(path))
        card_layout.addWidget(preview_button)
        return card

    def set_selected(self, font_path, selected):
        if selected:
            self.selected_paths.add(font_path)
        else:
            self.selected_paths.discard(font_path)
        self.update_count_label()

    def manage_duplicates(self):
        if not self.host:
            return
        if self.host.worker and self.host.worker.isRunning():
            QMessageBox.information(self, "Operation in progress", "Wait for the current operation to finish.")
            return
        dialog = DuplicateManagerDialog(self.host, self)
        dialog.exec_()
        self.refresh_catalog()

    def undo_last(self):
        if not self.host:
            return
        if self.host.worker and self.host.worker.isRunning():
            QMessageBox.information(self, "Operation in progress", "Wait for the current operation to finish.")
            return
        self.set_operation_busy(True, "Undoing the last operation...")
        self.host.run_thread(
            "undo",
            on_finished=self.operation_finished,
            on_update=self.handle_operation_update,
        )

    def update_preview_size(self, size):
        for preview, family in self.preview_labels.values():
            preview.setFont(make_preview_font(family, size))

    def select_visible(self):
        self.selected_paths.update(self.visible_paths)
        self.rebuild_cards()

    def clear_selection(self):
        self.selected_paths.clear()
        self.rebuild_cards()

    def clear_hidden_selection(self):
        hidden_paths = self.selected_paths.difference(self.visible_paths)
        if not hidden_paths:
            return
        self.selected_paths.difference_update(hidden_paths)
        self.rebuild_cards()

    def install_selected(self):
        if not self.selected_paths or not self.host:
            return
        if self.host.worker and self.host.worker.isRunning():
            QMessageBox.information(self, "Operation in progress", "Wait for the current operation to finish.")
            return
        self.set_operation_busy(True, "Installing selected fonts...")
        self.host.run_thread(
            "install",
            scope=self.scope_combo.currentData(),
            selected_fonts=sorted(self.selected_paths, key=lambda path: str(path).casefold()),
            on_finished=self.operation_finished,
            on_update=self.handle_operation_update,
        )

    def uninstall_selected(self):
        if not self.selected_paths or not self.host:
            return
        answer = QMessageBox.question(
            self,
            "Confirm font removal",
            f"Remove matching installed copies of {len(self.selected_paths)} selected font(s)?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        if self.host.worker and self.host.worker.isRunning():
            QMessageBox.information(self, "Operation in progress", "Wait for the current operation to finish.")
            return
        self.set_operation_busy(True, "Removing selected fonts...")
        self.host.run_thread(
            "uninstall-many",
            selected_fonts=sorted(self.selected_paths, key=lambda path: str(path).casefold()),
            on_finished=self.operation_finished,
            on_update=self.handle_operation_update,
        )

    def handle_operation_update(self, text, _color):
        """Mirror worker progress in the catalog while it is modal."""
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if not lines:
            return
        progress_tokens = (
            "[INSTALLED]",
            "[REMOVED]",
            "[ASSET REMOVAL]",
            "[UNDO]",
            "[SKIPPED]",
            "[FAILED]",
        )
        self.operation_completed_count += sum(
            line.startswith(progress_tokens) for line in lines
        )
        latest = lines[-1]
        if self.operation_completed_count:
            self.operation_label.setText(
                f"{latest} · {self.operation_completed_count} item(s) processed"
            )
        else:
            self.operation_label.setText(latest)

    def set_operation_busy(self, busy, message=""):
        self.operation_label.setText(message)
        if busy:
            self.operation_completed_count = 0
        for button in (
            self.select_visible_button,
            self.clear_selection_button,
            self.clear_hidden_button,
            self.refresh_button,
            self.install_button,
            self.uninstall_button,
            self.duplicate_button,
            self.undo_button,
        ):
            button.setEnabled(not busy)
        self.cancel_button.setEnabled(busy)

    def cancel_operation(self):
        if self.index_worker and self.index_worker.isRunning():
            self.index_worker.requestInterruption()
            self.operation_label.setText("Cancellation requested; finishing the index scan...")
            return
        if self.host and self.host.worker and self.host.worker.isRunning():
            self.host.worker.requestInterruption()
            self.operation_label.setText("Cancellation requested...")

    def refresh_catalog(self):
        if not self.host or self.catalog_loading:
            return
        self.metadata_cache.save()
        self.start_indexing()

    def operation_finished(self):
        if self.host:
            self.host.font_manager.invalidate_installed_index()
        self.set_operation_busy(False)
        self.refresh_catalog()

    def open_preview(self, font_path):
        dialog = FontPreviewDialog(font_path, self.host)
        dialog.exec_()

    def closeEvent(self, event):
        if self.host and self.host.worker and self.host.worker.isRunning():
            QMessageBox.information(self, "Operation in progress", "Close the catalog after the operation finishes.")
            event.ignore()
            return
        self._closing = True
        if self.index_worker and self.index_worker.isRunning():
            self.index_worker.requestInterruption()
            if not self.index_worker.wait(5000):
                self._closing = False
                QMessageBox.information(
                    self,
                    "Indexing in progress",
                    "The asset index is still being built. Try closing again shortly.",
                )
                event.ignore()
                return
            self.index_worker.deleteLater()
            self.index_worker = None
        self.card_render_timer.stop()
        self.card_render_queue.clear()
        self.metadata_timer.stop()
        self.metadata_queue.clear()
        self.status_timer.stop()
        self.status_queue.clear()
        self.metadata_cache.save()
        for font_id in self.font_ids.values():
            QFontDatabase.removeApplicationFont(font_id)
        super().closeEvent(event)


class DuplicateManagerDialog(QDialog):
    """Review byte-identical asset copies and remove extras with backups."""

    def __init__(self, host, parent=None):
        super().__init__(parent)
        self.host = host
        self.index_worker = None
        self.duplicate_loading = False
        self._closing = False
        self.setWindowTitle("Duplicate & Family Manager")
        self.resize(820, 620)

        theme = host.themes[host.current_theme]
        self.setStyleSheet(f"""
            QDialog {{ background-color: {theme['bg']}; color: {theme['fg']}; }}
            QTreeWidget {{
                background-color: #161b22;
                color: {theme['fg']};
                border: 1px solid {theme['border']};
            }}
            QPushButton {{
                background-color: #21262d;
                color: {theme['fg']};
                border: 1px solid {theme['border']};
                border-radius: 4px;
                padding: 7px 12px;
            }}
            QPushButton:hover {{ background-color: {theme['border']}; }}
            QPushButton:disabled {{ color: #484f58; }}
        """)

        layout = QVBoxLayout(self)
        instructions = QLabel(
            "Exact byte-identical copies are grouped below. The first copy in each group "
            "is kept as the canonical copy; selected extra copies can be backed up and removed."
        )
        instructions.setWordWrap(True)
        instructions.setStyleSheet("color: #8b949e; padding-bottom: 4px;")
        layout.addWidget(instructions)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Duplicate group / asset file", "Location or hash"])
        self.tree.setColumnWidth(0, 320)
        self.tree.setColumnWidth(1, 300)
        self.tree.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.tree.setAlternatingRowColors(True)
        self.tree.setUniformRowHeights(True)
        layout.addWidget(self.tree, 1)

        self.count_label = QLabel()
        self.count_label.setWordWrap(True)
        self.count_label.setMinimumWidth(0)
        self.count_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.count_label.setStyleSheet("color: #8b949e;")
        layout.addWidget(self.count_label)

        self.operation_label = QLabel()
        self.operation_label.setWordWrap(True)
        self.operation_label.setStyleSheet("color: #e3b341;")
        layout.addWidget(self.operation_label)

        footer = QGridLayout()
        self.select_extras_button = QPushButton("Select Extra Copies")
        self.remove_button = QPushButton("Back up & Remove Selected")
        self.undo_button = QPushButton("Undo Last")
        self.refresh_button = QPushButton("Refresh")
        self.cancel_button = QPushButton("Cancel Operation")
        self.cancel_button.setEnabled(False)
        self.close_button = QPushButton("Close")
        for index, button in enumerate((
            self.select_extras_button,
            self.undo_button,
            self.remove_button,
            self.refresh_button,
            self.cancel_button,
            self.close_button,
        )):
            footer.addWidget(button, index // 2, index % 2)
        layout.addLayout(footer)
        self.operation_completed_count = 0

        self.select_extras_button.clicked.connect(self.select_extra_copies)
        self.remove_button.clicked.connect(self.remove_selected)
        self.undo_button.clicked.connect(self.undo_last)
        self.refresh_button.clicked.connect(self.start_indexing)
        self.cancel_button.clicked.connect(self.cancel_operation)
        self.close_button.clicked.connect(self.close)

        self.count_label.setText("Waiting for the asset index...")
        self.start_indexing()
        fit_dialog_to_screen(self, 820, 620)

    def set_index_controls_enabled(self, enabled):
        for button in (
            self.select_extras_button,
            self.remove_button,
            self.undo_button,
            self.refresh_button,
        ):
            button.setEnabled(enabled)
        self.close_button.setEnabled(True)

    def start_indexing(self):
        if self.index_worker and self.index_worker.isRunning():
            return
        self.duplicate_loading = True
        self.set_index_controls_enabled(False)
        self.operation_label.setText("Indexing duplicate groups...")
        worker = CatalogIndexWorker(ASSET_DIR, FONT_INDEX_FILE, self)
        self.index_worker = worker
        worker.records_ready.connect(self.load_indexed_groups)
        worker.error_signal.connect(self.handle_index_error)
        worker.finished.connect(
            lambda finished_worker=worker: self.indexing_finished(finished_worker)
        )
        worker.start()

    def load_indexed_groups(self, records):
        if self._closing:
            return
        self.duplicate_loading = False
        self.set_index_controls_enabled(True)
        self.operation_label.clear()
        self.load_groups(records)

    def handle_index_error(self, message):
        if self._closing:
            return
        self.duplicate_loading = False
        self.count_label.setText(f"Duplicate indexing failed: {message}")
        self.operation_label.clear()
        self.set_index_controls_enabled(False)
        self.refresh_button.setEnabled(True)

    def indexing_finished(self, worker):
        if self.index_worker is worker:
            self.index_worker = None
        worker.deleteLater()

    def load_groups(self, records=None):
        if records is None:
            groups = FontManager.duplicate_groups(ASSET_DIR, FONT_INDEX_FILE)
        else:
            groups_by_digest = {}
            for record in records:
                groups_by_digest.setdefault(record.digest, []).append(record.path)
            groups = {
                digest: paths
                for digest, paths in groups_by_digest.items()
                if len(paths) > 1
            }
        self.tree.clear()
        duplicate_count = 0
        for digest, paths in sorted(groups.items(), key=lambda item: str(item[1][0]).casefold()):
            group_item = QTreeWidgetItem(
                self.tree,
                [f"Same content · {len(paths)} copies", f"SHA-256: {digest[:16]}…"],
            )
            group_item.setExpanded(True)
            for index, path in enumerate(paths):
                label = path.name + ("  (keep)" if index == 0 else "")
                child = QTreeWidgetItem(group_item, [label, str(path.parent)])
                child.setData(0, Qt.UserRole, str(path))
                if index == 0:
                    child.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                else:
                    child.setFlags(child.flags() | Qt.ItemIsUserCheckable)
                    child.setCheckState(0, Qt.Unchecked)
                    duplicate_count += 1

        self.count_label.setText(
            f"{len(groups)} duplicate group(s) · {duplicate_count} removable extra copy/copies"
        )
        self.remove_button.setEnabled(bool(duplicate_count))

    def select_extra_copies(self):
        for group_index in range(self.tree.topLevelItemCount()):
            group_item = self.tree.topLevelItem(group_index)
            for child_index in range(1, group_item.childCount()):
                group_item.child(child_index).setCheckState(0, Qt.Checked)

    def selected_paths(self):
        selected = []
        for group_index in range(self.tree.topLevelItemCount()):
            group_item = self.tree.topLevelItem(group_index)
            for child_index in range(group_item.childCount()):
                child = group_item.child(child_index)
                if child.checkState(0) == Qt.Checked:
                    value = child.data(0, Qt.UserRole)
                    if value:
                        selected.append(Path(value))
        return selected

    def remove_selected(self):
        selected = self.selected_paths()
        if not selected:
            QMessageBox.information(self, "No selection", "Select one or more extra copies first.")
            return
        answer = QMessageBox.question(
            self,
            "Confirm duplicate cleanup",
            f"Back up and remove {len(selected)} duplicate asset copy/copies?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        if self.host.worker and self.host.worker.isRunning():
            QMessageBox.information(self, "Operation in progress", "Wait for the current operation to finish.")
            return
        self.set_busy(True, "Backing up and removing selected copies...")
        self.host.run_thread(
            "remove-assets",
            selected_fonts=sorted(selected, key=lambda path: str(path).casefold()),
            on_finished=self.operation_finished,
            on_update=self.handle_operation_update,
        )

    def undo_last(self):
        if self.host.worker and self.host.worker.isRunning():
            QMessageBox.information(self, "Operation in progress", "Wait for the current operation to finish.")
            return
        self.set_busy(True, "Undoing the last operation...")
        self.host.run_thread(
            "undo",
            on_finished=self.operation_finished,
            on_update=self.handle_operation_update,
        )

    def handle_operation_update(self, text, _color):
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if not lines:
            return
        progress_tokens = (
            "[ASSET REMOVAL]",
            "[UNDO]",
            "[SKIPPED]",
            "[FAILED]",
        )
        self.operation_completed_count += sum(
            line.startswith(progress_tokens) for line in lines
        )
        latest = lines[-1]
        if self.operation_completed_count:
            self.operation_label.setText(
                f"{latest} · {self.operation_completed_count} item(s) processed"
            )
        else:
            self.operation_label.setText(latest)

    def set_busy(self, busy, message=""):
        self.operation_label.setText(message)
        if busy:
            self.operation_completed_count = 0
        for button in (
            self.select_extras_button,
            self.remove_button,
            self.undo_button,
            self.refresh_button,
        ):
            button.setEnabled(not busy)
        self.cancel_button.setEnabled(busy)

    def cancel_operation(self):
        if self.index_worker and self.index_worker.isRunning():
            self.index_worker.requestInterruption()
            self.operation_label.setText("Cancellation requested; finishing the index scan...")
            return
        if self.host.worker and self.host.worker.isRunning():
            self.host.worker.requestInterruption()
            self.operation_label.setText("Cancellation requested...")

    def operation_finished(self):
        self.set_busy(False)
        self.start_indexing()

    def closeEvent(self, event):
        if self.host.worker and self.host.worker.isRunning():
            QMessageBox.information(self, "Operation in progress", "Close after the operation finishes.")
            event.ignore()
            return
        self._closing = True
        if self.index_worker and self.index_worker.isRunning():
            self.index_worker.requestInterruption()
            if not self.index_worker.wait(5000):
                self._closing = False
                QMessageBox.information(
                    self,
                    "Indexing in progress",
                    "The asset index is still being built. Try closing again shortly.",
                )
                event.ignore()
                return
            self.index_worker.deleteLater()
            self.index_worker = None
        super().closeEvent(event)


class WorkerThread(QThread):
    update_signal = pyqtSignal(str, str)
    finished_signal = pyqtSignal()

    def __init__(self, task_type, target_font=None, scope="user", selected_fonts=None):
        super().__init__()
        self.task_type = task_type
        self.target_font = target_font
        self.scope = scope
        self.selected_fonts = selected_fonts
        self.manager = FontManager()

    def run(self):
        try:
            if self.task_type == "check":
                self.check_installed()
            elif self.task_type == "install":
                self.install_fonts()
            elif self.task_type == "uninstall":
                self.uninstall_font(self.target_font)
            elif self.task_type == "uninstall-many":
                self.uninstall_fonts()
            elif self.task_type == "remove-assets":
                self.remove_asset_files()
            elif self.task_type == "undo":
                self.undo_last()
            elif self.task_type == "sys-audit":
                self.system_audit()
            elif self.task_type == "search":
                self.search_fonts(self.target_font)
            elif self.task_type == "sync":
                self.sync_fonts()
            elif self.task_type == "validate":
                self.validate_fonts()
        except Exception as error:
            self.update_signal.emit(f"Unexpected worker error: {error}\n", "#ff5555")
        finally:
            self.finished_signal.emit()

    def search_fonts(self, query):
        if not query:
            self.update_signal.emit("Error: No search query provided.\n", "#ff5555")
            return

        self.update_signal.emit(f"Searching for '{query}' across all libraries...\n\n", "#e3b341")
        query = query.lower()
        matches = [
            font for font in self.get_asset_fonts()
            if query in font.name.lower() or query in font.parent.name.lower()
        ]

        if not matches:
            self.update_signal.emit(f"No matches found for '{query}'.\n", "#ff7b72")
            return

        for font in matches:
            if self.isInterruptionRequested():
                self.update_signal.emit("Search cancelled.\n", "#e3b341")
                return
            folder_name = font.parent.name if font.parent != ASSET_DIR else "Root"
            self.update_signal.emit(f"  - {font.name}\n", "#58a6ff")
            self.update_signal.emit(f"    Location: {folder_name}\n", "#8b949e")
        self.update_signal.emit(f"\nFound {len(matches)} matching fonts.\n", "#3fb950")

    def sync_fonts(self):
        self.update_signal.emit(f"Analyzing system sync status ({self.scope} scope)...\n", "#e3b341")
        fonts = self.get_asset_fonts()
        missing = []
        for font in fonts:
            if self.isInterruptionRequested():
                self.update_signal.emit("Sync cancelled.\n", "#e3b341")
                return
            if not self._has_matching_install(font, self.scope):
                missing.append(font)

        if not missing:
            self.update_signal.emit("System is already in sync. All library fonts are installed.\n", "#3fb950")
            return

        self.update_signal.emit(f"Found {len(missing)} missing fonts. Starting sync...\n\n", "#58a6ff")
        result = self.manager.install(missing, self.scope, self.isInterruptionRequested)
        self.add_cache_warning(result, self.scope)
        self.emit_operation_result(result, "Synced")

    def get_asset_fonts(self):
        return FontManager.discover_fonts(ASSET_DIR, FONT_INDEX_FILE)

    def system_audit(self):
        self.update_signal.emit("Starting System-to-Asset Audit...\n", "#e3b341")
        asset_signatures = set()
        for font in self.get_asset_fonts():
            try:
                asset_signatures.add((font.name.lower(), self.manager.file_digest(font)))
            except OSError:
                continue

        external = []
        for scope in ("user", "system"):
            directory = self.manager.directory(scope)
            if not directory.exists():
                continue
            for font in directory.rglob("*"):
                if self.isInterruptionRequested():
                    self.update_signal.emit("Audit cancelled.\n", "#e3b341")
                    return
                if not self.manager.is_font_file(font):
                    continue
                try:
                    signature = (font.name.lower(), self.manager.file_digest(font))
                except OSError:
                    signature = None
                if signature not in asset_signatures:
                    external.append((scope, font))

        if not external:
            self.update_signal.emit("\nClean! All installed fonts match your asset library.\n", "#3fb950")
        else:
            self.update_signal.emit(
                f"\nFound {len(external)} fonts not matching your library:\n", "#ff7b72"
            )
            self.update_signal.emit(
                "Note: Many system fonts are required by the operating system.\n\n", "#8b949e"
            )
            for scope, font in sorted(external, key=lambda item: item[1].name.lower())[:60]:
                self.update_signal.emit(f"  [EXTERNAL/{scope}] {font.name}\n", "#8b949e")
            if len(external) > 60:
                self.update_signal.emit(f"\n... and {len(external) - 60} more files.\n", "#8b949e")

        self.update_signal.emit("\nAudit complete.\n", "#58a6ff")

    def check_installed(self):
        fonts = self.get_asset_fonts()
        if not fonts:
            self.update_signal.emit("No source fonts to check.\n", "#ff5555")
            return

        self.update_signal.emit(f"Verifying installation status for {len(fonts)} fonts...\n\n", "#8b949e")
        installed_count = 0
        conflict_count = 0
        for font in fonts:
            if self.isInterruptionRequested():
                self.update_signal.emit("Status check cancelled.\n", "#e3b341")
                return
            exact, conflicts = self._matching_install_scopes(font)
            if exact:
                installed_count += 1
                self.update_signal.emit(
                    f"  [INSTALLED]  {font.name} ({', '.join(exact)})\n", "#3fb950"
                )
            elif conflicts:
                conflict_count += 1
                self.update_signal.emit(
                    f"  [CONFLICT ]  {font.name} ({', '.join(conflicts)})\n", "#e3b341"
                )
            else:
                self.update_signal.emit(f"  [ MISSING ]  {font.name}\n", "#ff5555")

        self.update_signal.emit(
            f"\nSummary: {installed_count}/{len(fonts)} installed, "
            f"{conflict_count} conflicts.\n", "#8b949e"
        )

    def install_fonts(self):
        fonts = self.selected_fonts if self.selected_fonts is not None else self.get_asset_fonts()
        if not fonts:
            self.update_signal.emit("Nothing to install.\n", "#ff5555")
            return

        self.update_signal.emit(
            f"Installing {len(fonts)} font(s) to the {self.scope} directory...\n\n", "#8b949e"
        )
        result = self.manager.install(fonts, self.scope, self.isInterruptionRequested)
        self.add_cache_warning(result, self.scope)
        self.emit_operation_result(result, "Installed")

    def uninstall_font(self, font_file):
        if not font_file:
            return
        self.update_signal.emit(f"Uninstalling {font_file.name}...\n", "#e3b341")
        result = self.manager.uninstall(font_file, should_cancel=self.isInterruptionRequested)
        self.add_cache_warning(result)
        self.emit_operation_result(result, "Removed")

    def uninstall_fonts(self):
        fonts = self.selected_fonts or []
        if not fonts:
            self.update_signal.emit("No fonts selected for removal.\n", "#ff5555")
            return

        self.update_signal.emit(f"Removing {len(fonts)} selected font(s)...\n\n", "#e3b341")
        result = self.manager.uninstall_many(
            fonts,
            should_cancel=self.isInterruptionRequested,
        )
        self.add_cache_warning(result)
        self.emit_operation_result(result, "Removed")

    def remove_asset_files(self):
        fonts = self.selected_fonts or []
        if not fonts:
            self.update_signal.emit("No asset copies selected for removal.\n", "#ff5555")
            return

        self.update_signal.emit(
            f"Backing up and removing {len(fonts)} asset copy/copies...\n\n",
            "#e3b341",
        )
        result = self.manager.remove_asset_files(
            fonts,
            should_cancel=self.isInterruptionRequested,
        )
        self.add_cache_warning(result, refresh=False)
        self.emit_operation_result(result, "Asset removal")

    def undo_last(self):
        self.update_signal.emit("Undoing the last recorded operation...\n\n", "#e3b341")
        result = self.manager.undo_last(self.isInterruptionRequested)
        self.add_cache_warning(result)
        self.emit_operation_result(result, "Undo")

    def validate_fonts(self):
        fonts = self.get_asset_fonts()
        if not fonts:
            self.update_signal.emit("Nothing to validate.\n", "#ff5555")
            return
        self.update_signal.emit(f"Validating {len(fonts)} font files...\n\n", "#e3b341")
        self.emit_operation_result(self.manager.validate(fonts), "Valid")

    def add_cache_warning(self, result, scope=None, refresh=True):
        if not refresh:
            return
        warning = self.manager.refresh_font_cache(scope)
        if warning:
            result.warnings.append(warning)

    def _matching_install_scopes(self, source, scope=None):
        exact = []
        conflicts = []
        try:
            source_digest = self.manager.file_digest(source)
        except OSError:
            return exact, conflicts
        locations = self.manager.installed_paths(source)
        if scope is not None:
            locations = {
                selected_scope: installed
                for selected_scope, installed in locations.items()
                if selected_scope == scope
            }
        for selected_scope, installed in locations.items():
            try:
                if self.manager.file_digest(installed) == source_digest:
                    exact.append(selected_scope)
                else:
                    conflicts.append(selected_scope)
            except OSError:
                conflicts.append(selected_scope)
        return exact, conflicts

    def _has_matching_install(self, source, scope=None):
        exact, _ = self._matching_install_scopes(source, scope)
        return bool(exact)

    def emit_operation_result(self, result: OperationResult, action: str):
        for path in result.changed:
            self.update_signal.emit(f"  [{action.upper()}] {path.name}\n", "#3fb950")
        for path, reason in result.skipped:
            self.update_signal.emit(f"  [SKIPPED] {path.name}: {reason}\n", "#8b949e")
        for path, reason in result.failed:
            self.update_signal.emit(f"  [FAILED] {path.name}: {reason}\n", "#ff5555")
        for warning in result.warnings:
            self.update_signal.emit(f"  [WARNING] {warning}\n", "#e3b341")
        if result.backups:
            self.update_signal.emit(
                f"  [BACKUP] Saved {len(result.backups)} file(s) for undo.\n",
                "#58a6ff",
            )
        if result.history_id:
            self.update_signal.emit("  Undo history updated.\n", "#8b949e")

        if result.cancelled:
            self.update_signal.emit("\nOperation cancelled.\n", "#e3b341")
        status_color = "#ff5555" if result.failed else "#3fb950"
        self.update_signal.emit(
            f"\n{action} complete: {len(result.changed)} changed, "
            f"{len(result.skipped)} skipped, {len(result.failed)} failed.\n",
            status_color,
        )

class TerminalWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("soheil-os -- zsh")
        self.resize(900, 600)
        
        # State Management variables
        self.current_state = "MAIN"
        self.folder_map = {}
        self.loose_font_paths = []
        self.loose_font_file_count = 0
        self.font_map = {}
        self.worker = None
        self.worker_callback = None
        self.worker_update_callback = None
        self.font_manager = FontManager()
        self.install_scope = "user"
        
        # New Feature State
        self.preview_text_per = "عجایب صنع حق را بنگر و از ذهن غافل دور کن که چطور ذرات جهان با نظم خاضعانه به خدمت خلق چیده شده‌اند\n۰۱۲۳۴۵۶۷۸۹ - 0123456789"
        self.preview_text_eng = "The quick brown fox jumps over the lazy dog.\nABCDEFGHIJKL MNOPQRST UVWXYZ\n0123456789"
        self.current_theme = "default"
        self.themes = {
            "default": {"bg": "#0d1117", "fg": "#8b949e", "prompt": "#3fb950", "input": "#58a6ff", "border": "#30363d"},
            "matrix": {"bg": "#000000", "fg": "#00ff00", "prompt": "#008f11", "input": "#00ff41", "border": "#003b00"},
            "dracula": {"bg": "#282a36", "fg": "#f8f8f2", "prompt": "#50fa7b", "input": "#8be9fd", "border": "#44475a"},
            "cyberpunk": {"bg": "#000b1e", "fg": "#00ff9f", "prompt": "#ff003c", "input": "#fdf9ff", "border": "#00ff9f"},
            "soheil-special": {"bg": "#050a15", "fg": "#d4af37", "prompt": "#ff4500", "input": "#ffd700", "border": "#1a2a44"}
        }

        # Load Persistent Config
        self.load_config()

        # Main Layout
        self.central_widget = QWidget()
        self.setCentralWidget(self.central_widget)
        self.layout = QVBoxLayout(self.central_widget)
        self.layout.setContentsMargins(15, 15, 15, 15)
        self.layout.setSpacing(0)

        # 1. Output Display
        self.output_area = QTextEdit()
        self.output_area.setReadOnly(True)
        self.output_area.setFrameStyle(0)
        self.layout.addWidget(self.output_area)

        # 2. Input Bar
        self.input_frame = QWidget()
        self.input_layout = QHBoxLayout(self.input_frame)
        self.input_layout.setContentsMargins(0, 5, 0, 0)
        self.input_layout.setSpacing(5)

        host_name = platform.node() or "local"
        self.prompt_label = QLabel(f"soheil@{host_name}:~$")
        self.prompt_label.setObjectName("prompt")
        
        self.input_line = QLineEdit()
        self.input_line.setFrame(False)
        self.input_line.returnPressed.connect(self.process_command)
        
        self.input_layout.addWidget(self.prompt_label)
        self.input_layout.addWidget(self.input_line)
        self.layout.addWidget(self.input_frame)

        # --- STYLING (Hacker CSS) ---
        self.setStyleSheet("""
            QMainWindow, QWidget { background-color: #0d1117; }
            QTextEdit {
                background-color: #0d1117;
                color: #8b949e; 
                font-family: 'Consolas', 'Menlo', 'Courier New';
                font-size: 14px;
                border: none;
            }
            QScrollBar:vertical {
                border: none;
                background: #0d1117;
                width: 10px;
                margin: 0px 0px 0px 0px;
            }
            QScrollBar::handle:vertical {
                background: #21262d; 
                min-height: 20px;
                border-radius: 5px;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; background: none; }
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: none; }
            QLabel#prompt {
                color: #3fb950; 
                font-family: 'Consolas', 'Menlo', 'Courier New';
                font-size: 14px;
                font-weight: bold;
            }
            QLineEdit {
                background-color: #0d1117;
                color: #58a6ff; 
                font-family: 'Consolas', 'Menlo', 'Courier New';
                font-size: 14px;
                border: none;
            }
        """)

        # --- ANIMATION ENGINE ---
        self.msg_queue = deque()
        self.current_msg_text = ""
        self.current_msg_color = ""
        self.pending_commands = []
        self.font_list_queue = []
        self.font_list_index = 0
        self.font_list_display_name = ""
        self.font_list_timer = QTimer(self)
        self.font_list_timer.setInterval(0)
        self.font_list_timer.timeout.connect(self.process_font_list_batch)
        
        self.type_timer = QTimer()
        self.type_timer.timeout.connect(self.process_typewriter)
        self.type_timer.start(5) 

        # Command Registry for automatic numbering
        self.main_commands = [
            {"cmd": "sync",     "desc": "Install only missing fonts",   "func": self.handle_sync_cmd},
            {"cmd": "search",   "desc": "Search fonts and open matching catalog cards", "func": self.handle_search_cmd},
            {"cmd": "list",     "desc": "Browse font folders",         "func": self.execute_list_command},
            {"cmd": "catalog",  "desc": "Open the visual font catalog", "func": self.open_catalog},
            {"cmd": "duplicates", "desc": "Review and remove duplicate assets", "func": self.open_duplicate_manager},
            {"cmd": "undo",     "desc": "Undo the last recorded operation", "func": self.handle_undo_cmd},
            {"cmd": "history",  "desc": "Show recent operation history", "func": self.handle_history_cmd},
            {"cmd": "audit",    "desc": "Check system status vs assets", "func": lambda: self.run_thread("check")},
            {"cmd": "sys-audit", "desc": "Find fonts NOT in asset library", "func": lambda: self.run_thread("sys-audit")},
            {"cmd": "sys-list", "desc": "List all installed system fonts", "func": self.execute_sys_list},
            {"cmd": "validate", "desc": "Validate asset font files", "func": lambda: self.run_thread("validate")},
            {"cmd": "theme",    "desc": "Change UI theme (matrix, dracula, etc.)", "func": self.handle_theme_cmd},
            {"cmd": "set-text", "desc": "Set custom preview text (per/eng)", "func": self.handle_set_text_cmd},
            {"cmd": "scope",    "desc": "Choose default install scope (user/system)", "func": self.handle_scope_cmd},
            {"cmd": "cancel",   "desc": "Cancel the active background operation", "func": self.handle_cancel_cmd},
            {"cmd": "exit",     "desc": "Close application",           "func": self.handle_exit_cmd}
        ]

        # --- UX: COMMAND HISTORY ---
        self.history = []
        self.history_index = -1
        self.temp_input = ""
        self.input_line.installEventFilter(self)

        # Initialize
        self.print_header()
        self.show_menu()
        self.input_line.setFocus()
        
        # Apply the loaded theme immediately
        self.update_theme(self.current_theme, manual=False)

    def load_config(self):
        """Loads persistent settings from config.json."""
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    config = json.load(f)
                    configured_theme = config.get("theme", "default")
                    if isinstance(configured_theme, str) and configured_theme in self.themes:
                        self.current_theme = configured_theme
                    configured_preview_per = config.get("preview_text_per")
                    if isinstance(configured_preview_per, str):
                        self.preview_text_per = configured_preview_per
                    configured_preview_eng = config.get("preview_text_eng")
                    if isinstance(configured_preview_eng, str):
                        self.preview_text_eng = configured_preview_eng
                    configured_scope = str(config.get("install_scope", self.install_scope)).lower()
                    if configured_scope in {"user", "system"}:
                        self.install_scope = configured_scope
                    # Note: We can't call update_theme here yet because UI isn't fully ready
                    # but the attribute is set for the initial style application.
            except Exception as e:
                print(f"Error loading config: {e}")

    def save_config(self):
        """Saves current settings to config.json."""
        config = {
            "theme": self.current_theme,
            "preview_text_per": self.preview_text_per,
            "preview_text_eng": self.preview_text_eng,
            "install_scope": self.install_scope
        }
        temporary_file = CONFIG_FILE.with_name(f".{CONFIG_FILE.name}.tmp")
        try:
            CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(temporary_file, "w", encoding="utf-8") as f:
                json.dump(config, f, indent=4, ensure_ascii=False)
            os.replace(temporary_file, CONFIG_FILE)
        except Exception as e:
            self.queue_text(f"Error saving config: {e}\n", "#ff5555")
        finally:
            try:
                temporary_file.unlink(missing_ok=True)
            except OSError:
                pass

    def eventFilter(self, source, event):
        """Intercepts arrow keys for history navigation and Tab for completion."""
        if source is self.input_line and event.type() == event.KeyPress:
            if event.key() == Qt.Key_Up:
                self.navigate_history(1)
                return True
            elif event.key() == Qt.Key_Down:
                self.navigate_history(-1)
                return True
            elif event.key() == Qt.Key_Tab:
                self.handle_tab_completion()
                return True
        return super().eventFilter(source, event)

    def mousePressEvent(self, event):
        """Feature 3: Global Auto-Focus. Clicking anywhere focuses the input line."""
        self.input_line.setFocus()
        super().mousePressEvent(event)

    def navigate_history(self, direction):
        """Allows cycling through command history using Up/Down arrows."""
        if not self.history:
            return

        if self.history_index == -1:
            self.temp_input = self.input_line.text()

        new_index = self.history_index + direction
        if 0 <= new_index < len(self.history):
            self.history_index = new_index
            # We want to show most recent first when pressing Up, but self.history is [old -> new]
            # So Up (1) should go from len-1 down to 0
            display_index = len(self.history) - 1 - self.history_index
            self.input_line.setText(self.history[display_index])
        elif new_index == -1:
            self.history_index = -1
            self.input_line.setText(self.temp_input)

    def handle_tab_completion(self):
        """Feature 1: Tab Completion for commands and theme names."""
        text = self.input_line.text().lower()
        parts = text.split()
        if not parts: return

        # Case A: Completing the main command
        if len(parts) == 1:
            candidates = []
            # Check main registry
            for entry in self.main_commands:
                if entry["cmd"].startswith(parts[0]):
                    candidates.append(entry["cmd"])
            context_words = []
            if self.current_state == "FOLDER_SELECT":
                context_words = ["back"]
            elif self.current_state == "FONT_SELECT":
                context_words = ["back", "preview", "info", "compare", "install", "uninstall"]
            # Help and clear are available in every terminal state.
            for word in context_words + ["help", "clear"]:
                if word.startswith(parts[0]):
                    candidates.append(word)
            
            if len(candidates) == 1:
                self.input_line.setText(candidates[0] + " ")
            elif len(candidates) > 1:
                # Show options in terminal (zsh style)
                self.queue_text("\n" + "  ".join(candidates) + "\n", "#8b949e")

        # Case B: Completing theme names
        elif len(parts) == 2 and parts[0] == "theme":
            candidates = [t for t in self.themes.keys() if t.startswith(parts[1])]
            if len(candidates) == 1:
                self.input_line.setText(f"theme {candidates[0]}")
            elif len(candidates) > 1:
                self.queue_text("\n" + "  ".join(candidates) + "\n", "#8b949e")

    def show_help(self):
        """Display only commands valid for the current terminal context."""

        help_text = "AVAILABLE COMMANDS:\n"
        for i, entry in enumerate(self.main_commands, 1):
            help_text += f"  [{i}] {entry['cmd']:<12} - {entry['desc']}\n"

        if self.current_state == "FOLDER_SELECT":
            help_text += "\nFOLDER SELECTION:\n"
            help_text += "  <number>            - Open a font family folder or group\n"
            help_text += "  back                - Return to the main menu\n"
        elif self.current_state == "FONT_SELECT":
            help_text += "\nFONT SELECTION:\n"
            help_text += "  preview <num>       - View a font sample\n"
            help_text += "  info <num>          - Show file, hash, and install status\n"
            help_text += "  compare <n1> <n2>   - Side-by-side view\n"
            help_text += "  install <n...> [--user|--system]\n"
            help_text += "  uninstall <num>     - Remove matching installed copies\n"
            help_text += "  back                - Return to the main menu\n"

        self.queue_text(help_text + "\n", "#58a6ff", immediate=True)

    def trigger_error_feedback(self):
        """Feature 2: Input Validation feedback (Shake and Red Flash)."""
        # 1. Shake Animation
        original_pos = self.input_frame.pos()
        self.shake_anim = QPropertyAnimation(self.input_frame, b"pos")
        self.shake_anim.setDuration(300)
        self.shake_anim.setStartValue(original_pos)
        
        # Add keyframes for shaking
        for i in range(1, 6):
            offset = 10 if i % 2 == 0 else -10
            self.shake_anim.setKeyValueAt(i/6, QPoint(original_pos.x() + offset, original_pos.y()))
        
        self.shake_anim.setEndValue(original_pos)
        self.shake_anim.setEasingCurve(QEasingCurve.InOutQuad)
        self.shake_anim.start()

        # 2. Red Flash
        t = self.themes[self.current_theme]
        self.input_line.setStyleSheet(f"background-color: #440000; color: {t['input']}; border: none;")
        QTimer.singleShot(400, lambda: self.input_line.setStyleSheet(f"background-color: {t['bg']}; color: {t['input']}; border: none;"))

    def handle_search_cmd(self, args=None):
        if args:
            query = " ".join(args).strip()
            self.queue_text(
                f"Opening catalog search for '{query}'...\n",
                "#e3b341",
                immediate=True,
            )
            self.open_catalog(initial_query=query)
        else:
            self.queue_text("Usage: search <font_name>\n", "#ff5555")

    def handle_install_cmd(self, args=None):
        """Install selected font numbers from the active family view."""
        if self.current_state != "FONT_SELECT":
            self.queue_text("Select a family and font first with 'list'.\n", "#e3b341")
            return
        scope, selected, invalid = self.parse_operation_args(args or [], allow_selection=True)
        if invalid or not selected:
            self.queue_text(
                "Usage: install <font_numbers...> [--user|--system]\n",
                "#ff5555",
            )
            return
        self.run_thread("install", scope=scope, selected_fonts=selected)

    def handle_sync_cmd(self, args=None):
        scope, _, invalid = self.parse_operation_args(args or [], allow_selection=False)
        if invalid:
            self.queue_text("Usage: sync [--user|--system]\n", "#ff5555")
            return
        self.run_thread("sync", scope=scope)

    def handle_scope_cmd(self, args=None):
        if not args:
            self.queue_text(
                f"Default install scope: {self.install_scope}\n"
                "Usage: scope <user|system>\n",
                "#8b949e",
            )
            return
        new_scope = args[0].lower()
        if new_scope not in {"user", "system"} or len(args) != 1:
            self.queue_text("Usage: scope <user|system>\n", "#ff5555")
            return
        self.install_scope = new_scope
        self.save_config()
        self.queue_text(f"Default install scope set to: {new_scope}\n", "#3fb950")

    def handle_cancel_cmd(self, args=None):
        if args:
            self.queue_text("Usage: cancel\n", "#ff5555", immediate=True)
            return
        if self.worker and self.worker.isRunning():
            self.worker.requestInterruption()
            self.queue_text(
                "Cancellation requested; finishing the current file...\n",
                "#e3b341",
                immediate=True,
            )
        else:
            self.queue_text("No background operation is running.\n", "#8b949e", immediate=True)

    def handle_undo_cmd(self, args=None):
        if args:
            self.queue_text("Usage: undo\n", "#ff5555")
            return
        if self.worker and self.worker.isRunning():
            self.queue_text("Finish or cancel the active operation before undoing.\n", "#e3b341")
            return
        self.run_thread("undo")

    def handle_history_cmd(self, args=None):
        if args:
            self.queue_text("Usage: history\n", "#ff5555")
            return

        records = self.font_manager.history_records()
        if not records:
            self.queue_text("No recorded operations yet.\n", "#8b949e")
            return

        self.queue_text("Recent reversible operations:\n\n", "#e3b341", immediate=True)
        for record in reversed(records[-20:]):
            action = str(record.get("action", "unknown"))
            created_at = str(record.get("created_at", "unknown")).replace("T", " ")[:19]
            item_count = len(record.get("items", [])) if isinstance(record.get("items", []), list) else 0
            state = "undone" if record.get("undone", False) else "undo available"
            self.queue_text(
                f"  {created_at} · {action} · {item_count} item(s) · {state}\n",
                "#58a6ff" if state == "undo available" else "#8b949e",
                immediate=True,
            )
        self.queue_text(
            "\nUse 'undo' to reverse the newest operation still marked undoable.\n",
            "#8b949e",
            immediate=True,
        )

    def parse_operation_args(self, args, allow_selection):
        scope = self.install_scope
        selected = []
        invalid = False
        for argument in args:
            value = argument.lower()
            if value in {"user", "--user"}:
                scope = "user"
            elif value in {"system", "--system"}:
                scope = "system"
            elif allow_selection and self.current_state == "FONT_SELECT" and argument in self.font_map:
                selected.append(self.font_map[argument])
            else:
                invalid = True
        return scope, (selected or None), invalid

    def handle_theme_cmd(self, args=None):
        if args: self.update_theme(args[0])
        else: self.queue_text("Usage: theme <name> (matrix, dracula, cyberpunk, soheil-special, default)\n", "#ff5555")

    def handle_set_text_cmd(self, args=None):
        if args and len(args) >= 2:
            lang, text = args[0].lower(), " ".join(args[1:])
            if lang == "per":
                self.preview_text_per = text
                self.queue_text("Persian preview text updated.\n", "#3fb950")
            elif lang == "eng":
                self.preview_text_eng = text
                self.queue_text("English preview text updated.\n", "#3fb950")
            else: 
                self.queue_text("Usage: set-text <per|eng> <text>\n", "#ff5555")
                return
            self.save_config()
        else: self.queue_text("Usage: set-text <per|eng> <text>\n", "#ff5555")

    def handle_exit_cmd(self, args=None):
        self.pending_commands.clear()
        self.save_config()
        self.queue_text("Saving session... completed.\n", "#8b949e")
        QTimer.singleShot(1500, self.close)

    def execute_sys_list(self, args=None):
        self.queue_text("Scanning system-wide fonts...\n", "#e3b341", immediate=True)
        families = QFontDatabase().families()
        for f in sorted(families)[:100]:
            self.queue_text(f"  - {f}\n", "#8b949e", immediate=True)
        self.queue_text(
            f"\nFound {len(families)} families total. (Shown first 100)\n",
            "#58a6ff",
            immediate=True,
        )

    def print_header(self):
        now = datetime.datetime.now().strftime("%a %b %d %H:%M:%S")
        self.queue_text(f"Last login: {now} on ttys001\n\n", "#8b949e")

    def show_menu(self):
        menu_text = (
            "Welcome to Soheil_OS v2.1. Safe & Modular.\n"
            "Type 'help' to see available commands.\n\n"
        )
        self.queue_text(menu_text, "#58a6ff")

    def append_output(self, text, color):
        if not text:
            return
        cursor = self.output_area.textCursor()
        cursor.movePosition(QTextCursor.End)
        fmt = cursor.charFormat()
        fmt.setForeground(QColor(color))
        cursor.setCharFormat(fmt)
        cursor.insertText(text)
        self.output_area.verticalScrollBar().setValue(
            self.output_area.verticalScrollBar().maximum()
        )

    def queue_text(self, text, color="#8b949e", immediate=False):
        if immediate and not self.output_is_busy():
            self.append_output(text, color)
            return
        self.msg_queue.append((text, color))

    def output_is_busy(self):
        return bool(self.current_msg_text or self.msg_queue)

    def process_pending_commands(self):
        """Run commands entered while typewriter output was still rendering."""
        if self.output_is_busy() or self.current_state == "FONT_LOADING" or not self.pending_commands:
            return

        raw_cmd = self.pending_commands.pop(0)
        self._process_command_now(raw_cmd)
        if self.pending_commands and not self.output_is_busy():
            QTimer.singleShot(0, self.process_pending_commands)

    def process_typewriter(self):
        if not self.current_msg_text:
            if not self.msg_queue:
                self.process_pending_commands()
                return 
            
            next_chunk, color = self.msg_queue.popleft()
            self.current_msg_text = list(next_chunk) 
            self.current_msg_color = color
            
        if self.current_msg_text:
            char = self.current_msg_text.pop(0)
            self.append_output(char, self.current_msg_color)

        if not self.current_msg_text and not self.msg_queue:
            self.process_pending_commands()

    def get_font_info(self, font_path):
        """Extracts the font family name using QFontDatabase."""
        font_id = QFontDatabase.addApplicationFont(str(font_path.absolute()))
        if font_id != -1:
            families = QFontDatabase.applicationFontFamilies(font_id)
            family = families[0] if families else font_path.stem
            QFontDatabase.removeApplicationFont(font_id)
            if families:
                return family
        return font_path.stem

    def update_theme(self, theme_name, manual=True):
        if theme_name not in self.themes:
            if manual: self.queue_text(f"Error: Theme '{theme_name}' not found.\n", "#ff5555")
            return
        
        self.current_theme = theme_name
        t = self.themes[theme_name]
        
        self.setStyleSheet(f"""
            QMainWindow, QWidget {{ background-color: {t['bg']}; }}
            QTextEdit {{
                background-color: {t['bg']};
                color: {t['fg']}; 
                font-family: 'Consolas', 'Menlo', 'Courier New';
                font-size: 14px;
                border: none;
            }}
            QLabel#prompt {{
                color: {t['prompt']}; 
                font-family: 'Consolas', 'Menlo', 'Courier New';
                font-size: 14px;
                font-weight: bold;
            }}
            QLineEdit {{
                background-color: {t['bg']};
                color: {t['input']}; 
                font-family: 'Consolas', 'Menlo', 'Courier New';
                font-size: 14px;
                border: none;
            }}
            QScrollBar:vertical {{ border: none; background: {t['bg']}; width: 10px; }}
            QScrollBar::handle:vertical {{ background: {t['border']}; min-height: 20px; border-radius: 5px; }}
        """)
        
        if manual:
            self.queue_text(f"Switched to theme: {theme_name.upper()}\n", t['input'])
            self.save_config()

    def execute_list_command(self):
        """Build and render the interactive folder list without animation delay."""
        self.font_list_timer.stop()
        self.font_list_queue.clear()
        self.input_line.setEnabled(True)
        if not ASSET_DIR.exists():
            self.queue_text("Error: 'asset/' directory not found.\n", "#ff5555", immediate=True)
            return

        # Always reset state before scanning to ensure we don't have stale maps
        self.folder_map.clear()
        self.loose_font_paths.clear()
        self.loose_font_file_count = 0
        self.font_map.clear()
        self.current_state = "MAIN"

        folders = sorted([d for d in ASSET_DIR.iterdir() if d.is_dir()], key=lambda x: x.name.lower())

        # A single directory walk is substantially faster than opening the
        # persistent index once per folder just to calculate display counts.
        all_font_files = FontManager.list_font_files(ASSET_DIR)
        folder_counts = {folder: 0 for folder in folders}
        all_loose = []
        for font in all_font_files:
            try:
                relative = font.relative_to(ASSET_DIR)
            except ValueError:
                continue
            if len(relative.parts) == 1:
                all_loose.append(font)
                continue
            top_level_folder = ASSET_DIR / relative.parts[0]
            if top_level_folder in folder_counts:
                folder_counts[top_level_folder] += 1

        # Deduplicate loose files in root; this is normally a very small set.
        loose_files = FontManager.deduplicate_fonts(all_loose)
        self.loose_font_file_count = len(all_loose)

        self.queue_text(
            f"Refreshing asset directory... found {len(folders)} folders and "
            f"{len(loose_files)} loose fonts.\n",
            "#8b949e",
            immediate=True,
        )

        index = 1
        if folders:
            for d in folders:
                count = folder_counts[d]
                self.queue_text(
                    f"  - [{index}] {d.name} ({count} files inside; identical duplicates may be hidden)\n",
                    "#58a6ff",
                    immediate=True,
                )
                self.folder_map[str(index)] = d
                index += 1

        if loose_files:
            self.loose_font_paths = loose_files
            self.queue_text(
                "\nLoose Fonts (in root asset folder):\n",
                "#e3b341",
                immediate=True,
            )
            self.queue_text(
                f"  - [{index}] Loose Fonts ({len(all_loose)} files; {len(loose_files)} unique shown)\n",
                "#58a6ff",
                immediate=True,
            )
            self.folder_map[str(index)] = None
            index += 1

        if folders or loose_files:
            self.queue_text(
                "\n[Select a folder or group number to view contents]\n",
                "#e3b341",
                immediate=True,
            )
            self.current_state = "FOLDER_SELECT"

        if not folders and not loose_files:
            self.queue_text(
                "\n[Asset folder is empty]\n\n",
                "#ff5555",
                immediate=True,
            )

    def list_folder_contents(self, folder_path):
        """Print the fonts inside a selected family folder."""
        fonts = FontManager.discover_fonts(folder_path, FONT_INDEX_FILE)
        self.list_font_contents(fonts, folder_path.name)

    def list_loose_font_contents(self):
        """Print the fonts in the virtual root-level Loose Fonts group."""
        self.list_font_contents(
            self.loose_font_paths,
            "Loose Fonts",
            physical_count=self.loose_font_file_count,
        )

    def list_font_contents(self, fonts, display_name, physical_count=None):
        """Render a font selection view for a real or virtual group."""
        self.font_list_timer.stop()
        self.font_list_queue.clear()
        self.font_map.clear()
        if not fonts:
            self.queue_text(
                f"  (Group '{display_name}' is empty or has no fonts)\n",
                "#ff5555",
                immediate=True,
            )
            self.current_state = "MAIN"
            self.input_line.setEnabled(True)
        else:
            duplicate_note = " Identical duplicate copies are hidden." if physical_count is None else ""
            count_note = ""
            if physical_count is not None and physical_count != len(fonts):
                count_note = f" ({physical_count} files, {len(fonts)} unique shown)"
            self.queue_text(
                f"Displaying proper names for fonts in '{display_name}'{count_note}:{duplicate_note}\n\n",
                "#e3b341",
                immediate=True,
            )
            self.current_state = "FONT_LOADING"
            self.input_line.setEnabled(False)
            self.font_list_queue = list(fonts)
            self.font_list_index = 0
            self.font_list_display_name = display_name
            self.font_list_timer.start()

    def process_font_list_batch(self):
        """Render selected-font rows in small batches to keep the terminal responsive."""
        batch_size = 8
        total = len(self.font_list_queue)
        for _ in range(batch_size):
            if self.font_list_index >= total:
                break
            font_path = self.font_list_queue[self.font_list_index]
            index = self.font_list_index + 1
            proper_name = self.get_font_info(font_path)
            locations = self.font_manager.installed_paths(font_path)
            status = f" [installed: {', '.join(locations)}]" if locations else " [missing]"
            self.queue_text(
                f"  - [{index}] {proper_name}{status}\n",
                "#58a6ff",
                immediate=True,
            )
            self.queue_text(
                f"      file: {font_path.name} [{font_path.suffix.upper()[1:]}]\n",
                "#484f58",
                immediate=True,
            )
            self.font_map[str(index)] = font_path
            self.font_list_index += 1

        if self.font_list_index < total:
            return

        self.font_list_timer.stop()
        self.font_list_queue.clear()
        self.queue_text(
            "\n[Commands: preview <n> | info <n> | compare <n1> <n2> | "
            "install <n...> [--user|--system] | uninstall <n> | back]\n",
            "#e3b341",
            immediate=True,
        )
        self.current_state = "FONT_SELECT"
        self.input_line.setEnabled(True)
        self.input_line.setFocus()
        if self.pending_commands:
            QTimer.singleShot(0, self.process_pending_commands)

    def open_catalog(self, args=None, initial_query=""):
        if args:
            self.queue_text("Usage: catalog\n", "#ff5555")
            return
        if self.worker and self.worker.isRunning():
            self.queue_text("Finish or cancel the active operation before opening the catalog.\n", "#e3b341")
            return
        dialog = FontCatalogDialog([], self, initial_query=initial_query)
        dialog.start_indexing()
        dialog.exec_()

    def open_duplicate_manager(self, args=None):
        if args:
            self.queue_text("Usage: duplicates\n", "#ff5555")
            return
        if self.worker and self.worker.isRunning():
            self.queue_text("Finish or cancel the active operation before managing duplicates.\n", "#e3b341")
            return
        dialog = DuplicateManagerDialog(self, self)
        dialog.exec_()

    def show_font_info(self, font_path):
        valid, reason = FontManager.validate_font_file(font_path)
        try:
            size = font_path.stat().st_size
            digest = self.font_manager.file_digest(font_path)[:16]
        except OSError as error:
            self.queue_text(f"Unable to read {font_path.name}: {error}\n", "#ff5555")
            return

        locations = self.font_manager.installed_paths(font_path)
        installed = ", ".join(locations) if locations else "not installed"
        self.queue_text(
            f"Font information: {self.get_font_info(font_path)}\n"
            f"  File: {font_path.name}\n"
            f"  Format: {font_path.suffix.upper()[1:]}\n"
            f"  Size: {size:,} bytes\n"
            f"  SHA-256: {digest}...\n"
            f"  Validation: {reason}\n"
            f"  Installed: {installed}\n",
            "#58a6ff" if valid else "#ff5555",
        )

    def process_command(self):
        raw_cmd = self.input_line.text().strip()
        if not raw_cmd: return

        if self.output_is_busy() or self.pending_commands:
            if len(self.pending_commands) >= 20:
                self.input_line.clear()
                self.queue_text(
                    "Too many commands are queued; wait for the current output to finish.\n",
                    "#e3b341",
                )
                return
            self.pending_commands.append(raw_cmd)
            self.input_line.clear()
            self.queue_text(
                f"\n[Queued until current output finishes: {raw_cmd}]\n",
                "#8b949e",
            )
            return

        self._process_command_now(raw_cmd)

    def _process_command_now(self, raw_cmd):
        """Execute a command only after all earlier terminal output is complete."""
        
        parts = raw_cmd.split()
        cmd = parts[0].lower()
        args = parts[1:] if len(parts) > 1 else []

        # 1. Echo the command
        self.output_area.moveCursor(QTextCursor.End)
        prompt = html.escape(self.prompt_label.text())
        escaped_command = html.escape(raw_cmd)
        self.output_area.insertHtml(
            f"<span style='color:{self.themes[self.current_theme]['prompt']}'>{prompt}</span> "
            f"<span style='color:#fff'>{escaped_command}</span><br>"
        )
        self.input_line.clear()

        # Update History
        if not self.history or self.history[-1] != raw_cmd:
            self.history.append(raw_cmd)
        self.history_index = -1

        # 2. Check Context-Specific States
        if self.current_state == "FONT_LOADING":
            self.queue_text("The font list is still loading; please wait a moment.\n", "#e3b341")
            return

        if self.current_state == "FOLDER_SELECT":
            if cmd in self.folder_map:
                selected_group = self.folder_map[cmd]
                if selected_group is None:
                    self.list_loose_font_contents()
                else:
                    self.list_folder_contents(selected_group)
                return
            elif cmd == "back":
                self.current_state = "MAIN"
                self.queue_text("Returning to main menu...\n", "#8b949e")
                return
            elif cmd in {"preview", "info", "compare", "install", "uninstall"}:
                self.queue_text("Select a family folder before using font actions.\n", "#e3b341")
                return
            elif cmd.isdigit():
                self.trigger_error_feedback()
                self.queue_text(
                    f"Invalid folder/group number: {cmd}. Choose a number from the list above.\n",
                    "#ff5555",
                )
                return

        if self.current_state == "FONT_SELECT":
            if cmd == "install":
                self.handle_install_cmd(args)
                return
            elif cmd == "preview":
                if len(args) == 1 and args[0] in self.font_map:
                    f_path = self.font_map[args[0]]
                    dialog = FontPreviewDialog(f_path, self)
                    dialog.exec_()
                else:
                    self.queue_text("Usage: preview <font_number>\n", "#ff5555")
                return
            elif cmd == "info":
                if len(args) == 1 and args[0] in self.font_map:
                    self.show_font_info(self.font_map[args[0]])
                else:
                    self.queue_text("Usage: info <font_number>\n", "#ff5555")
                return
            elif cmd == "compare":
                if len(args) == 2 and args[0] in self.font_map and args[1] in self.font_map:
                    dialog = FontCompareDialog(self.font_map[args[0]], self.font_map[args[1]], self)
                    dialog.exec_()
                else:
                    self.queue_text("Usage: compare <valid_number> <valid_number>\n", "#ff5555")
                return
            elif cmd == "uninstall":
                if len(args) != 1 or args[0] not in self.font_map:
                    self.queue_text("Usage: uninstall <font_number>\n", "#ff5555")
                    return
                font_path = self.font_map[args[0]]
                answer = QMessageBox.question(
                    self,
                    "Confirm font removal",
                    f"Remove matching installed copies of '{font_path.name}'?",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No,
                )
                if answer == QMessageBox.Yes:
                    self.run_thread("uninstall", font_path)
                return
            elif cmd == "back":
                self.current_state = "MAIN"
                self.queue_text("Returning to main menu...\n", "#8b949e")
                return
            elif cmd.isdigit():
                self.trigger_error_feedback()
                self.queue_text(
                    "Use a font action, for example: preview 1, info 1, or install 1.\n",
                    "#e3b341",
                )
                return

        if self.current_state == "MAIN" and cmd in {
            "preview", "info", "compare", "install", "uninstall", "back"
        }:
            self.queue_text("Use 'list' and select a family before using that command.\n", "#e3b341")
            return

        # 3. Dynamic Command Resolution (Main Menu)
        # Check if input is a number
        if cmd.isdigit():
            idx = int(cmd) - 1
            if 0 <= idx < len(self.main_commands):
                self.main_commands[idx]["func"]()
                return
            else:
                self.trigger_error_feedback()
                self.queue_text(f"Error: Index {cmd} out of range.\n", "#ff5555")
                return
        
        # Check if input matches a command name
        for entry in self.main_commands:
            if cmd == entry["cmd"]:
                # Some functions might need args (like theme or set-text)
                import inspect
                sig = inspect.signature(entry["func"])
                if "args" in sig.parameters:
                    entry["func"](args)
                else:
                    entry["func"]()
                return

        # 4. Built-in Terminal Commands
        if cmd == "help":
            self.show_help()
             
        elif cmd == "clear" or cmd == "cls":
            self.output_area.clear()
        
        else:
            self.trigger_error_feedback()
            self.queue_text(f"zsh: command not found: {cmd}\n", "#ff5555")

    def run_thread(
        self,
        task_type,
        target=None,
        scope=None,
        selected_fonts=None,
        on_finished=None,
        on_update=None,
    ):
        if self.worker and self.worker.isRunning():
            self.queue_text("A background operation is already running. Use 'cancel' first.\n", "#e3b341")
            return

        worker = WorkerThread(
            task_type,
            target,
            scope or self.install_scope,
            selected_fonts,
        )
        self.worker = worker
        self.worker_callback = on_finished
        self.worker_update_callback = on_update
        worker.update_signal.connect(self.handle_worker_update)
        worker.finished_signal.connect(self._handle_worker_finished)
        worker.start()

    def handle_worker_update(self, text, color):
        """Show worker output immediately and mirror progress to active dialogs."""
        self.queue_text(text, color, immediate=True)
        if self.worker_update_callback:
            self.worker_update_callback(text, color)

    def _handle_worker_finished(self):
        worker = self.worker
        if worker is None:
            return
        self._worker_finished(worker, self.worker_callback)

    def _worker_finished(self, worker, on_finished=None):
        self.queue_text("\n", "#fff")
        self.font_manager.invalidate_installed_index()
        if self.worker is worker:
            self.worker = None
            self.worker_callback = None
            self.worker_update_callback = None
        if on_finished:
            on_finished()
        worker.deleteLater()

    def closeEvent(self, event):
        self.save_config()
        if self.worker and self.worker.isRunning():
            self.worker.requestInterruption()
            if not self.worker.wait(3000):
                self.queue_text("Please wait for the background operation to stop.\n", "#e3b341")
                event.ignore()
                return
        self.font_list_timer.stop()
        self.font_list_queue.clear()
        event.accept()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = TerminalWindow()
    window.show()
    sys.exit(app.exec_())
