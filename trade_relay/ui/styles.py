"""
PyQt6 QSS dark theme for Trade Relay.
Windows: uses CJK-friendly fonts for Chinese UI.
Linux: uses system sans-serif for English UI.
"""
import platform


def _font_family() -> str:
    if platform.system() == "Windows":
        return '"Microsoft YaHei", "SimHei", "Arial"'
    return '"Segoe UI", "Ubuntu", "Noto Sans", "DejaVu Sans"'


def build_stylesheet() -> str:
    ff = _font_family()
    return f"""
/* ── Global ── */
* {{
    font-family: {ff};
    font-size: 13px;
}}

QMainWindow, QDialog, QWidget {{
    background-color: #0d1117;
    color: #e6edf3;
}}

/* ── Buttons ── */
QPushButton {{
    background-color: #21262d;
    color: #e6edf3;
    border: 1px solid #30363d;
    border-radius: 6px;
    padding: 6px 16px;
    min-height: 28px;
}}
QPushButton:hover {{ background-color: #30363d; border-color: #58a6ff; }}
QPushButton:pressed {{ background-color: #161b22; }}
QPushButton:disabled {{ color: #484f58; border-color: #21262d; }}

QPushButton#primary {{
    background-color: #1f6feb;
    border-color: #1f6feb;
    color: white;
}}
QPushButton#primary:hover {{ background-color: #388bfd; border-color: #388bfd; }}
QPushButton#primary:pressed {{ background-color: #1158c7; }}

QPushButton#primary_sm {{
    background-color: #1f6feb;
    border-color: #1f6feb;
    color: white;
    padding: 2px 10px;
    min-height: 22px;
    font-size: 12px;
}}
QPushButton#primary_sm:hover {{ background-color: #388bfd; border-color: #388bfd; }}
QPushButton#primary_sm:disabled {{
    background-color: #21262d;
    border-color: #30363d;
    color: #484f58;
}}

QPushButton#danger_sm {{
    background-color: #da3633;
    border-color: #da3633;
    color: white;
    padding: 2px 10px;
    min-height: 22px;
    font-size: 12px;
}}
QPushButton#danger_sm:hover {{ background-color: #f85149; }}
QPushButton#danger_sm:disabled {{
    background-color: #21262d;
    border-color: #30363d;
    color: #484f58;
}}

QPushButton#danger {{
    background-color: #da3633;
    border-color: #da3633;
    color: white;
}}
QPushButton#danger:hover {{ background-color: #f85149; }}

/* ── Input ── */
QLineEdit {{
    background-color: #161b22;
    color: #e6edf3;
    border: 1px solid #30363d;
    border-radius: 6px;
    padding: 6px 10px;
    min-height: 28px;
}}
QLineEdit:focus {{ border-color: #58a6ff; }}
QLineEdit:disabled {{ color: #484f58; background-color: #0d1117; }}

/* ── ComboBox ── */
QComboBox {{
    background-color: #161b22;
    color: #e6edf3;
    border: 1px solid #30363d;
    border-radius: 6px;
    padding: 6px 10px;
    min-height: 28px;
}}
QComboBox:focus {{ border-color: #58a6ff; }}
QComboBox::drop-down {{ border: none; width: 24px; }}
QComboBox::down-arrow {{ width: 10px; height: 10px; }}
QComboBox QAbstractItemView {{
    background-color: #161b22;
    color: #e6edf3;
    border: 1px solid #30363d;
    selection-background-color: #1f6feb;
    outline: none;
}}

/* ── Labels ── */
QLabel {{ color: #e6edf3; }}

/* ── Tab ── */
QTabWidget::pane {{
    border: 1px solid #30363d;
    background-color: #0d1117;
    top: -1px;
}}
QTabBar::tab {{
    background-color: #161b22;
    color: #8b949e;
    border: 1px solid #30363d;
    border-bottom: none;
    padding: 8px 22px;
    margin-right: 2px;
}}
QTabBar::tab:selected {{
    background-color: #0d1117;
    color: #e6edf3;
    border-top: 2px solid #58a6ff;
}}
QTabBar::tab:hover:!selected {{ color: #e6edf3; background-color: #21262d; }}

/* ── Table ── */
QTableWidget {{
    background-color: #0d1117;
    color: #e6edf3;
    border: 1px solid #30363d;
    gridline-color: #21262d;
    selection-background-color: #1f3a5f;
    outline: none;
}}
QTableWidget QHeaderView::section {{
    background-color: #161b22;
    color: #8b949e;
    border: none;
    border-right: 1px solid #30363d;
    border-bottom: 1px solid #30363d;
    padding: 6px 10px;
    font-weight: bold;
}}
QTableWidget::item {{ padding: 4px 8px; border-bottom: 1px solid #21262d; }}
QTableWidget::item:selected {{ background-color: #1f3a5f; }}

/* ── GroupBox ── */
QGroupBox {{
    border: 1px solid #30363d;
    border-radius: 6px;
    margin-top: 14px;
    padding-top: 6px;
    color: #58a6ff;
    font-weight: bold;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 4px;
}}

/* ── CheckBox ── */
QCheckBox {{ color: #e6edf3; spacing: 8px; }}
QCheckBox::indicator {{
    width: 16px; height: 16px;
    border: 1px solid #30363d;
    border-radius: 3px;
    background-color: #161b22;
}}
QCheckBox::indicator:checked {{
    background-color: #1f6feb;
    border-color: #1f6feb;
}}

/* ── ScrollBar ── */
QScrollBar:vertical {{
    background-color: #161b22;
    width: 8px;
    border: none;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background-color: #30363d;
    border-radius: 4px;
    min-height: 20px;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar:horizontal {{
    background-color: #161b22;
    height: 8px;
    border: none;
}}
QScrollBar::handle:horizontal {{
    background-color: #30363d;
    border-radius: 4px;
    min-width: 20px;
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}

/* ── StatusBar ── */
QStatusBar {{
    background-color: #161b22;
    color: #8b949e;
    border-top: 1px solid #30363d;
}}

/* ── Splitter ── */
QSplitter::handle {{ background-color: #30363d; }}

/* ── Order form: symbol bar ── */
QWidget#sym_bar {{
    background-color: #161b22;
    border-bottom: 1px solid #21262d;
}}

/* ── Order form: tag buttons (全仓 / 100x / BBO …) ── */
QPushButton#tag_btn {{
    background-color: #21262d;
    color: #8b949e;
    border: 1px solid #30363d;
    border-radius: 4px;
    padding: 2px 8px;
    font-size: 12px;
    min-height: 22px;
}}
QPushButton#tag_btn:hover {{ color: #e6edf3; border-color: #58a6ff; }}

/* ── Order form: icon-only button (↺) ── */
QPushButton#icon_btn {{
    background-color: transparent;
    color: #8b949e;
    border: 1px solid #30363d;
    border-radius: 4px;
    padding: 0;
    font-size: 14px;
}}
QPushButton#icon_btn:hover {{ color: #58a6ff; border-color: #58a6ff; }}

/* ── Direction tab (开仓 / 平仓) ── */
QPushButton#tab_dir {{
    background: transparent;
    border: none;
    border-bottom: 2px solid transparent;
    color: #8b949e;
    padding: 8px 20px;
    font-size: 14px;
    border-radius: 0;
}}
QPushButton#tab_dir:checked {{
    color: #e6edf3;
    border-bottom: 2px solid #e6edf3;
}}
QPushButton#tab_dir:hover:!checked {{ color: #c9d1d9; }}

/* ── Order type tab (限价 / 市价 / 条件委托) ── */
QPushButton#tab_type {{
    background: transparent;
    border: none;
    border-bottom: 2px solid transparent;
    color: #8b949e;
    padding: 6px 12px;
    font-size: 12px;
    border-radius: 0;
}}
QPushButton#tab_type:checked {{
    color: #f0c040;
    border-bottom: 2px solid #f0c040;
}}
QPushButton#tab_type:hover:!checked {{ color: #c9d1d9; }}

/* ── Trade inputs (price / qty) ── */
QLineEdit#trade_input {{
    background-color: #21262d;
    color: #e6edf3;
    border: 1px solid #30363d;
    border-radius: 6px;
    padding: 6px 10px;
    font-size: 14px;
}}
QLineEdit#trade_input:focus {{ border-color: #58a6ff; }}
QLineEdit#trade_input:disabled {{
    color: #484f58;
    background-color: #161b22;
}}

/* ── Long button (开多) ── */
QPushButton#long_btn {{
    background-color: #2ea043;
    border: none;
    border-radius: 6px;
    color: white;
    font-size: 15px;
    font-weight: bold;
}}
QPushButton#long_btn:hover {{ background-color: #3fb950; }}
QPushButton#long_btn:pressed {{ background-color: #238636; }}
QPushButton#long_btn:disabled {{ background-color: #21262d; color: #484f58; }}

/* ── Short button (开空) ── */
QPushButton#short_btn {{
    background-color: #b91c1c;
    border: none;
    border-radius: 6px;
    color: white;
    font-size: 15px;
    font-weight: bold;
}}
QPushButton#short_btn:hover {{ background-color: #ef4444; }}
QPushButton#short_btn:pressed {{ background-color: #991b1b; }}
QPushButton#short_btn:disabled {{ background-color: #21262d; color: #484f58; }}

/* ── Percentage slider ── */
QSlider#pct_slider::groove:horizontal {{
    height: 4px;
    background: #30363d;
    border-radius: 2px;
}}
QSlider#pct_slider::sub-page:horizontal {{
    background: #58a6ff;
    border-radius: 2px;
}}
QSlider#pct_slider::handle:horizontal {{
    background: #e6edf3;
    border: 2px solid #58a6ff;
    width: 12px;
    height: 12px;
    border-radius: 7px;
    margin: -5px 0;
}}

/* ── Notice bar ── */
QLabel#notice_bar {{
    background: #161b22;
    color: #8b949e;
    border-top: 1px solid #21262d;
    font-size: 12px;
    padding: 2px 8px;
}}

/* ── Positions panel tab buttons ── */
QPushButton#pos_tab_btn {{
    background: transparent;
    border: none;
    border-bottom: 2px solid transparent;
    border-radius: 0;
    color: #8b949e;
    font-size: 13px;
    padding: 6px 16px;
    min-height: 34px;
}}
QPushButton#pos_tab_btn:hover {{ color: #e6edf3; }}
QPushButton#pos_tab_btn:checked {{
    color: #e6edf3;
    border-bottom: 2px solid #58a6ff;
    font-weight: bold;
}}
"""
