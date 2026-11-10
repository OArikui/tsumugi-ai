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
    stt_ready = Signal(bool, str)
    recording_changed = Signal(bool)
    transcript_ready = Signal(str)
    voicevox_connection_changed = Signal(bool, str)

    def __init__(
        self,
        llm: Any,
        tts: Any,
        viewer: Live2DWidget,
        stt: Any | None,
        parse_response: Callable[[str], tuple[str, str]],
        stop_event: threading.Event,
        parent=None,
        streaming_enabled: bool = True,
    ) -> None:
        super().__init__(parent)
        self._llm = llm
        self._tts = tts
        self._viewer = viewer
        self._stt = stt
        self._stt_ready = False
        self._voicevox_connected = False
        self._parse_response = parse_response
        self._stop_event = stop_event
        self._streaming_enabled = bool(streaming_enabled)
        self._queue: queue.Queue[tuple[str, str | None]] = queue.Queue()

    def submit(self, text: str) -> None:
        self._queue.put(("text", text))

    def start_recording(self) -> None:
        self._queue.put(("record_start", None))

    def stop_recording(self) -> None:
        self._queue.put(("record_stop", None))

    def reconnect_voicevox(self) -> None:
        """VOICEVOXへの再接続をワーカースレッドへ依頼する。"""
        self._queue.put(("reconnect_voicevox", None))

    def request_stop(self) -> None:
        self._stop_event.set()
        self._queue.put(("stop", None))

    def _disable_stt(self, reason: str) -> None:
        logger.warning(
            "音声入力を無効化します。テキスト入力は引き続き利用できます。理由: %s",
            reason,
            exc_info=True,
        )
        if self._stt is not None:
            try:
                self._stt.close()
            except Exception:
                logger.debug("音声入力リソースの終了に失敗しました。", exc_info=True)
        self._stt = None
        self._stt_ready = False
        self.stt_ready.emit(False, reason)
        self.recording_changed.emit(False)

    def run(self) -> None:
        """初期化後、要求を1件ずつ順番に処理する。"""
        try:
            self.status_changed.emit("VOICEVOXに接続しています")
            try:
                self._tts.check_connection()
                self._voicevox_connected = True
                self.voicevox_connection_changed.emit(True, "")
            except Exception as exc:
                self._voicevox_connected = False
                message = (
                    "VOICEVOXに接続できません。起動してから"
                    "「VOICEVOX再接続」を押してください。"
                )
                logger.warning("%s 詳細: %s", message, exc)
                self.voicevox_connection_changed.emit(False, message)
            if self._stop_event.is_set():
                return

            self.status_changed.emit("LLMを初期化しています")
            self._llm.start()
            if self._stop_event.is_set():
                return

            if self._stt is not None:
                self.status_changed.emit("音声入力を準備しています")
                try:
                    self._stt.prepare()
                    self._stt_ready = True
                    self.stt_ready.emit(True, "")
                except Exception as exc:
                    self._disable_stt(str(exc))
            else:
                self.stt_ready.emit(False, "")

            self.ready.emit()
            self.status_changed.emit("待機中")
            while not self._stop_event.is_set():
                try:
                    task, payload = self._queue.get(timeout=0.2)
                except queue.Empty:
                    continue

                if task == "stop" or self._stop_event.is_set():
                    break

                if task == "reconnect_voicevox":
                    self.status_changed.emit("VOICEVOXに再接続しています")
                    try:
                        self._tts.check_connection()
                        self._voicevox_connected = True
                        self.voicevox_connection_changed.emit(True, "")
                    except Exception as exc:
                        self._voicevox_connected = False
                        message = (
                            "VOICEVOXに接続できません。起動してから"
                            "「VOICEVOX再接続」を押してください。"
                        )
                        logger.warning("%s 詳細: %s", message, exc)
                        self.voicevox_connection_changed.emit(False, message)
                    if not self._stop_event.is_set():
                        self.status_changed.emit("待機中")
                    continue

                if task == "record_start":
                    if not self._stt_ready or self._stt is None:
                        continue
                    try:
                        self._stt.start_recording()
                        self._viewer.set_activity_state("listening")
                        self.recording_changed.emit(True)
                        self.status_changed.emit("聞き取り中...")
                    except Exception as exc:
                        self._disable_stt(str(exc))
                        self.status_changed.emit("待機中")
                    continue

                if task == "record_stop":
                    if not self._stt_ready or self._stt is None:
                        continue
                    try:
                        self.status_changed.emit("文字起こし中...")
                        self._viewer.set_activity_state("thinking")
                        self.recording_changed.emit(False)
                        recognized_text = self._stt.stop_recording_and_transcribe()
                        if recognized_text:
                            self.transcript_ready.emit(recognized_text)
                        self.status_changed.emit("待機中")
                    except Exception as exc:
                        self._disable_stt(str(exc))
                        self.status_changed.emit("待機中")
                    continue

                if task != "text" or not payload:
                    continue

                text = payload
                streaming_session = None
                try:
                    self.status_changed.emit("考え中")
                    self._viewer.set_activity_state("thinking")
                    use_streaming = (
                        self._streaming_enabled
                        and self._voicevox_connected
                        and bool(getattr(self._llm, "supports_streaming", False))
                    )
                    if use_streaming:
                        streaming_session = self._tts.start_streaming()
                        response = self._llm.chat_streaming(
                            text,
                            streaming_session.feed_text,
                            streaming_session.abort,
                        )
                    else:
                        response = self._llm.chat(text)
                    if self._stop_event.is_set():
                        if streaming_session is not None:
                            streaming_session.cancel()
                        break

                    emotion, spoken_text = self._parse_response(response)
                    if not spoken_text:
                        if streaming_session is not None:
                            streaming_session.cancel()
                        self.error_occurred.emit(
                            "LLMから読み上げ可能な本文が返されませんでした。"
                        )
                        self.status_changed.emit("待機中")
                        continue

                    self._viewer.set_emotion(emotion)
                    self.assistant_message.emit(spoken_text)
                    if self._voicevox_connected:
                        self.status_changed.emit("声を生成中")
                        self._viewer.set_activity_state("synthesizing")
                        try:
                            if streaming_session is not None:
                                streaming_session.finish(spoken_text)
                            else:
                                self._tts.speak(spoken_text)
                        except Exception as exc:
                            self._voicevox_connected = False
                            message = (
                                "VOICEVOXに接続できません。起動してから"
                                "「VOICEVOX再接続」を押してください。"
                            )
                            logger.warning(
                                "VOICEVOXの音声合成・再生に失敗しました: %s",
                                exc,
                                exc_info=True,
                            )
                            self.voicevox_connection_changed.emit(False, message)
                    else:
                        if streaming_session is not None:
                            streaming_session.cancel()
                        logger.info(
                            "VOICEVOX未接続のため、音声読み上げをスキップしました。"
                        )
                except Exception as exc:
                    if streaming_session is not None:
                        streaming_session.cancel()
                    if not self._stop_event.is_set():
                        logger.exception("GUIの会話処理に失敗しました。")
                        self.error_occurred.emit(str(exc))
                finally:
                    if streaming_session is not None:
                        streaming_session.cancel()
                    self._viewer.set_mouth_open(0.0)
                    self._viewer.set_emotion("normal")
                    self._viewer.set_activity_state("idle")
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
            self._viewer.set_activity_state("idle")
            if self._stt is not None:
                try:
                    self._stt.close()
                except Exception:
                    logger.exception("音声入力の終了処理に失敗しました。")
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
        stt_factory: Callable[[], Any | None],
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
        self._voicevox_connected = False
        self._voicevox_connecting = False
        self._stt_available = False
        self._recording = False
        self._stt_auto_send = bool(config.get("stt_auto_send", True))
        self._status_text = "起動しています..."
        self._turn_pending = False

        central = QWidget(self)
        root = QVBoxLayout(central)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        header = QHBoxLayout()
        title = QLabel("春日部つむぎ AI")
        title.setStyleSheet("font-size: 20px; font-weight: 700;")
        header.addWidget(title)
        header.addStretch(1)
        self.voicevox_status_label = QLabel("VOICEVOX: 接続確認中")
        self.voicevox_status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.voicevox_status_label.setStyleSheet(
            "padding: 7px 10px; border-radius: 8px; "
            "background: #263445; color: #E8EEF7; font-weight: 600;"
        )
        header.addWidget(self.voicevox_status_label)
        self.voicevox_reconnect_button = QPushButton("VOICEVOX再接続")
        self.voicevox_reconnect_button.setEnabled(False)
        self.voicevox_reconnect_button.clicked.connect(
            lambda _checked=False: self._reconnect_voicevox()
        )
        header.addWidget(self.voicevox_reconnect_button)
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
        self.send_button.clicked.connect(lambda _checked=False: self._send_message())
        self.mic_button = QPushButton("🎤 音声入力", conversation_panel)
        self.mic_button.setEnabled(False)
        self.mic_button.setMinimumHeight(42)
        self.mic_button.clicked.connect(
            lambda _checked=False: self._toggle_microphone()
        )
        entry_row.addWidget(self.input_field, 1)
        entry_row.addWidget(self.mic_button)
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
        self.exit_button.clicked.connect(lambda _checked=False: self.close())
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
        try:
            self._stt = stt_factory()
        except Exception as exc:
            logger.warning("音声入力の準備に失敗しました。テキスト入力で続行します: %s", exc)
            self._stt = None

        self._worker = ConversationWorker(
            self._llm,
            self._tts,
            self.live2d_widget,
            self._stt,
            parse_response,
            self._stop_event,
            self,
            streaming_enabled=bool(config.get("tts_streaming_enabled", True)),
        )
        self._worker.status_changed.connect(self._set_status)
        self._worker.ready.connect(self._on_ready)
        self._worker.assistant_message.connect(self._append_assistant_message)
        self._worker.error_occurred.connect(self._show_error)
        self._worker.turn_finished.connect(self._on_turn_finished)
        self._worker.stt_ready.connect(self._on_stt_ready)
        self._worker.recording_changed.connect(self._on_recording_changed)
        self._worker.transcript_ready.connect(self._on_transcript_ready)
        self._worker.voicevox_connection_changed.connect(
            self._on_voicevox_connection_changed
        )
        self._worker.finished.connect(self._on_worker_finished)
        self._worker.start()

    @Slot(str)
    def _set_status(self, status: str) -> None:
        self._status_text = status
        self.status_label.setText(status)
        self._refresh_voicevox_reconnect_button()
        if status == "待機中" and self._ready and not self._turn_pending:
            self.input_field.setEnabled(True)
            self.send_button.setEnabled(True)
        self._refresh_microphone_button()
        if status == "考え中":
            self.live2d_widget.set_activity("thinking")
        elif status in ("声を生成中", "再生中"):
            self.live2d_widget.set_activity("speaking")
        elif status in ("待機中", "起動エラー"):
            self.live2d_widget.set_activity("idle")

    def _on_playback_progress(self, progress: float, rms: float) -> None:
        if progress < 1.0:
            self._worker.status_changed.emit("再生中")

    def _refresh_voicevox_reconnect_button(self) -> None:
        if self._voicevox_connecting:
            self.voicevox_reconnect_button.setText("接続中...")
        elif self._voicevox_connected:
            self.voicevox_reconnect_button.setText("接続済み")
        else:
            self.voicevox_reconnect_button.setText("VOICEVOX再接続")
        self.voicevox_reconnect_button.setEnabled(
            self._ready
            and not self._voicevox_connected
            and not self._voicevox_connecting
            and not self._turn_pending
            and not self._close_requested
        )

    @Slot(bool, str)
    def _on_voicevox_connection_changed(self, connected: bool, detail: str) -> None:
        self._voicevox_connected = connected
        self._voicevox_connecting = False
        if connected:
            self.voicevox_status_label.setText("VOICEVOX: 接続済み")
            self.voicevox_status_label.setToolTip("VOICEVOXに接続しています。")
        else:
            self.voicevox_status_label.setText("VOICEVOX: 未接続")
            self.voicevox_status_label.setToolTip(
                detail or "VOICEVOXに接続できません。起動してから再接続してください。"
            )
        self._refresh_voicevox_reconnect_button()

    def _reconnect_voicevox(self) -> None:
        if (
            not self._ready
            or not self._worker.isRunning()
            or self._voicevox_connected
            or self._voicevox_connecting
        ):
            return
        self._voicevox_connecting = True
        self.voicevox_status_label.setText("VOICEVOX: 再接続中")
        self._refresh_voicevox_reconnect_button()
        self._worker.reconnect_voicevox()

    def _refresh_microphone_button(self) -> None:
        can_start = (
            self._stt_available
            and self._ready
            and not self._close_requested
            and not self._turn_pending
            and self._status_text in ("待機中", "聞き取り中...")
        )
        self.mic_button.setEnabled(can_start)
        self.mic_button.setText(
            "録音停止" if self._recording else "🎤 音声入力"
        )

    @Slot(bool, str)
    def _on_stt_ready(self, available: bool, detail: str) -> None:
        self._stt_available = available
        if not available and detail:
            self.hint_label.setText(
                "音声入力は利用できません。テキスト入力は引き続き使えます。"
            )
            logger.warning("音声入力は無効です: %s", detail)
        elif available:
            self.hint_label.setText(
                "マイクボタンで録音し、もう一度押すと文字起こしします。"
            )
        self._refresh_microphone_button()

    @Slot(bool)
    def _on_recording_changed(self, recording: bool) -> None:
        self._recording = recording
        if recording:
            self.input_field.setEnabled(False)
            self.send_button.setEnabled(False)
        self._refresh_microphone_button()

    @Slot(str)
    def _on_transcript_ready(self, transcript: str) -> None:
        self.input_field.setText(transcript)
        if self._stt_auto_send:
            self._submit_text(transcript)
        else:
            self.input_field.setEnabled(self._ready and not self._close_requested)
            self.send_button.setEnabled(self._ready and not self._close_requested)
            self.input_field.setFocus()
            self.hint_label.setText("音声認識結果を入力欄に入れました。内容を確認して送信してください。")

    @Slot()
    def _on_ready(self) -> None:
        self._ready = True
        self.input_field.setEnabled(True)
        self.send_button.setEnabled(True)
        self._refresh_microphone_button()
        self._refresh_voicevox_reconnect_button()
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
        self._submit_text(self.input_field.text())

    def _submit_text(self, value: str) -> None:
        if not self._ready or not self._worker.isRunning():
            return
        message = value.strip()
        if not message:
            return
        escaped = html.escape(message).replace("\n", "<br>")
        self.history.append(
            f'<p style="color:#9EC7F2;"><b>あなた:</b> {escaped}</p>'
        )
        self.input_field.clear()
        self.input_field.setEnabled(False)
        self.send_button.setEnabled(False)
        self._turn_pending = True
        self._refresh_microphone_button()
        self._worker.submit(message)

    def _toggle_microphone(self) -> None:
        if not self._stt_available or not self._worker.isRunning():
            return
        if self._recording:
            self.mic_button.setEnabled(False)
            self._worker.stop_recording()
        elif self._status_text == "待機中" and not self._turn_pending:
            self.mic_button.setEnabled(False)
            self._worker.start_recording()

    @Slot()
    def _on_turn_finished(self) -> None:
        self._turn_pending = False
        self._refresh_voicevox_reconnect_button()
        if self._ready and not self._close_requested:
            self.input_field.setEnabled(True)
            self.send_button.setEnabled(True)
            self.input_field.setFocus()
        self._refresh_microphone_button()

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
            lambda _checked=False: self._finish_login_dialog(dialog, True)
        )
        cancel_button.clicked.connect(
            lambda _checked=False: self._finish_login_dialog(dialog, False)
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
        self.mic_button.setEnabled(False)
        self._refresh_voicevox_reconnect_button()
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
            self._refresh_voicevox_reconnect_button()
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
    stt_factory: Callable[[], Any | None],
    parse_response: Callable[[str], tuple[str, str]],
) -> int:
    """GUIアプリケーションを起動する。"""
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    app.setApplicationName("春日部つむぎ AI")
    window = MainWindow(
        config,
        llm_factory,
        tts_factory,
        stt_factory,
        parse_response,
    )
    window.show()
    return app.exec()
