"""会話UIとバックグラウンド処理を提供するPySide6 GUI。"""

from __future__ import annotations

import html
import logging
import queue
import threading
from typing import Any, Callable

from PySide6.QtCore import QObject, QThread, QTimer, Qt, Signal, Slot
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from app.live2d_widget import Live2DWidget

logger = logging.getLogger(__name__)


class LoginBridge(QObject):
    """ワーカースレッドとGUIログイン確認ダイアログの間を仲介する。"""

    requested = Signal(str)

    def __init__(self, stop_event: threading.Event) -> None:
        super().__init__()
        self._stop_event = stop_event
        self._event = threading.Event()
        self._lock = threading.Lock()
        self._accepted = False

    def wait_for_login(self, service: str) -> bool:
        """Edgeでのログイン完了をGUIから受け取るまでワーカーだけを待機させる。"""
        with self._lock:
            self._accepted = False
            self._event.clear()

        self.requested.emit(service)
        while not self._event.wait(0.2):
            if self._stop_event.is_set():
                return False

        with self._lock:
            return self._accepted and not self._stop_event.is_set()

    def complete(self, accepted: bool) -> None:
        with self._lock:
            if self._event.is_set():
                return
            self._accepted = bool(accepted)
            self._event.set()

    def cancel(self) -> None:
        self.complete(False)


