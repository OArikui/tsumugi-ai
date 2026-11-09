import io
import logging
import re
import time
import wave
from typing import Any, Callable

import numpy as np
import requests
import sounddevice as sd

logger = logging.getLogger(__name__)

RMSCallback = Callable[[float], None]


class VoiceVoxTTS:
    """VOICEVOX APIと連携し、音声を合成・再生する。"""

    def __init__(
        self,
        voicevox_url: str,
        speaker: int = 8,
        timeout: float = 30.0,
        volume: float = 1.0,
        rms_callback: RMSCallback | None = None,
    ) -> None:
        self.voicevox_url = voicevox_url.rstrip("/")
        self.speaker = speaker
        self.timeout = timeout
        self.volume = max(0.0, min(float(volume), 2.0))
        self.rms_callback = rms_callback
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "KasukabeTsumugiAI/1.0"})

    @staticmethod
    def split_sentences(text: str) -> list[str]:
        """文章を句読点で文単位に分割する。"""
        cleaned = re.sub(r"\s+", " ", text).strip()
        if not cleaned:
            return []
        sentences = re.split(r"(?<=[。！？!?])\s*", cleaned)
        return [sentence.strip() for sentence in sentences if sentence.strip()]

    def _create_audio_query(self, sentence: str) -> dict[str, Any]:
        """文章から音声合成用クエリを生成する。"""
        try:
            response = self.session.post(
                f"{self.voicevox_url}/audio_query",
                params={"text": sentence, "speaker": self.speaker},
                timeout=self.timeout,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            raise RuntimeError("VOICEVOXの音声合成クエリを取得できませんでした。VOICEVOXの起動状態を確認してください。") from exc
        try:
            return response.json()
        except ValueError as exc:
            raise RuntimeError("VOICEVOXの応答をJSONとして読み取れませんでした。") from exc

    def _synthesize(self, audio_query: dict[str, Any]) -> bytes:
        """音声合成クエリからWAV音声を生成する。"""
        try:
            response = self.session.post(
                f"{self.voicevox_url}/synthesis",
                params={"speaker": self.speaker},
                json=audio_query,
                timeout=self.timeout,
            )
            response.raise_for_status()
            return response.content
        except requests.RequestException as exc:
            raise RuntimeError("VOICEVOXの音声合成に失敗しました。VOICEVOXの起動状態を確認してください。") from exc

    @staticmethod
    def _decode_wav(wav_data: bytes) -> tuple[np.ndarray, int, int]:
        """音声データの形式(WAV)を確認し、データを返す。"""
        wav_file = wave.open(io.BytesIO(wav_data))
        try:
            channels = wav_file.getnchannels()
            sample_width = wav_file.getsampwidth()
            sample_rate = wav_file.getframerate()
            frames = wav_file.readframes(wav_file.getnframes())
        finally:
            wav_file.close()
        if sample_width == 2:
            audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
        elif sample_width == 4:
            audio = np.frombuffer(frames, dtype=np.int32).astype(np.float32) / 2147483648.0
        elif sample_width == 1:
            audio = (np.frombuffer(frames, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
        elif sample_width == 3:
            raw = np.frombuffer(frames, dtype=np.uint8).reshape(-1, 3)
            values = (
                raw[:, 0].astype(np.int32)
                | (raw[:, 1].astype(np.int32) << 8)
                | (raw[:, 2].astype(np.int32) << 16)
            )
            values = np.where(values & 0x800000, values - 0x1000000, values)
            audio = values.astype(np.float32) / 8388608.0
        else:
            raise RuntimeError(f"未対応のサンプル幅です: {sample_width}")
        if channels not in (1, 2):
            raise RuntimeError(f"対応できないチャンネル数です。channels={channels}")
        return audio, sample_rate, channels

    @staticmethod
    def _calculate_rms(audio: np.ndarray) -> float:
        """音声データのRMSを計算する。"""
        if audio.size == 0:
            return 0.0
        return float(np.sqrt(np.mean(audio.astype(np.float64) ** 2)))

    def _play_audio(self, wav_data: bytes) -> None:
        """音声を再生する。"""
        audio, sample_rate, channels = self._decode_wav(wav_data)
        audio = np.clip(audio * self.volume, -1.0, 1.0).astype(np.float32)
        # ステレオはフレーム単位に整形する（VOICEVOX出力は通常モノラル）
        if channels == 2:
            audio = audio.reshape(-1, 2)
        position = 0
        block_size = 1024

        def callback(outdata, frames, time_info, status):
            nonlocal position
            if status:
                logger.debug("音声再生状態: %s", status)
            total = len(audio)
            remaining = total - position
            if remaining <= 0:
                outdata.fill(0)
                raise sd.CallbackStop
            count = min(frames, remaining)
            chunk = audio[position:position + count]
            outdata.fill(0)
            if channels == 2:
                outdata[:count] = chunk
                flat = chunk.reshape(-1)
            else:
                outdata[:count] = chunk.reshape(-1, 1)
                flat = chunk
            position += count
            if self.rms_callback is not None:
                rms = float(np.sqrt(np.mean(np.square(flat))))
                try:
                    self.rms_callback(rms)
                except Exception:
                    logger.warning("RMSコールバックの呼び出しに失敗しました。", exc_info=True)

        try:
            with sd.OutputStream(
                samplerate=sample_rate,
                channels=channels,
                dtype="float32",
                blocksize=block_size,
                callback=callback,
            ):
                while position < len(audio):
                    time.sleep(0.05)
        except sd.PortAudioError as exc:
            raise RuntimeError("音声を再生できません。Windowsの出力デバイスやサウンド設定を確認してください。") from exc

    def speak(self, text: str) -> None:
        """テキストを文単位で合成・再生する。"""
        sentences = self.split_sentences(text)
        if not sentences:
            logger.warning("読み上げる文章がありません。")
            return
        total = len(sentences)
        for index, sentence in enumerate(sentences, start=1):
            logger.info("音声合成中 (%d/%d): %s", index, total, sentence)
            query = self._create_audio_query(sentence)
            wav_data = self._synthesize(query)
            logger.info("音声を再生します。")
            self._play_audio(wav_data)
            logger.info("音声再生が完了しました。")

    def close(self) -> None:
        self.session.close()

    def check_connection(self) -> None:
        """VOICEVOXへの接続確認を行う。"""
        try:
            response = self.session.get(f"{self.voicevox_url}/version", timeout=self.timeout)
            response.raise_for_status()
            logger.info("VOICEVOXに接続しました。バージョン: %s", response.text.strip())
        except requests.RequestException as exc:
            raise RuntimeError("VOICEVOXに接続できません。VOICEVOXを起動してください。") from exc
