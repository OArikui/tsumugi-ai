"""マイク録音とfaster-whisperによる音声文字起こし。"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)


class SpeechToText:
    """Push-to-Talk方式の録音と日本語文字起こしを行う。

    prepare() と stop_recording_and_transcribe() は時間のかかるため、
    GUIではConversationWorkerなどのワーカースレッドから呼び出す。
    start_recording() は録音ストリームを開始してすぐに戻り、音声取得は
    sounddeviceのコールバックでバックグラウンド実行する。
    """

    SAMPLE_RATE = 16_000
    CHANNELS = 1
    MAX_RECORDING_SECONDS = 180

    def __init__(
        self,
        model_name: str = "small",
        language: str = "ja",
        mode: str = "push_to_talk",
        device: int | str | None = None,
    ) -> None:
        self.model_name = str(model_name or "small").strip()
        self.language = str(language or "ja").strip() or "ja"
        self.mode = str(mode or "push_to_talk").strip().casefold()
        if self.mode != "push_to_talk":
            logger.warning(
                "stt_mode=%r は未対応のためPush-to-Talkで動作します。",
                self.mode,
            )
            self.mode = "push_to_talk"

        self._requested_device = device
        self._input_device: int | str | None = None
        self._sounddevice: Any | None = None
        self._model: Any | None = None
        self._stream: Any | None = None
        self._lock = threading.Lock()
        self._chunks: list[np.ndarray] = []
        self._recording = False
        self._recorded_frames = 0
        self._recording_started_at = 0.0
        self._max_frames = self.SAMPLE_RATE * self.MAX_RECORDING_SECONDS
        self._max_duration_reached = threading.Event()

    @property
    def is_ready(self) -> bool:
        return self._model is not None and self._sounddevice is not None

    @property
    def is_recording(self) -> bool:
        with self._lock:
            return self._recording

    def prepare(self) -> None:
        """マイクを確認し、faster-whisperモデルをロードする。"""
        if self.is_ready:
            return

        try:
            import sounddevice as sd
        except ImportError as exc:
            raise RuntimeError(
                "sounddeviceがインストールされていません。"
            ) from exc

        try:
            devices = sd.query_devices()
            input_devices = [
                (index, info)
                for index, info in enumerate(devices)
                if int(info.get("max_input_channels", 0)) > 0
            ]
            if not input_devices:
                raise RuntimeError("利用可能なマイク入力デバイスがありません。")

            if self._requested_device is not None:
                self._input_device = self._requested_device
            else:
                try:
                    default_input = sd.default.device[0]
                except (TypeError, IndexError, AttributeError):
                    default_input = None

                valid_ids = {index for index, _ in input_devices}
                if default_input in valid_ids:
                    self._input_device = int(default_input)
                else:
                    self._input_device = input_devices[0][0]

            from faster_whisper import WhisperModel

            logger.info(
                "音声認識モデルを読み込んでいます: %s (CPU / int8)",
                self.model_name,
            )
            model = WhisperModel(
                self.model_name,
                device="cpu",
                compute_type="int8",
            )
        except Exception as exc:
            raise RuntimeError(
                "音声入力の初期化に失敗しました。マイク、faster-whisper、"
                "モデルのダウンロード状態を確認してください。"
            ) from exc

        self._sounddevice = sd
        self._model = model
        logger.info(
            "音声入力を利用できます。マイク=%s、モデル=%s、言語=%s",
            self._input_device,
            self.model_name,
            self.language,
        )

    def _audio_callback(
        self,
        indata: np.ndarray,
        frames: int,
        time_info: Any,
        status: Any,
    ) -> None:
        """sounddeviceの録音コールバック。音声のコピーだけを行う。"""
        if status:
            logger.debug("マイク入力状態: %s", status)

        with self._lock:
            if not self._recording:
                return

            remaining = self._max_frames - self._recorded_frames
            if remaining <= 0:
                self._recording = False
                self._max_duration_reached.set()
                raise self._sounddevice.CallbackStop

            accepted = min(int(frames), remaining)
            if accepted > 0:
                self._chunks.append(
                    np.asarray(indata[:accepted, 0], dtype=np.float32).copy()
                )
                self._recorded_frames += accepted

            if self._recorded_frames >= self._max_frames:
                self._recording = False
                self._max_duration_reached.set()
                raise self._sounddevice.CallbackStop

    def start_recording(self) -> None:
        """マイク録音を開始する。音声データの取得はコールバックで行う。"""
        if not self.is_ready:
            raise RuntimeError("音声入力が初期化されていません。")

        with self._lock:
            if self._recording:
                raise RuntimeError("すでに録音中です。")
            if self._stream is not None:
                raise RuntimeError("前回の録音ストリームが終了していません。")
            self._chunks = []
            self._recorded_frames = 0
            self._recording_started_at = time.monotonic()
            self._max_duration_reached.clear()
            self._recording = True

        try:
            stream = self._sounddevice.InputStream(
                device=self._input_device,
                samplerate=self.SAMPLE_RATE,
                channels=self.CHANNELS,
                dtype="float32",
                callback=self._audio_callback,
                blocksize=0,
            )
            stream.start()
            with self._lock:
                self._stream = stream
            logger.info("マイク録音を開始しました。")
        except Exception:
            with self._lock:
                self._recording = False
            logger.warning("マイク録音を開始できません。", exc_info=True)
            raise

    def stop_recording(self) -> np.ndarray:
        """録音を停止し、16kHzモノラルのfloat32波形を返す。"""
        with self._lock:
            if not self._recording and self._stream is None:
                raise RuntimeError("録音は開始されていません。")
            self._recording = False
            stream = self._stream
            self._stream = None

        if stream is not None:
            try:
                stream.stop()
            finally:
                stream.close()

        with self._lock:
            chunks = self._chunks
            self._chunks = []
            frame_count = self._recorded_frames
            elapsed = (
                time.monotonic() - self._recording_started_at
                if self._recording_started_at
                else 0.0
            )

        if self._max_duration_reached.is_set():
            logger.warning(
                "録音が上限の%d秒に達したため自動停止しました。",
                self.MAX_RECORDING_SECONDS,
            )
        logger.info(
            "マイク録音を停止しました。長さ=%.1f秒、サンプル=%d",
            elapsed,
            frame_count,
        )

        if not chunks:
            return np.empty(0, dtype=np.float32)
        return np.concatenate(chunks).astype(np.float32, copy=False)

    def transcribe(self, audio: np.ndarray) -> str:
        """波形を日本語で文字起こしする。モデル推論は呼び出し元スレッドで行う。"""
        if not self.is_ready:
            raise RuntimeError("音声認識モデルが初期化されていません。")

        waveform = np.asarray(audio, dtype=np.float32).reshape(-1)
        if waveform.size < int(self.SAMPLE_RATE * 0.25):
            logger.info("録音が短いため文字起こしを省略しました。")
            return ""
        if not np.isfinite(waveform).all():
            raise ValueError("録音データに不正な値が含まれています。")

        peak = float(np.max(np.abs(waveform)))
        rms = float(np.sqrt(np.mean(np.square(waveform, dtype=np.float64))))
        if peak < 0.003 or rms < 0.0008:
            logger.info("音声がほぼ無音のため文字起こしを省略しました。")
            return ""

        logger.info(
            "音声を文字起こししています。長さ=%.1f秒",
            waveform.size / self.SAMPLE_RATE,
        )
        segments, info = self._model.transcribe(
            waveform,
            language=self.language,
            task="transcribe",
            beam_size=5,
            vad_filter=True,
        )
        # faster-whisperはsegmentsを反復した時点で推論を実行する。
        text = "".join(segment.text for segment in segments).strip()
        logger.info(
            "音声認識が完了しました。言語=%s、文字数=%d",
            getattr(info, "language", self.language),
            len(text),
        )
        return text

    def stop_recording_and_transcribe(self) -> str:
        """録音を止めて文字起こしする。GUIではワーカースレッドから呼ぶ。"""
        audio = self.stop_recording()
        return self.transcribe(audio)

    def close(self) -> None:
        """録音ストリームを閉じる。"""
        try:
            if self.is_recording or self._stream is not None:
                self.stop_recording()
        except Exception:
            logger.warning("録音ストリームの終了に失敗しました。", exc_info=True)
        self._model = None
        self._sounddevice = None
        logger.info("音声入力リソースを解放しました。")
