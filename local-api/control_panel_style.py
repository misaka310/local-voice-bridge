from __future__ import annotations


PANEL_STYLE = """
QFrame#panel-card {
    background: rgba(10, 12, 18, 238);
    border: 1px solid rgba(120, 180, 255, 70);
    border-radius: 14px;
}
QLabel { color: #f5f7ff; font: 12px 'Segoe UI'; }
QLabel#panel-title { font-size: 14px; font-weight: 700; }
QLabel#panel-status { color: #9fd0ff; font-weight: 600; }
QLabel#panel-current-text { color: #c8d2e8; }
QLabel#panel-queue { color: #8792a8; font-size: 11px; }
QPushButton, QComboBox {
    color: #f5f7ff;
    background: rgba(255, 255, 255, 18);
    border: 1px solid rgba(255, 255, 255, 35);
    border-radius: 8px;
    padding: 5px 8px;
}
QPushButton:hover, QComboBox:hover { background: rgba(255, 255, 255, 30); }
QPushButton:checked { background: rgba(73, 168, 113, 80); border-color: rgba(73, 168, 113, 150); }
QPushButton:disabled { color: #667085; background: rgba(255, 255, 255, 8); }
QPushButton#panel-hide { padding: 0; font-size: 17px; }
QComboBox QAbstractItemView { background: #171b25; color: #f5f7ff; selection-background-color: #2f6feb; }
QSlider::groove:horizontal { height: 4px; background: #3a4252; border-radius: 2px; }
QSlider::handle:horizontal { width: 14px; margin: -5px 0; border-radius: 7px; background: #8fc7ff; }
"""