class ConversationWorker(QThread):
    """LLM問い合わせ・音声合成・再生をUIスレッド外で実行する。"""

    status_changed = Signal(str)
    ready = Signal()
    assistant_message = Signal(str)
    error_occurred = Signal(str)
    turn_finished = Signal()

    def __init__(
        self,
        llm: Any,
        tts: Any,
        viewer: Live2DWidget,
        parse_response: Callable[[str], tuple[str, str]],
        stop_event: threading.Event,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._llm = llm
        self._tts = tts
        self._viewer = viewer
        self._parse_response = parse_response
        self._stop_event = stop_event
        self._queue: queue.Queue[str | None] = queue.Queue()

    def submit(self, text: str) -> None:
        self._queue.put(text)

    def request_stop(self) -> None:
        self._stop_event.set()
        self._queue.put(None)

    def run(self) -> None:
        """初期化後、要求を1件ずつ順番に処理する。"""
        try:
            self.status_changed.emit("VOICEVOXに接続しています")
            self._tts.check_connection()
            if self._stop_event.is_set():
                return

            self.status_changed.emit("LLMを初期化しています")
            self._llm.start()
            if self._stop_event.is_set():
                return

            self.ready.emit()
            self.status_changed.emit("待機中")
            while not self._stop_event.is_set():
                try:
                    text = self._queue.get(timeout=0.2)
                except queue.Empty:
                    continue

                if text is None or self._stop_event.is_set():
                    break

                try:
                    self.status_changed.emit("考え中")
                    self._viewer.set_activity("thinking")
                    response = self._llm.chat(text)
                    if self._stop_event.is_set():
                        break

                    emotion, spoken_text = self._parse_response(response)
                    if not spoken_text:
                        self.error_occurred.emit(
                            "LLMから読み上げ可能な本文が返されませんでした。"
                        )
                        self.turn_finished.emit()
                        self.status_changed.emit("待機中")
                        self._viewer.set_activity("idle")
                        continue

                    self._viewer.set_emotion(emotion)
                    self.assistant_message.emit(spoken_text)
                    self.status_changed.emit("声を生成中")
                    self._viewer.set_activity("speaking")
                    self._tts.speak(spoken_text)
                except Exception as exc:
                    if not self._stop_event.is_set():
                        logger.exception("GUIの会話処理に失敗しました。")
                        self.error_occurred.emit(str(exc))
                finally:
                    self._viewer.set_mouth_open(0.0)
                    self._viewer.set_emotion("normal")
                    self._viewer.set_activity("idle")
                    self.turn_finished.emit()
                    if not self._stop_event.is_set():
                        self.status_changed.emit("待機中")
        except Exception as exc:
            if not self._stop_event.is_set():
                logger.exception("GUIの初期化に失敗しました。")
                self.error_occurred.emit(str(exc))
                self.status_changed.emit("起動エラー")
        finally:
            self._viewer.set_mouth_open(0.0)
            self._viewer.set_emotion("normal")
            self._viewer.set_activity("idle")
            try:
                self._llm.close()
            except Exception:
                logger.exception("LLMの終了処理に失敗しました。")
            try:
                self._tts.close()
            except Exception:
                logger.exception("VOICEVOXの終了処理に失敗しました。")


class MainWindow(QMainWindow):
    """Live2D・会話・入力・状態を1つにまとめるメインウィンドウ。"""

    def __init__(
        self,
        config: dict,
        llm_factory: Callable[[Callable[[str], bool], threading.Event], Any],
        tts_factory: Callable[
            [Live2DWidget, Callable[[float, float], None], threading.Event],
            Any,
        ],
        parse_response: Callable[[str], tuple[str, str]],
    ) -> None:
        super().__init__()
        self.setWindowTitle("春日部つむぎ AI")
        self.resize(1080, 720)
        self.setMinimumSize(820, 560)

        self._stop_event = threading.Event()
        self._login_bridge = LoginBridge(self._stop_event)
        self._login_bridge.requested.connect(self._show_login_dialog)
        self._login_dialog: QDialog | None = None
        self._close_requested = False
        self._close_ready = False
        self._ready = False

        central = QWidget(self)
        root = QVBoxLayout(central)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        header = QHBoxLayout()
        title = QLabel("春日部つむぎ AI")
        title.setStyleSheet("font-size: 20px; font-weight: 700;")
        header.addWidget(title)
        header.addStretch(1)
        self.status_label = QLabel("起動しています...")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.status_label.setStyleSheet(
            "padding: 7px 12px; border-radius: 8px; "
            "background: #263445; color: #E8EEF7; font-weight: 600;"
        )
        header.addWidget(self.status_label)
        root.addLayout(header)

        content = QHBoxLayout()
        content.setSpacing(14)
        self.live2d_widget = Live2DWidget(config, central)
        content.addWidget(self.live2d_widget, 5)

        conversation_panel = QWidget(central)
        conversation_layout = QVBoxLayout(conversation_panel)
        conversation_layout.setContentsMargins(0, 0, 0, 0)
        conversation_layout.setSpacing(8)

        conversation_title = QLabel("会話")
        conversation_title.setStyleSheet("font-size: 16px; font-weight: 700;")
        conversation_layout.addWidget(conversation_title)

        self.history = QTextBrowser(conversation_panel)
        self.history.setOpenExternalLinks(False)
        self.history.setPlaceholderText("ここに会話が表示されます。")
        self.history.setStyleSheet(
            "QTextBrowser { background: #17212D; color: #E7EDF5; "
            "border: 1px solid #354354; border-radius: 10px; padding: 10px; }"
        )
        conversation_layout.addWidget(self.history, 1)

        entry_row = QHBoxLayout()
        self.input_field = QLineEdit(conversation_panel)
        self.input_field.setPlaceholderText("メッセージを入力...")
        self.input_field.setEnabled(False)
        self.input_field.returnPressed.connect(self._send_message)
        self.input_field.setMinimumHeight(42)
        self.send_button = QPushButton("送信", conversation_panel)
        self.send_button.setEnabled(False)
        self.send_button.setMinimumHeight(42)
        self.send_button.clicked.connect(self._send_message)
        entry_row.addWidget(self.input_field, 1)
        entry_row.addWidget(self.send_button)
        conversation_layout.addLayout(entry_row)
        content.addWidget(conversation_panel, 6)
        root.addLayout(content, 1)

        footer = QHBoxLayout()
        self.hint_label = QLabel(
            "Enterキーで送信できます。ログインが必要な場合は案内ダイアログが表示されます。"
        )
        self.hint_label.setStyleSheet("color: #8795A7;")
        footer.addWidget(self.hint_label, 1)
        self.exit_button = QPushButton("終了")
        self.exit_button.clicked.connect(self.close)
        footer.addWidget(self.exit_button)
        root.addLayout(footer)

        self.setCentralWidget(central)
        self.setStyleSheet(
            "QMainWindow, QWidget { background: #101923; color: #E7EDF5; } "
            "QLineEdit { background: #17212D; border: 1px solid #405166; "
            "border-radius: 8px; padding: 8px 10px; color: #F5F7FA; } "
            "QPushButton { background: #D89B45; color: #17212D; "
            "border: none; border-radius: 8px; padding: 8px 16px; "
            "font-weight: 700; } "
            "QPushButton:disabled { background: #485361; color: #AAB4C0; }"
        )

        self._llm = llm_factory(
            self._login_bridge.wait_for_login,
            self._stop_event,
        )
        self._tts = tts_factory(
            self.live2d_widget,
            self._on_playback_progress,
            self._stop_event,
        )
        self._worker = ConversationWorker(
            self._llm,
            self._tts,
            self.live2d_widget,
            parse_response,
            self._stop_event,
            self,
        )
        self._worker.status_changed.connect(self._set_status)
        self._worker.ready.connect(self._on_ready)
        self._worker.assistant_message.connect(self._append_assistant_message)
        self._worker.error_occurred.connect(self._show_error)
        self._worker.turn_finished.connect(self._on_turn_finished)
        self._worker.finished.connect(self._on_worker_finished)
        QTimer.singleShot(0, self._worker.start)

    @Slot(str)
    def _set_status(self, status: str) -> None:
        self.status_label.setText(status)
        if status == "考え中":
            self.live2d_widget.set_activity("thinking")
        elif status in ("声を生成中", "再生中"):
            self.live2d_widget.set_activity("speaking")
        elif status in ("待機中", "起動エラー"):
            self.live2d_widget.set_activity("idle")

    def _on_playback_progress(self, progress: float, rms: float) -> None:
        if progress < 1.0:
            self._worker.status_changed.emit("再生中")

    @Slot()
    def _on_ready(self) -> None:
        self._ready = True
        self.input_field.setEnabled(True)
        self.send_button.setEnabled(True)
        self.input_field.setFocus()
        self.history.append(
            '<p style="color:#8FA5BD;">準備ができました。会話を始められます。</p>'
        )

    @Slot(str)
    def _append_assistant_message(self, message: str) -> None:
        escaped = html.escape(message).replace("\n", "<br>")
        self.history.append(
            f'<p style="color:#F2C27B;"><b>春日部つむぎ:</b> {escaped}</p>'
        )

    def _send_message(self) -> None:
        if not self._ready or not self._worker.isRunning():
            return
        message = self.input_field.text().strip()
        if not message:
            return
        escaped = html.escape(message).replace("\n", "<br>")
        self.history.append(
            f'<p style="color:#9EC7F2;"><b>あなた:</b> {escaped}</p>'
        )
        self.input_field.clear()
        self.input_field.setEnabled(False)
        self.send_button.setEnabled(False)
        self._worker.submit(message)

    @Slot()
    def _on_turn_finished(self) -> None:
        if self._ready and not self._close_requested:
            self.input_field.setEnabled(True)
            self.send_button.setEnabled(True)
            self.input_field.setFocus()

    @Slot(str)
    def _show_error(self, message: str) -> None:
        escaped = html.escape(message)
        self.history.append(
            f'<p style="color:#F28B82;"><b>エラー:</b> {escaped}</p>'
        )
        if not self._ready:
            self.status_label.setText("起動エラー")
            self.hint_label.setText(
                "接続を確認してください。終了して設定を見直すことができます。"
            )

    @Slot(str)
    def _show_login_dialog(self, service: str) -> None:
        if self._close_requested:
            self._login_bridge.cancel()
            return
        if self._login_dialog is not None:
            self._login_dialog.raise_()
            self._login_dialog.activateWindow()
            return

        dialog = QDialog(self)
        dialog.setWindowTitle(f"{service} のログイン")
        dialog.setModal(True)
        layout = QVBoxLayout(dialog)
        label = QLabel(
            f"Edgeで {service} にログインしてください。\n"
            "ログインが完了したら「ログイン完了」を押してください。"
        )
        label.setWordWrap(True)
        layout.addWidget(label)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        complete_button = QPushButton("ログイン完了", dialog)
        cancel_button = QPushButton("キャンセル", dialog)
        buttons.addWidget(cancel_button)
        buttons.addWidget(complete_button)
        layout.addLayout(buttons)

        complete_button.clicked.connect(
            lambda: self._finish_login_dialog(dialog, True)
        )
        cancel_button.clicked.connect(
            lambda: self._finish_login_dialog(dialog, False)
        )
        dialog.finished.connect(
            lambda _result: self._finish_login_dialog(dialog, False)
        )
        self._login_dialog = dialog
        dialog.open()

    def _finish_login_dialog(self, dialog: QDialog, accepted: bool) -> None:
        self._login_bridge.complete(accepted)
        if self._login_dialog is dialog:
            self._login_dialog = None
        if dialog.isVisible():
            dialog.done(
                QDialog.DialogCode.Accepted
                if accepted
                else QDialog.DialogCode.Rejected
            )

    @Slot()
    def _on_worker_finished(self) -> None:
        self._ready = False
        self.input_field.setEnabled(False)
        self.send_button.setEnabled(False)
        if self._close_requested:
            self._close_ready = True
            QTimer.singleShot(0, self.close)
        elif self.status_label.text() != "起動エラー":
            self.status_label.setText("終了しました")

    def closeEvent(self, event) -> None:
        if self._close_ready:
            self.live2d_widget.shutdown()
            event.accept()
            return

        if self._worker.isRunning():
            self._close_requested = True
            self.status_label.setText("終了処理中...")
            self.input_field.setEnabled(False)
            self.send_button.setEnabled(False)
            self.exit_button.setEnabled(False)
            self._login_bridge.cancel()
            self._worker.request_stop()
            event.ignore()
            return

        self.live2d_widget.shutdown()
        event.accept()


def run_gui(
    config: dict,
    llm_factory: Callable[[Callable[[str], bool], threading.Event], Any],
    tts_factory: Callable[
        [Live2DWidget, Callable[[float, float], None], threading.Event],
        Any,
    ],
    parse_response: Callable[[str], tuple[str, str]],
) -> int:
    """GUIアプリケーションを起動する。"""
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    app.setApplicationName("春日部つむぎ AI")
    window = MainWindow(config, llm_factory, tts_factory, parse_response)
    window.show()
    return app.exec()
