"""Live2Dアバターの表示とパラメータ制御。"""

from __future__ import annotations

import ctypes
import importlib.util
import json
import logging
import math
import os
import random
import threading
import time

from pathlib import Path
from typing import Any


logger = logging.getLogger(__name__)


class Live2DViewer:
    """GLFWウィンドウでLive2Dモデルを描画するビューア。"""

    CORE_DLL_NAME = "Live2DCubismCore.dll"
    STARTUP_TIMEOUT = 30.0
    SHUTDOWN_TIMEOUT = 15.0
    BLINK_DURATION = 0.24

    def __init__(
        self,
        model_path: str,
        width: int = 480,
        height: int = 640,
        mouth_scale: float = 3.0,
    ) -> None:
        """モデルパス、ウィンドウサイズ、口パク倍率を設定する。"""
        self.model_path = Path(model_path).expanduser().resolve()
        self.width = max(1, int(width))
        self.height = max(1, int(height))
        self.mouth_scale = max(0.0, float(mouth_scale))

        self._stop_event = threading.Event()
        self._ready_event = threading.Event()
        self._mouth_lock = threading.Lock()
        self._state_lock = threading.Lock()

        self._mouth_open = 0.0
        self._running = False
        self._startup_success = False
        self._thread: threading.Thread | None = None

        self._dll_directory_handle: Any | None = None
        self._core_dll_handle: Any | None = None
        self._idle_motion_count = 0

    def _check_cancelled(self) -> None:
        """起動キャンセル要求を確認する。"""
        if self._stop_event.is_set():
            raise RuntimeError("Live2Dの起動がキャンセルされました。")

    def _find_core_dll(self) -> Path | None:
        """Cubism Core DLLを既知の配置場所から検索する。"""
        candidates: list[Path] = []

        env_path = os.environ.get("LIVE2D_CORE_DLL")
        if env_path:
            candidates.append(Path(env_path).expanduser())

        project_root = Path(__file__).resolve().parent.parent
        candidates.extend(
            [
                project_root / self.CORE_DLL_NAME,
                project_root / "Core" / "dll" / "windows" / "x86_64" / self.CORE_DLL_NAME,
            ]
        )

        try:
            candidates.extend(
                project_root.glob(
                    "*/Core/dll/windows/x86_64/" + self.CORE_DLL_NAME
                )
            )
        except OSError:
            logger.debug("Cubism SDKフォルダの検索に失敗しました。", exc_info=True)

        try:
            spec = importlib.util.find_spec("live2d")
            if spec is not None and spec.origin:
                candidates.append(
                    Path(spec.origin).parent / self.CORE_DLL_NAME
                )
        except (ImportError, ValueError, AttributeError):
            pass

        for directory in os.environ.get("PATH", "").split(os.pathsep):
            if directory:
                candidates.append(Path(directory) / self.CORE_DLL_NAME)

        for candidate in candidates:
            try:
                resolved = candidate.resolve()
                if resolved.is_file():
                    return resolved
            except (OSError, ValueError):
                continue

        return None

    def _release_dll_resources(self) -> None:
        """DLLハンドルとDLL検索パスを解放する。"""
        self._core_dll_handle = None

        if self._dll_directory_handle is not None:
            try:
                self._dll_directory_handle.close()
            except OSError:
                logger.debug(
                    "DLL検索パスの解放に失敗しました。",
                    exc_info=True,
                )
            finally:
                self._dll_directory_handle = None

    def _prepare_core_dll(self) -> bool:
        """Cubism Core DLLを読み込み、依存DLLの検索パスを登録する。"""
        if os.name != "nt":
            logger.warning(
                "Live2DビューアはWindows環境を対象としています。"
            )
            return False

        dll_path = self._find_core_dll()
        if dll_path is None:
            logger.warning(
                "%s が見つかりません。"
                "Cubism Native SDKのDLLをプロジェクト直下、"
                "またはLIVE2D_CORE_DLLで指定した場所に配置してください。"
                "アバターなしで続行します。",
                self.CORE_DLL_NAME,
            )
            return False

        try:
            self._dll_directory_handle = os.add_dll_directory(
                str(dll_path.parent)
            )
            self._core_dll_handle = ctypes.WinDLL(str(dll_path))
            logger.info("Cubism Core DLLを読み込みました: %s", dll_path)
            return True
        except (OSError, AttributeError) as exc:
            logger.warning(
                "Cubism Core DLLを読み込めません。"
                "PythonとDLLのビット数およびDLLの依存関係を確認してください。"
                "原因: %s",
                exc,
            )
            self._release_dll_resources()
            return False

    @staticmethod
    def _check_dependencies() -> bool:
        """Live2D描画用Pythonパッケージの存在を確認する。"""
        for module_name in ("live2d", "glfw", "OpenGL"):
            try:
                available = importlib.util.find_spec(module_name) is not None
            except (ImportError, ValueError, ModuleNotFoundError):
                available = False

            if not available:
                logger.warning(
                    "必要なPythonパッケージ %s がありません。"
                    "pip install -r requirements.txt を実行してください。"
                    "アバターなしで続行します。",
                    module_name,
                )
                return False
        return True

    def _load_model_metadata(self) -> bool:
        """モデルJSONを検証し、Idleモーション数を取得する。"""
        if not self.model_path.is_file():
            logger.warning(
                "Live2Dモデルが見つかりません: %s。アバターなしで続行します。",
                self.model_path,
            )
            return False

        try:
            with self.model_path.open("r", encoding="utf-8-sig") as file:
                data = json.load(file)
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning(
                "Live2DモデルJSONを読み込めません: %s。原因: %s",
                self.model_path,
                exc,
            )
            return False

        if not isinstance(data, dict):
            logger.warning("Live2DモデルJSONのルートがオブジェクトではありません。")
            return False

        references = data.get("FileReferences")
        if not isinstance(references, dict):
            logger.warning("モデルJSONにFileReferencesがありません。")
            return False

        motions = references.get("Motions", {})
        if not isinstance(motions, dict):
            motions = {}

        idle_motions = motions.get("Idle", [])
        self._idle_motion_count = (
            len(idle_motions) if isinstance(idle_motions, list) else 0
        )
        return True

    def start(self) -> bool:
        """Live2Dウィンドウと描画スレッドを開始する。"""
        with self._state_lock:
            if self._thread is not None:
                if self._thread.is_alive():
                    return self._startup_success and self._running
                self._thread.join()
                self._thread = None

            self._startup_success = False
            self._running = False
            self._ready_event.clear()
            self._stop_event.clear()

        try:
            if not self._load_model_metadata():
                return False
            if not self._check_dependencies():
                return False
            if not self._prepare_core_dll():
                return False

            thread = threading.Thread(
                target=self._render_loop,
                name="Live2DViewer",
                daemon=True,
            )
            with self._state_lock:
                self._thread = thread
            thread.start()

        except Exception as exc:
            logger.warning(
                "Live2D描画スレッドを開始できません。"
                "アバターなしで続行します。原因: %s",
                exc,
                exc_info=True,
            )
            with self._state_lock:
                self._thread = None
            self._release_dll_resources()
            return False

        if not self._ready_event.wait(self.STARTUP_TIMEOUT):
            logger.warning(
                "Live2Dの初期化がタイムアウトしました。起動をキャンセルします。"
            )
            self._stop_event.set()
            thread.join(timeout=self.SHUTDOWN_TIMEOUT)
            if thread.is_alive():
                logger.warning(
                    "Live2D描画スレッドは初期化処理の終了を待っています。"
                )
            else:
                with self._state_lock:
                    if self._thread is thread:
                        self._thread = None
                self._release_dll_resources()
            return False

        if not self._startup_success:
            thread.join(timeout=1.0)
            if not thread.is_alive():
                with self._state_lock:
                    if self._thread is thread:
                        self._thread = None
            return False

        return self.is_running

    def set_mouth_open(self, value: float) -> None:
        """口の開き具合を0.0〜1.0に制限してスレッドセーフに保持する。"""
        try:
            mouth_value = float(value)
        except (TypeError, ValueError):
            mouth_value = 0.0

        if not math.isfinite(mouth_value):
            mouth_value = 0.0

        with self._mouth_lock:
            self._mouth_open = max(0.0, min(1.0, mouth_value))

    def _get_mouth_open(self) -> float:
        """描画スレッド用に口の開き具合を取得する。"""
        with self._mouth_lock:
            return self._mouth_open

    def _render_loop(self) -> None:
        """GLFW・OpenGL・Live2Dの操作を専用スレッド内で完結させる。"""
        glfw = None
        live2d = None
        model = None
        window = None
        gl = None

        glfw_initialized = False
        live2d_initialized = False
        renderer_created = False
        startup_reported = False

        try:
            # Core DLLを読み込んだ後にネイティブ拡張をimportする。
            import glfw as glfw_module
            import live2d as live2d_module
            from OpenGL import GL

            glfw = glfw_module
            live2d = live2d_module
            gl = GL

            self._check_cancelled()

            if not glfw.init():
                raise RuntimeError("GLFWの初期化に失敗しました。")
            glfw_initialized = True

            self._check_cancelled()

            window = glfw.create_window(
                self.width,
                self.height,
                "春日部つむぎAI - Live2D",
                None,
                None,
            )
            if not window:
                raise RuntimeError("Live2Dウィンドウを作成できません。")

            glfw.make_context_current(window)
            glfw.swap_interval(1)

            self._check_cancelled()

            live2d.init()
            live2d_initialized = True
            live2d.glInit()

            self._check_cancelled()

            model = live2d.Model()
            model.LoadModelJson(
                str(self.model_path),
                create_renderer=False,
            )
            self._check_cancelled()

            model.CreateRenderer()
            renderer_created = True
            model.Resize(self.width, self.height)

            model.SetAutoBlink(False)
            model.SetAutoBreath(False)

            parameter_ids = set(model.GetParamIds())
            if "ParamMouthOpenY" not in parameter_ids:
                logger.warning(
                    "モデルにParamMouthOpenYがありません。口パクは動作しません。"
                )

            def on_framebuffer_resize(
                resized_window: Any,
                width: int,
                height: int,
            ) -> None:
                """フレームバッファ変更を描画モデルへ反映する。"""
                if width <= 0 or height <= 0:
                    return
                gl.glViewport(0, 0, width, height)
                model.Resize(width, height)

            glfw.set_framebuffer_size_callback(
                window,
                on_framebuffer_resize,
            )
            on_framebuffer_resize(
                window,
                *glfw.get_framebuffer_size(window),
            )

            if self._idle_motion_count > 0:
                try:
                    model.StartMotion(
                        "Idle",
                        0,
                        live2d.MotionPriority.IDLE,
                    )
                    logger.info("Live2DのIdleモーションを開始しました。")
                except Exception as exc:
                    logger.warning("Idleモーションを開始できません: %s", exc)
            else:
                logger.info("モデルにIdleモーションはありません。")

            self._check_cancelled()

            initial_time = time.monotonic()
            last_frame_time = initial_time
            next_blink_time = initial_time + random.uniform(3.0, 5.0)
            blink_start_time: float | None = None

            with self._state_lock:
                self._startup_success = True
                self._running = True

            logger.info(
                "Live2Dビューアを起動しました。サイズ: %dx%d",
                self.width,
                self.height,
            )
            self._ready_event.set()
            startup_reported = True

            while (
                not self._stop_event.is_set()
                and not glfw.window_should_close(window)
            ):
                glfw.poll_events()

                now = time.monotonic()
                delta_time = max(0.0, min(now - last_frame_time, 0.1))
                last_frame_time = now

                model.Update(delta_time)

                if "ParamMouthOpenY" in parameter_ids:
                    model.SetParamById(
                        "ParamMouthOpenY",
                        self._get_mouth_open(),
                    )

                # 数秒ごとに短いまばたきを行う。
                if blink_start_time is None and now >= next_blink_time:
                    blink_start_time = now

                blink_open = 1.0
                if blink_start_time is not None:
                    progress = (
                        now - blink_start_time
                    ) / self.BLINK_DURATION

                    if progress >= 1.0:
                        blink_start_time = None
                        next_blink_time = now + random.uniform(3.0, 5.0)
                    elif progress < 0.5:
                        blink_open = 1.0 - progress * 2.0
                    else:
                        blink_open = (progress - 0.5) * 2.0

                    blink_open = max(0.0, min(1.0, blink_open))

                if "ParamEyeLOpen" in parameter_ids:
                    model.SetParamById("ParamEyeLOpen", blink_open)
                if "ParamEyeROpen" in parameter_ids:
                    model.SetParamById("ParamEyeROpen", blink_open)

                if "ParamBreath" in parameter_ids:
                    elapsed = now - initial_time
                    breath_value = (
                        0.5
                        + 0.5 * math.sin(
                            elapsed * 2.0 * math.pi / 4.5
                        )
                    )
                    model.SetParamById("ParamBreath", breath_value)

                live2d.clearBuffer(0.96, 0.96, 0.98, 1.0)
                model.Draw()
                glfw.swap_buffers(window)

        except Exception as exc:
            if self._stop_event.is_set() and not startup_reported:
                logger.info("Live2Dの起動をキャンセルしました。")
            elif not startup_reported:
                logger.warning(
                    "Live2Dの起動に失敗しました。"
                    "Cubism Core DLL、OpenGL、モデル構成を確認してください。"
                    "原因: %s",
                    exc,
                    exc_info=True,
                )
            else:
                logger.exception("Live2Dの描画中にエラーが発生しました。")

        finally:
            with self._mouth_lock:
                self._mouth_open = 0.0

            with self._state_lock:
                self._running = False
                if not startup_reported:
                    self._startup_success = False

            if not startup_reported:
                self._ready_event.set()

            if glfw is not None and window:
                try:
                    glfw.make_context_current(window)
                except Exception:
                    logger.debug("OpenGLコンテキストの再設定に失敗しました。", exc_info=True)

            if model is not None and renderer_created:
                try:
                    model.DestroyRenderer()
                except Exception:
                    logger.debug("Live2D Rendererの解放に失敗しました。", exc_info=True)

            if live2d is not None and live2d_initialized:
                try:
                    live2d.dispose()
                except Exception:
                    logger.debug("Live2Dの解放に失敗しました。", exc_info=True)

            if glfw is not None:
                if window:
                    try:
                        glfw.destroy_window(window)
                    except Exception:
                        logger.debug("Live2Dウィンドウの解放に失敗しました。", exc_info=True)

                if glfw_initialized:
                    try:
                        glfw.terminate()
                    except Exception:
                        logger.debug("GLFWの終了に失敗しました。", exc_info=True)

            self._release_dll_resources()
            logger.info("Live2D描画スレッドを終了しました。")

    def close(self) -> None:
        """描画ループを停止し、スレッド終了後にリソースを解放する。"""
        self.set_mouth_open(0.0)
        self._stop_event.set()

        with self._state_lock:
            thread = self._thread

        if thread is None:
            self._release_dll_resources()
            return

        if thread is not threading.current_thread() and thread.is_alive():
            thread.join(timeout=self.SHUTDOWN_TIMEOUT)

        if thread.is_alive():
            logger.warning(
                "Live2D描画スレッドが終了していません。"
                "スレッド側で終了処理を継続します。"
            )
            return

        with self._state_lock:
            if self._thread is thread:
                self._thread = None
            self._running = False

        self._release_dll_resources()

    @property
    def is_running(self) -> bool:
        """Live2D描画スレッドが起動済みかつ動作中かを返す。"""
        with self._state_lock:
            return self._running
