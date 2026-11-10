import io
import logging
import queue
import re
import threading
import time
import wave
from typing import Any, Callable

import numpy as np
import requests
import sounddevice as sd

logger = logging.getLogger(__name__)

RMSCallback = Callable[[float], None]
PlaybackCallback = Callable[[float, float], None]


class VoiceVoxTTS:
    """VOICEVOX APIと連携し、音声を合成・再生する。"""

    def __init__(
        self,
        voicevox_url: str,
        speaker: int = 8,
        timeout: float = 30.0,
        volume: float = 1.0,
        rms_callback: RMSCallback | None = None,
        split_by_sentence: bool = False,
        playback_callback: PlaybackCallback | None = None,
        stop_event: threading.Event | None = None,
    ) -> None:
        self.voicevox_url = voicevox_url.rstrip("/")
        self.speaker = speaker
        self.timeout = timeout
        self.volume = max(0.0, min(float(volume), 2.0))
        self.rms_callback = rms_callback
        self.split_by_sentence = split_by_sentence
        self.playback_callback = playback_callback
        self.stop_event = stop_event
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "KasukabeTsumugiAI/1.0"})
        self._stream_lock = threading.Lock()
        self._stream_sessions: set[VoiceVoxStreamingSession] = set()

    @staticmethod
    def split_sentences(text: str) -> list[str]:
        """文章を句読点で文単位に分割する。"""
        cleaned = re.sub(r"\s+", " ", text).strip()
        if not cleaned:
            return []
        sentences = re.split(r"(?<=[。！？!?])\s*", cleaned)
        return [sentence.strip() for sentence in sentences if sentence.strip()]

    def _create_audio_query(self, sentence: str, timeout: float | None = None) -> dict[str, Any]:
        """文章から音声合成用クエリを生成する。"""
        if self.stop_event is not None and self.stop_event.is_set():
            raise RuntimeError("音声合成は停止要求により中断されました。")
        try:
            response = self.session.post(
                f"{self.voicevox_url}/audio_query",
                params={"text": sentence, "speaker": self.speaker},
                timeout=timeout if timeout is not None else self.timeout,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            raise RuntimeError("VOICEVOXの音声合成クエリを取得できませんでした。VOICEVOXの起動状態を確認してください。") from exc
        try:
            return response.json()
        except ValueError as exc:
            raise RuntimeError("VOICEVOXの応答をJSONとして読み取れませんでした。") from exc

    def _synthesize(self, audio_query: dict[str, Any], timeout: float | None = None) -> bytes:
        """音声合成クエリからWAV音声を生成する。"""
        if self.stop_event is not None and self.stop_event.is_set():
            raise RuntimeError("音声合成は停止要求により中断されました。")
        try:
            response = self.session.post(
                f"{self.voicevox_url}/synthesis",
                params={"speaker": self.speaker},
                json=audio_query,
                timeout=timeout if timeout is not None else self.timeout,
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
        if self.stop_event is not None and self.stop_event.is_set():
            return
        audio, sample_rate, channels = self._decode_wav(wav_data)
        audio = np.clip(audio * self.volume, -1.0, 1.0).astype(np.float32)
        # ステレオはフレーム単位に整形する（VOICEVOX出力は通常モノラル）
        if channels == 2:
            audio = audio.reshape(-1, 2)
        position = 0
        block_size = 1024
        last_playback_notice = 0.0

        def callback(outdata, frames, time_info, status):
            nonlocal position, last_playback_notice
            if self.stop_event is not None and self.stop_event.is_set():
                outdata.fill(0)
                raise sd.CallbackStop
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
            rms = float(np.sqrt(np.mean(np.square(flat)))) if flat.size else 0.0
            if self.rms_callback is not None:
                try:
                    self.rms_callback(rms)
                except Exception:
                    logger.warning("RMSコールバックの呼び出しに失敗しました。", exc_info=True)
            if self.playback_callback is not None:
                now = time.monotonic()
                if now - last_playback_notice >= 0.1 or position >= total:
                    last_playback_notice = now
                    try:
                        self.playback_callback(position / total if total else 1.0, rms)
                    except Exception:
                        logger.warning("再生進捗コールバックの呼び出しに失敗しました。", exc_info=True)

        if self.playback_callback is not None:
            try:
                self.playback_callback(0.0, 0.0)
            except Exception:
                logger.warning("再生開始コールバックの呼び出しに失敗しました。", exc_info=True)

        try:
            with sd.OutputStream(
                samplerate=sample_rate,
                channels=channels,
                dtype="float32",
                blocksize=block_size,
                callback=callback,
            ):
                while position < len(audio):
                    if self.stop_event is not None and self.stop_event.is_set():
                        break
                    time.sleep(0.05)
        except sd.PortAudioError as exc:
            raise RuntimeError("音声を再生できません。Windowsの出力デバイスやサウンド設定を確認してください。") from exc
        if (
            self.playback_callback is not None
            and (self.stop_event is None or not self.stop_event.is_set())
        ):
            try:
                self.playback_callback(1.0, 0.0)
            except Exception:
                pass

    def speak(self, text: str) -> None:
        """テキストを合成・再生する。

        既定では全文を一括で合成してから再生する（文ごとに分けると
        合成待ちの間が空いてしまうため）。
        split_by_sentence が True の場合は従来通り文単位で処理する。
        """
        if self.stop_event is not None and self.stop_event.is_set():
            return
        if self.split_by_sentence:
            sentences = self.split_sentences(text)
            if not sentences:
                logger.warning("読み上げる文章がありません。")
                return
            total = len(sentences)
            for index, sentence in enumerate(sentences, start=1):
                if self.stop_event is not None and self.stop_event.is_set():
                    return
                logger.info("音声合成中 (%d/%d): %s", index, total, sentence)
                query = self._create_audio_query(sentence)
                wav_data = self._synthesize(query)
                logger.info("音声を再生します。")
                self._play_audio(wav_data)
                logger.info("音声再生が完了しました。")
            return
        cleaned = re.sub(r"\s+", " ", text).strip()
        if not cleaned:
            logger.warning("読み上げる文章がありません。")
            return
        # 長文の一括合成は時間がかかるため、タイムアウトを長めにする
        long_timeout = max(self.timeout, 120.0)
        logger.info("音声を一括合成しています: %s", cleaned)
        query = self._create_audio_query(cleaned, timeout=long_timeout)
        wav_data = self._synthesize(query, timeout=long_timeout)
        logger.info("音声を再生します。")
        self._play_audio(wav_data)
        logger.info("音声再生が完了しました。")

    def start_streaming(self) -> VoiceVoxStreamingSession:
        """生成中の文を先行合成するセッションを開始する。"""
        if self.stop_event is not None and self.stop_event.is_set():
            raise RuntimeError("音声合成は停止要求により中断されています。")
        session = VoiceVoxStreamingSession(self)
        with self._stream_lock:
            self._stream_sessions.add(session)
        return session

    def _release_streaming_session(self, session: VoiceVoxStreamingSession) -> None:
        with self._stream_lock:
            self._stream_sessions.discard(session)

    @staticmethod
    def concatenate_wavs(wav_parts: list[bytes]) -> bytes | None:
        """形式が一致するPCM WAVだけを無音追加なしで連結する。"""
        if not wav_parts:
            return None
        frame_data: list[bytes] = []
        expected: tuple[int, int, int] | None = None
        try:
            for wav_data in wav_parts:
                with wave.open(io.BytesIO(wav_data), "rb") as source:
                    current = (
                        source.getframerate(),
                        source.getnchannels(),
                        source.getsampwidth(),
                    )
                    if expected is None:
                        expected = current
                    elif current != expected:
                        logger.warning(
                            "WAV形式が一致しないため文単位の再生に切り替えます。"
                        )
                        return None
                    if source.getcomptype() != "NONE":
                        logger.warning(
                            "非PCM WAVのため文単位の再生に切り替えます。"
                        )
                        return None
                    frame_data.append(source.readframes(source.getnframes()))
            if expected is None:
                return None
            sample_rate, channels, sample_width = expected
            output = io.BytesIO()
            with wave.open(output, "wb") as target:
                target.setnchannels(channels)
                target.setsampwidth(sample_width)
                target.setframerate(sample_rate)
                target.writeframes(b"".join(frame_data))
            return output.getvalue()
        except (wave.Error, OSError, EOFError, ValueError):
            logger.warning(
                "WAV連結に失敗したため文単位の再生に切り替えます。",
                exc_info=True,
            )
            return None

    def close(self) -> None:
        with self._stream_lock:
            sessions = list(self._stream_sessions)
        for session in sessions:
            session.cancel()
        self.session.close()

    def check_connection(self) -> None:
        """VOICEVOXへの接続確認を行う。"""
        try:
            response = self.session.get(f"{self.voicevox_url}/version", timeout=self.timeout)
            response.raise_for_status()
            logger.info("VOICEVOXに接続しました。バージョン: %s", response.text.strip())
        except requests.RequestException as exc:
            raise RuntimeError("VOICEVOXに接続できません。VOICEVOXを起動してください。") from exc


class VoiceVoxStreamingSession:
    """Gemini生成中に確定した文を順次合成し、再生前にまとめる。"""

    def __init__(self, tts: VoiceVoxTTS) -> None:
        self._tts = tts
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._lock = threading.Lock()
        self._raw_text = ""
        self._body_snapshot = ""
        self._confirmed_count = 0
        self._sentences: list[str] = []
        self._wav_parts: list[bytes] = []
        self._error: Exception | None = None
        self._aborted = False
        self._cancelled = False
        self._completed = False
        self._thread = threading.Thread(
            target=self._synthesis_worker,
            name="VOICEVOX-Streaming-Synthesis",
            daemon=True,
        )
        self._thread.start()

    def _is_stopped(self) -> bool:
        return (
            self._tts.stop_event is not None
            and self._tts.stop_event.is_set()
        )

    def _is_cancelled(self) -> bool:
        with self._lock:
            return self._cancelled

    def _extract_body(self) -> str | None:
        """先頭の感情タグが閉じるまでは本文を公開しない。"""
        leading = self._raw_text.lstrip()
        folded = leading.casefold()
        tag_prefix = "[emotion:"
        if len(folded) < len(tag_prefix) and tag_prefix.startswith(folded):
            return None
        if folded.startswith(tag_prefix):
            match = re.match(
                r"^\s*\[emotion:[^\]\r\n]*\]\s*",
                self._raw_text,
                flags=re.IGNORECASE,
            )
            if match is None:
                return None
            return self._raw_text[match.end():]
        return self._raw_text

    def feed_text(self, chunk: str) -> None:
        """LLMから届いた追記分を蓄積し、後続文字のある確定文だけをキューへ送る。"""
        if not chunk or self._is_stopped():
            return
        with self._lock:
            if self._cancelled or self._completed:
                return
            self._raw_text += chunk

        body = self._extract_body()
        if body is None:
            return
        body = re.sub(
            r"\[emotion:[^\]\r\n]*\]",
            "",
            body,
            flags=re.IGNORECASE,
        )
        normalized = re.sub(r"\s+", " ", body).strip()
        if self._body_snapshot and not normalized.startswith(self._body_snapshot):
            logger.warning(
                "ストリーミング本文が以前の内容と矛盾したため、先行合成を破棄します。"
            )
            self.abort()
            return
        self._body_snapshot = normalized

        parts = self._tts.split_sentences(normalized)
        confirmed = parts[:-1] if len(parts) > 1 else []
        while self._confirmed_count < len(confirmed):
            sentence = confirmed[self._confirmed_count]
            self._enqueue_sentence(sentence)
            self._confirmed_count += 1

    def _enqueue_sentence(self, sentence: str) -> None:
        if not sentence or self._is_stopped():
            return
        with self._lock:
            if self._cancelled or self._completed:
                return
            self._sentences.append(sentence)
            self._queue.put(sentence)

    def _synthesis_worker(self) -> None:
        try:
            while True:
                sentence = self._queue.get()
                try:
                    if sentence is None:
                        return
                    if self._is_stopped() or self._is_cancelled():
                        return
                    query = self._tts._create_audio_query(sentence)
                    if self._is_stopped() or self._is_cancelled():
                        return
                    wav_data = self._tts._synthesize(query)
                    if self._is_stopped() or self._is_cancelled():
                        return
                    with self._lock:
                        self._wav_parts.append(wav_data)
                except Exception as exc:
                    with self._lock:
                        self._error = exc
                        self._cancelled = True
                    logger.warning(
                        "先行音声合成に失敗しました。完了後の通常合成へ切り替えます: %s",
                        exc,
                        exc_info=True,
                    )
                    return
                finally:
                    self._queue.task_done()
        finally:
            self._tts._release_streaming_session(self)

    def _wait_for_worker(self) -> bool:
        while self._thread.is_alive():
            if self._is_stopped():
                self.cancel()
                return False
            self._thread.join(timeout=0.1)
        return not self._is_stopped()

    def _discard_queued(self) -> None:
        while True:
            try:
                self._queue.get_nowait()
                self._queue.task_done()
            except queue.Empty:
                break

    def abort(self) -> None:
        """LLM側で既出テキストの書き換えを検知した場合、先行合成を破棄する。"""
        with self._lock:
            if self._completed or self._aborted:
                return
            self._aborted = True
            self._cancelled = True
            self._discard_queued()
            self._queue.put(None)

    def cancel(self) -> None:
        """停止・例外時に未処理の先行合成を破棄する。"""
        with self._lock:
            if self._completed or self._cancelled:
                return
            self._cancelled = True
            self._discard_queued()
            self._queue.put(None)

    def finish(self, final_text: str) -> None:
        """最後の文を合成し、全WAVを連結してから既存経路で再生する。"""
        with self._lock:
            if self._completed:
                return
        try:
            if self._is_stopped():
                self.cancel()
                return

            final_sentences = self._tts.split_sentences(final_text)
            with self._lock:
                queued_sentences = list(self._sentences)
                was_aborted = self._aborted

            if was_aborted:
                if self._wait_for_worker() and final_text.strip():
                    logger.info("返答が書き換わったため、全文を通常方式で合成します。")
                    self._tts.speak(final_text)
                return

            if final_sentences[:len(queued_sentences)] != queued_sentences:
                self.abort()
                if self._wait_for_worker() and final_text.strip():
                    logger.warning(
                        "最終本文と先行合成済み文が一致しないため、全文を再合成します。"
                    )
                    self._tts.speak(final_text)
                return

            for sentence in final_sentences[len(queued_sentences):]:
                self._enqueue_sentence(sentence)
            self._queue.put(None)

            if not self._wait_for_worker():
                return

            with self._lock:
                error = self._error
                wav_parts = list(self._wav_parts)

            if error is not None or len(wav_parts) != len(final_sentences):
                if final_text.strip() and not self._is_stopped():
                    logger.warning("先行合成を完了できなかったため、全文を通常方式で合成します。")
                    self._tts.speak(final_text)
                return

            if not wav_parts:
                return

            combined = self._tts.concatenate_wavs(wav_parts)
            if combined is not None:
                logger.info("先行合成した%d文のWAVを連結して再生します。", len(wav_parts))
                if not self._is_stopped():
                    self._tts._play_audio(combined)
                return

            logger.warning("WAVの形式不一致または連結失敗のため、文単位で連続再生します。")
            for wav_data in wav_parts:
                if self._is_stopped():
                    break
                self._tts._play_audio(wav_data)
        finally:
            with self._lock:
                self._completed = True
            self._tts._release_streaming_session(self)
