"""Spotlight-style popup UI for Dolce Data — PyQt6."""
from __future__ import annotations

import io
import os
import re
import sys
import tempfile
import threading
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

    def __init__(self, query: str, client, model: str) -> None:
        super().__init__()
        self.query, self.client, self.model = query, client, model

    def run(self) -> None:
        from src.router import handle
        try:
            result = handle(self.query, self.client, self.model, verbose=False)
            self.done.emit(result)
        except Exception as exc:
            self.error.emit(str(exc))


# ── Local Whisper model (loaded once, reused across calls) ─────────────────────

_whisper_instance = None

def _get_whisper_model():
    global _whisper_instance
    if _whisper_instance is None:
        import warnings
        warnings.filterwarnings("ignore", message=".*unauthenticated.*HF Hub.*")
        from faster_whisper import WhisperModel
        _whisper_instance = WhisperModel(
            "base", device="cpu", compute_type="int8"
        )
    return _whisper_instance


# ── Voice transcription worker ─────────────────────────────────────────────────

class _VoiceWorker(QObject):
    transcribed = pyqtSignal(str)
    error       = pyqtSignal(str)

    def __init__(self, frames: list, sample_rate: int) -> None:
        super().__init__()
        self._frames      = frames
        self._sample_rate = sample_rate

    def run(self) -> None:
        try:
            import numpy as np
            import soundfile as sf
        except ImportError as exc:
            self.error.emit(f"Missing package: {exc}. Run: pip install faster-whisper sounddevice soundfile")
            return

        audio = np.concatenate(self._frames, axis=0)
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            sf.write(f.name, audio, self._sample_rate)
            tmppath = f.name

        try:
            model = _get_whisper_model()
            segments, _ = model.transcribe(tmppath, beam_size=1)
            text = " ".join(seg.text for seg in segments).strip()
            self.transcribed.emit(text)
        except Exception as exc:
            self.error.emit(str(exc))
        finally:
            os.unlink(tmppath)


# ── Main window ────────────────────────────────────────────────────────────────

class SpotlightWindow(QWidget):
    W      = 700
    H_BAR  = 68
    H_FULL = 530

    def __init__(self, client, model: str) -> None:
        super().__init__()
        self.client = client
        self.model  = model
        self._thread: QThread | None        = None
        self._worker: _Worker | None        = None
        self._voice_thread: QThread | None  = None
        self._voice_worker: _VoiceWorker | None = None
        self._recording     = False
        self._rec_frames: list = []
        self._rec_stream    = None
        self._sample_rate   = 16000
        self._spin_idx  = 0
        self._drag_pos  = None

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

        self._mic_btn = QLabel("🎙")
        self._mic_btn.setFont(QFont(_FONT_UI, 15))
        self._mic_btn.setStyleSheet(f"color: {_DIM};")
        self._mic_btn.setFixedSize(26, 26)
        self._mic_btn.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._mic_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._mic_btn.setToolTip("Click to start/stop voice input")
        self._mic_btn.mousePressEvent = lambda _: self._toggle_voice()
        bar.addWidget(self._mic_btn)
        bar.addSpacing(6)

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

        self._worker = _Worker(query, self.client, self.model)
        self._thread = QThread()
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.done.connect(self._on_result)
        self._worker.error.connect(self._on_error)
        self._worker.done.connect(self._thread.quit)
        self._worker.error.connect(self._thread.quit)
        self._thread.start()

    # ── Result rendering ───────────────────────────────────────────────────────

    def _on_result(self, result) -> None:
        self._stop_spinner()
        self._expand()

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

    # ── Voice input ────────────────────────────────────────────────────────────

    def _set_mic(self, emoji: str, placeholder: str | None = None) -> None:
        self._mic_btn.setText(emoji)
        if placeholder is not None:
            self._input.setPlaceholderText(placeholder)
        else:
            self._set_placeholder()

    def _toggle_voice(self) -> None:
        if self._recording:
            self._stop_recording()
        else:
            self._start_recording()

    def _start_recording(self) -> None:
        try:
            import sounddevice as sd
        except ImportError:
            self._on_voice_error("sounddevice not installed. Run: pip install sounddevice soundfile")
            return

        try:
            self._recording  = True
            self._rec_frames = []
            self._set_mic("🔴", "Listening…  (click 🔴 to stop)")

            def _cb(indata, frames, time, status):  # noqa: ARG001
                if self._recording:
                    self._rec_frames.append(indata.copy())

            self._rec_stream = sd.InputStream(
                samplerate=self._sample_rate, channels=1,
                dtype="float32", callback=_cb,
            )
            self._rec_stream.start()
        except Exception as exc:
            self._recording = False
            self._set_mic("🎙")
            self._on_voice_error(str(exc))

    def _stop_recording(self) -> None:
        self._recording = False
        if self._rec_stream:
            try:
                self._rec_stream.stop()
                self._rec_stream.close()
            except Exception:
                pass
            self._rec_stream = None

        if not self._rec_frames:
            self._set_mic("🎙")
            return

        self._set_mic("⏳", "Transcribing…")

        self._voice_worker = _VoiceWorker(self._rec_frames, self._sample_rate)
        self._voice_thread = QThread()
        self._voice_worker.moveToThread(self._voice_thread)
        self._voice_thread.started.connect(self._voice_worker.run)
        self._voice_worker.transcribed.connect(self._on_transcribed)
        self._voice_worker.error.connect(self._on_voice_error)
        self._voice_worker.transcribed.connect(self._voice_thread.quit)
        self._voice_worker.error.connect(self._voice_thread.quit)
        self._voice_thread.start()

    def _on_transcribed(self, text: str) -> None:
        self._set_mic("🎙")
        self._input.setText(text)
        self._input.setFocus()
        self._on_submit()

    def _on_voice_error(self, msg: str) -> None:
        self._set_mic("🎙")
        print(f"[voice] {msg}", flush=True)
        self._expand()
        cursor = self._text.textCursor()
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(_RED))
        cursor.insertText(f"⚠  Voice error: {msg}\n", fmt)
        self._text.setTextCursor(cursor)

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

    # Pre-load Whisper model in background so first mic click is instant
    threading.Thread(target=_get_whisper_model, daemon=True).start()

    sys.exit(app.exec())
