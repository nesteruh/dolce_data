"""Spotlight-style popup UI for Dolce Data — PyQt6."""
from __future__ import annotations

import os
import re
import sys
from dotenv import load_dotenv
from openai import OpenAI

from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout,
    QLineEdit, QTextEdit, QLabel, QFrame, QPushButton,
)
from PyQt6.QtCore import (
    Qt, QThread, pyqtSignal, QObject, QTimer, QRectF,
)
from PyQt6.QtGui import (
    QColor, QPainter, QPainterPath, QFont, QTextCursor,
    QTextCharFormat, QKeySequence, QShortcut,
)

load_dotenv()

# ── Design tokens ──────────────────────────────────────────────────────────────
_BG_ALPHA  = (28, 28, 30, 238)   # #1c1c1e at ~93% opacity
_CARD_HEX  = "#2c2c2e"
_TEXT      = "#f2f2f7"
_DIM       = "#8e8e93"
_ACCENT    = "#0a84ff"
_BORDER    = "#48484a"
_GREEN     = "#30d158"
_ORANGE    = "#ff9f0a"
_RED       = "#ff453a"
_RADIUS    = 14

_IS_MAC    = sys.platform == "darwin"
_FONT_UI   = "SF Pro Display" if _IS_MAC else "Helvetica Neue"
_FONT_MONO = "SF Mono"         if _IS_MAC else "Courier"
_SPIN      = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

_RISK_CLR  = {"LOW": _GREEN, "MEDIUM": _ORANGE, "HIGH": _RED, "CRITICAL": _RED}

_QSS = f"""
* {{ background: transparent; color: {_TEXT}; font-family: "{_FONT_UI}"; }}
QLineEdit {{
    border: none; color: {_TEXT}; font-size: 20px;
    selection-background-color: {_ACCENT};
}}
QLineEdit[placeholder="1"] {{ color: {_DIM}; }}
QTextEdit {{
    border: none; color: {_TEXT};
    font-family: "{_FONT_MONO}"; font-size: 13px;
    selection-background-color: {_ACCENT};
}}
QScrollBar:vertical {{
    background: transparent; width: 5px; margin: 0;
}}
QScrollBar::handle:vertical {{
    background: {_BORDER}; border-radius: 2px; min-height: 24px;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QPushButton {{
    border: none; color: {_ACCENT}; font-size: 12px;
    padding: 2px 10px; background: transparent;
}}
QPushButton:hover {{ color: white; }}
"""


# ── Text cleaning ──────────────────────────────────────────────────────────────

def _clean(text: str) -> str:
    # Remove machine-readable ACTION_ lines — users see the Run buttons instead
    text = re.sub(r"^ACTION_\w+:.*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"\[/?[^\]]+\]", "", text)                          # Rich tags
    text = re.sub(r"^#{1,6}\s+", "  ", text, flags=re.MULTILINE)     # MD headers
    text = re.sub(r"\*{1,3}([^*\n]+)\*{1,3}", r"\1", text)           # Bold/italic
    # Only strip _word_ italic patterns; leave bare underscores in names/types alone
    text = re.sub(r"(?<!\w)_{1,2}(\w[^_\n]*\w)_{1,2}(?!\w)", r"\1", text)
    text = re.sub(r"^\s*[-*]\s+", "  • ", text, flags=re.MULTILINE)  # Lists
    text = re.sub(r"\n{3,}", "\n\n", text)                            # Collapse blank lines
    return text.strip()


# ── Background worker ──────────────────────────────────────────────────────────

class _Worker(QObject):
    done  = pyqtSignal(object)
    error = pyqtSignal(str)

    def __init__(self, query: str, client, model: str, token_buf: list) -> None:
        super().__init__()
        self.query, self.client, self.model = query, client, model
        self._token_buf = token_buf

    def run(self) -> None:
        from src.router import handle
        try:
            result = handle(
                self.query, self.client, self.model, verbose=False,
                on_token=self._token_buf.append,
            )
            self.done.emit(result)
        except Exception as exc:
            self.error.emit(str(exc))


# ── Main window ────────────────────────────────────────────────────────────────

