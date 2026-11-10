from __future__ import annotations
import sys
import threading
import time
from contextlib import contextmanager
from typing import Iterator


def _bar(ratio: float, width: int) -> str:
    """0.0〜1.0 の割合をバーの文字列にする。"""
    ratio = max(0.0, min(1.0, ratio))
    filled = int(round(ratio * width))
    return "█" * filled + "░" * (width - filled)


class ConsoleUI:
    """コンソールの1行を使って状態をバー表示する。

    - 処理中: ラベル + 流れるバー（不定形プログレス）
    - 再生中: 再生進捗バー + 音量（RMS）メーター

    描画は専用スレッドが一定間隔で行い、同じ行を上書きし続ける。
    """

    def __init__(self, stream=None) -> None:
        self._stream = stream if stream is not None else sys.stdout
        self._mode = "idle"
        self._label = ""
        self._progress = 0.0
        self._rms = 0.0
        self._tick = 0
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    # 描画 -------------------------------------------------------------

    def _render(self) -> None:
        with self._lock:
            mode = self._mode
            label = self._label
            progress = self._progress
            rms = self._rms
            self._tick += 1
            tick = self._tick
        if mode == "playback":
            level = min(1.0, rms * 3.0)
            line = (
                f"[再生中] {_bar(progress, 24)} {progress * 100:5.1f}%"
                f"  音量 {_bar(level, 10)}"
            )
        else:
            width = 24
            block = 6
            span = width + block
            pos = tick % span - block
            cells = "".join(
                "█" if pos <= i < pos + block else "░" for i in range(width)
            )
            line = f"[処理中] {label} {cells}"
        self._stream.write("\r" + line + "   ")
        self._stream.flush()

    def _clear_line(self) -> None:
        self._stream.write("\r" + " " * 100 + "\r")
        self._stream.flush()

    def _run(self) -> None:
        while not self._stop_event.is_set():
            self._render()
            self._stop_event.wait(0.1)

    # 制御 -------------------------------------------------------------

    def start(self, label: str) -> None:
        self.stop()
        with self._lock:
            self._mode = "processing"
            self._label = label
            self._progress = 0.0
            self._rms = 0.0
            self._tick = 0
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._thread is not None:
            self._stop_event.set()
            self._thread.join(timeout=1.0)
            self._thread = None
            self._clear_line()
        with self._lock:
            self._mode = "idle"

    def finish(self) -> None:
        """完了表示にする: バーを全部塗りつぶした状態で1行残す。"""
        with self._lock:
            mode = self._mode
            label = self._label
        if self._thread is not None:
            self._stop_event.set()
            self._thread.join(timeout=1.0)
            self._thread = None
        if mode == "playback":
            line = f"[再生中] {_bar(1.0, 24)} 100.0%  音量 {_bar(0.0, 10)}"
        else:
            line = f"[処理中] {label} {_bar(1.0, 24)} 完了"
        self._stream.write("\r" + line + "   \n")
        self._stream.flush()
        with self._lock:
            self._mode = "idle"
            self._progress = 0.0
            self._rms = 0.0

    @contextmanager
    def processing(self, label: str) -> Iterator[None]:
        """処理中バーを表示するコンテキストマネージャ。

        正常に終わった場合だけ完了表示（バーを塗りつぶして残す）にし、
        例外時は行を消すだけにする。
        """
        self.start(label)
        succeeded = False
        try:
            yield
            succeeded = True
        finally:
            if succeeded:
                self.finish()
            else:
                self.stop()

    # TTSからの進捗通知 -------------------------------------------------

    def update_playback(self, progress: float, rms: float) -> None:
        """再生進捗と音量を受け取り、再生中バーに切り替える。"""
        with self._lock:
            self._mode = "playback"
            self._progress = progress
            self._rms = rms
        if self._thread is None:
            self._render()