class SpotlightWindow(QWidget):
    W      = 700
    H_BAR  = 68
    H_FULL = 530

    def __init__(self, client, model: str) -> None:
        super().__init__()
        self.client = client
        self.model  = model
        self._thread: QThread | None  = None
        self._worker: _Worker | None  = None
        self._spin_idx  = 0
        self._drag_pos  = None
        self._token_buf: list = []

        flags = (
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setStyleSheet(_QSS)
        self.resize(self.W, self.H_BAR)
        self._center()

        self._build()
        self._collapse()

        QShortcut(QKeySequence("Escape"), self, self._on_escape)

    def _center(self) -> None:
        screen = QApplication.primaryScreen().availableGeometry()
        x = (screen.width() - self.W) // 2
        y = int(screen.height() * 0.22)
        self.move(x, y)

    # ── Layout ─────────────────────────────────────────────────────────────────

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Search bar
        bar = QHBoxLayout()
        bar.setContentsMargins(16, 0, 14, 0)
        bar.setSpacing(0)

        self._icon = QLabel("⌘")
        self._icon.setFont(QFont(_FONT_UI, 20, QFont.Weight.Bold))
        self._icon.setStyleSheet(f"color: {_DIM};")
        self._icon.setFixedSize(34, self.H_BAR)
        bar.addWidget(self._icon)
        bar.addSpacing(6)

        self._input = QLineEdit()
        self._input.setFont(QFont(_FONT_UI, 20))
        self._input.setFixedHeight(self.H_BAR)
        self._input.returnPressed.connect(self._on_submit)
        self._input.textChanged.connect(self._on_text_changed)
        self._set_placeholder()
        bar.addWidget(self._input, 1)

        self._spin_lbl = QLabel("")
        self._spin_lbl.setFont(QFont(_FONT_UI, 16))
        self._spin_lbl.setStyleSheet(f"color: {_DIM};")
        self._spin_lbl.setFixedWidth(24)
        self._spin_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        bar.addWidget(self._spin_lbl)
        bar.addSpacing(4)

        close_btn = QLabel("✕")
        close_btn.setFont(QFont(_FONT_UI, 13))
        close_btn.setStyleSheet(f"color: {_DIM};")
        close_btn.setFixedSize(22, 22)
        close_btn.setAlignment(Qt.AlignmentFlag.AlignCenter)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.mousePressEvent = lambda _: QApplication.quit()
        bar.addWidget(close_btn)

        bar_widget = QWidget()
        bar_widget.setFixedHeight(self.H_BAR)
        bar_widget.setLayout(bar)
        root.addWidget(bar_widget)

        # Separator
        self._sep = QFrame()
        self._sep.setFrameShape(QFrame.Shape.HLine)
        self._sep.setStyleSheet(f"color: {_BORDER};")
        self._sep.setFixedHeight(1)
        root.addWidget(self._sep)

        # Results text
        self._text = QTextEdit()
        self._text.setReadOnly(True)
        self._text.setFont(QFont(_FONT_MONO, 13))
        self._text.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        root.addWidget(self._text, 1)

        # Actions panel
        self._act_panel = QWidget()
        self._act_layout = QVBoxLayout(self._act_panel)
        self._act_layout.setContentsMargins(16, 4, 16, 12)
        self._act_layout.setSpacing(4)
        root.addWidget(self._act_panel)

        # Poll timer — drains token buffer every 50 ms so text appears incrementally
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(50)
        self._poll_timer.timeout.connect(self._drain_tokens)

        # Spinner timer
        self._spin_timer = QTimer(self)
        self._spin_timer.timeout.connect(self._tick_spinner)

    # ── Placeholder ────────────────────────────────────────────────────────────

    def _set_placeholder(self) -> None:
        self._input.setPlaceholderText("Ask anything about your Mac…")
        self._input.setProperty("placeholder", "1")
        self._input.style().unpolish(self._input)
        self._input.style().polish(self._input)

    def _on_text_changed(self, text: str) -> None:
        has = bool(text.strip())
        self._input.setProperty("placeholder", "0" if has else "1")
        self._input.style().unpolish(self._input)
        self._input.style().polish(self._input)

    # ── Expand / collapse ──────────────────────────────────────────────────────

    def _expand(self) -> None:
        self._sep.show()
        self._text.show()
        self.resize(self.W, self.H_FULL)

    def _collapse(self) -> None:
        self._sep.hide()
        self._text.hide()
        self._act_panel.hide()
        self.resize(self.W, self.H_BAR)

    # ── Events ─────────────────────────────────────────────────────────────────

    def _on_escape(self) -> None:
        if self._thread and self._thread.isRunning():
            return
        self._collapse()
        self._input.clear()
        self._input.setFocus()

    def _on_submit(self) -> None:
        query = self._input.text().strip()
        if not query or (self._thread and self._thread.isRunning()):
            return
        self._input.clear()
        self._collapse()
        self._text.clear()
        self._start_spinner()

        self._token_buf.clear()
        self._worker = _Worker(query, self.client, self.model, self._token_buf)
        self._thread = QThread()
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.done.connect(self._on_result)
        self._worker.error.connect(self._on_error)
        self._worker.done.connect(self._thread.quit)
        self._worker.error.connect(self._thread.quit)
        self._poll_timer.start()
        self._thread.start()

    # ── Result rendering ───────────────────────────────────────────────────────

    def _drain_tokens(self) -> None:
        n = len(self._token_buf)
        if not n:
            return
        text = "".join(self._token_buf[:n])
        del self._token_buf[:n]
        if not self._text.isVisible():
            self._stop_spinner()
            self._expand()
        self._text.insertPlainText(text)
        sb = self._text.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _on_result(self, result) -> None:
        self._poll_timer.stop()
        self._drain_tokens()  # flush any tokens that arrived before done fired
        self._stop_spinner()
        self._expand()

        self._text.clear()
        cursor = self._text.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)

        def _fmt(color: str | None = None, bold: bool = False) -> QTextCharFormat:
            f = QTextCharFormat()
            if color:
                f.setForeground(QColor(color))
            if bold:
                f.setFontWeight(QFont.Weight.Bold)
            return f

        cursor.insertText(f"  {result.agent}\n", _fmt(_DIM))
        cursor.insertText("─" * 60 + "\n\n", _fmt(_DIM))
        cursor.insertText(_clean(result.full_response) + "\n", _fmt())

        self._text.setTextCursor(cursor)
        self._text.verticalScrollBar().setValue(0)

        actions = getattr(result, "actions", None) or []
        if actions:
            self._show_actions(actions)

    def _on_error(self, msg: str) -> None:
        self._stop_spinner()
        self._expand()
        cursor = self._text.textCursor()

        def _fmt(color: str | None = None) -> QTextCharFormat:
            f = QTextCharFormat()
            if color:
                f.setForeground(QColor(color))
            return f

        cursor.insertText("⚠  Error\n\n", _fmt(_RED))
        cursor.insertText(f"{msg}\n\n", _fmt())
        cursor.insertText("Make sure Ollama is running: ollama serve", _fmt(_DIM))
        self._text.setTextCursor(cursor)

    # ── Actions ────────────────────────────────────────────────────────────────

    def _show_actions(self, actions: list) -> None:
        try:
            from src.actions import ActionExecutor
        except ImportError:
            return

        # Clear old rows
        while self._act_layout.count():
            item = self._act_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        executor = ActionExecutor()

        for action in actions:
            color = _RISK_CLR.get(action.risk, _TEXT)
            desc  = getattr(action, "description", str(action))
            desc  = (desc[:58] + "…") if len(desc) > 58 else desc

            row = QWidget()
            row.setStyleSheet(f"background: {_CARD_HEX}; border-radius: 6px;")
            rl  = QHBoxLayout(row)
            rl.setContentsMargins(10, 5, 6, 5)
            rl.setSpacing(6)

            risk_lbl = QLabel(action.risk)
            risk_lbl.setFont(QFont(_FONT_UI, 11))
            risk_lbl.setStyleSheet(f"color: {color}; background: transparent;")
            risk_lbl.setFixedWidth(72)
            rl.addWidget(risk_lbl)

            desc_lbl = QLabel(desc)
            desc_lbl.setFont(QFont(_FONT_UI, 12))
            desc_lbl.setStyleSheet(f"color: {_TEXT}; background: transparent;")
            rl.addWidget(desc_lbl, 1)

            run_btn = QPushButton("Run ›")
            run_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            run_btn.clicked.connect(
                lambda _, a=action, ex=executor: self._execute(a, ex)
            )
            rl.addWidget(run_btn)

            self._act_layout.addWidget(row)

        self._act_panel.show()

    def _execute(self, action, executor) -> None:
        if action.risk in ("HIGH", "CRITICAL"):
            if not self._confirm(action.risk):
                return
        ar   = executor.execute(action)
        msg  = ar.output if ar.success else (ar.error or "No output")
        icon = "✓" if ar.success else "✗"
        clr  = _GREEN if ar.success else _RED

        cursor = self._text.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(clr))
        cursor.insertText(f"\n{icon}  {msg}\n", fmt)
        self._text.setTextCursor(cursor)
        self._text.ensureCursorVisible()

    def _confirm(self, risk: str) -> bool:
        from PyQt6.QtWidgets import QDialog
        dlg = QDialog(self, Qt.WindowType.FramelessWindowHint)
        dlg.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        dlg.setModal(True)

        outer = QVBoxLayout(dlg)
        outer.setContentsMargins(0, 0, 0, 0)

        card = QWidget()
        card.setStyleSheet(
            f"background: {_CARD_HEX}; border-radius: 10px;"
            f"color: {_TEXT};"
        )
        inner = QVBoxLayout(card)
        inner.setContentsMargins(24, 20, 24, 16)
        inner.setSpacing(10)

        title = QLabel(f"⚠  {risk} risk action")
        title.setFont(QFont(_FONT_UI, 14, QFont.Weight.Bold))
        title.setStyleSheet(f"color: {_RED}; background: transparent;")
        inner.addWidget(title)

        body = QLabel("Are you sure you want to run this?")
        body.setFont(QFont(_FONT_UI, 12))
        body.setStyleSheet(f"color: {_TEXT}; background: transparent;")
        inner.addWidget(body)

        btns = QHBoxLayout()
        cancel = QPushButton("Cancel")
        cancel.setStyleSheet(
            f"background: {_BORDER}; color: {_DIM}; border-radius: 6px;"
            "padding: 6px 16px;"
        )
        cancel.clicked.connect(dlg.reject)
        btns.addWidget(cancel)

        run_btn = QPushButton("Run")
        run_btn.setStyleSheet(
            f"background: {_RED}; color: white; border-radius: 6px;"
            "padding: 6px 16px; font-weight: bold;"
        )
        run_btn.clicked.connect(dlg.accept)
        btns.addWidget(run_btn)
        inner.addLayout(btns)

        outer.addWidget(card)
        return dlg.exec() == QDialog.DialogCode.Accepted

    # ── Spinner ────────────────────────────────────────────────────────────────

    def _start_spinner(self) -> None:
        self._spin_idx = 0
        self._spin_timer.start(80)

    def _tick_spinner(self) -> None:
        self._spin_lbl.setText(_SPIN[self._spin_idx % len(_SPIN)])
        self._spin_idx += 1

    def _stop_spinner(self) -> None:
        self._spin_timer.stop()
        self._spin_lbl.setText("")

    # ── Rounded background ─────────────────────────────────────────────────────

    def paintEvent(self, event) -> None:  # noqa: N802, ARG002
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), _RADIUS, _RADIUS)
        p.fillPath(path, QColor(*_BG_ALPHA))

    # ── Drag to move ───────────────────────────────────────────────────────────

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag_pos and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802, ARG002
        self._drag_pos = None


# ── Entry point ────────────────────────────────────────────────────────────────

def run() -> None:
    load_dotenv()
    client = OpenAI(
        base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1"),
        api_key =os.getenv("OLLAMA_API_KEY",  "ollama"),
    )
    model = os.getenv("AGENT_MODEL", "llama3.2")

    app = QApplication(sys.argv)
    app.setApplicationName("Dolce Data")

    win = SpotlightWindow(client, model)
    win.show()
    win._input.setFocus()

    sys.exit(app.exec())
