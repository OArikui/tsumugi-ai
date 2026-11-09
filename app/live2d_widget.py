"""PySide6 QOpenGLWidget上にLive2Dモデルを描画する。"""

from __future__ import annotations

import logging
import math
import random
import threading
import time
from pathlib import Path
from typing import Any

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtOpenGLWidgets import QOpenGLWidget

from app.live2d_viewer import Live2DViewer

logger = logging.getLogger(__name__)


class Live2DWidget(QOpenGLWidget):
    """Live2DViewerの制御機能を共有し、描画だけをQtのOpenGLコンテキストで行う。"""

    def __init__(self, config: dict, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumSize(300, 360)

        project_root = Path(__file__).resolve().parent.parent
        model_path = Path(
            str(config.get(
                "live2d_model_path",
                "./assets/live2d/tsumugi/春日部つむぎ公式live2Dモデル.model3.json",
            ))
        ).expanduser()
        if not model_path.is_absolute():
            model_path = project_root / model_path

        self._enabled = bool(config.get("live2d_enabled", True))
        self._controller = Live2DViewer(
            model_path=str(model_path.resolve()),
            width=int(config.get("live2d_width", 480)),
            height=int(config.get("live2d_height", 640)),
            mouth_scale=float(config.get("live2d_mouth_scale", 3.0)),
            mouth_param=str(config.get("live2d_mouth_param", "")),
            idle_sway=bool(config.get("live2d_idle_sway", True)),
            expressions=config.get("live2d_expressions"),
            natural_idle=bool(config.get("live2d_natural_idle", True)),
        )

        self._model: Any | None = None
        self._live2d: Any | None = None
        self._gl: Any | None = None
        self._live2d_initialized = False
        self._renderer_created = False
        self._model_ready = False
        self._shutdown = False
        self._fallback_message = (
            "Live2Dは設定で無効です。"
            if not self._enabled
            else "Live2Dを初期化しています..."
        )

        self._resolved_parameters: dict[str, str] = {}
        self._resolved_sway: dict[str, str] = {}
        self._resolved_face: dict[str, str] = {}
        self._resolved_natural: dict[str, str] = {}

        self._activity_lock = threading.Lock()
        self._activity = "idle"

        now = time.monotonic()
        self._initial_time = now
        self._last_frame_time = now
        self._next_blink_time = now + random.uniform(3.0, 5.0)
        self._blink_start_time: float | None = None
        self._last_expression_revision = -1
        self._use_face_fallback = False
        self._active_face_preset: dict[str, float] = {}
        self._rest_started_at: float | None = None
        self._next_rest_close_time: float | None = None
        self._rest_close_start_time: float | None = None
        self._next_rest_close_delay = random.uniform(6.0, 12.0)
        self._rest_gaze_phase = random.uniform(0.0, math.tau)

        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self.update)
        self._timer.start()

    @property
    def is_running(self) -> bool:
        """Live2DモデルがQt描画コンテキストで初期化済みかを返す。"""
        return self._model_ready and not self._shutdown

    def set_mouth_open(self, value: float) -> None:
        """口パク値をスレッドセーフに更新する。"""
        self._controller.set_mouth_open(value)

    def set_emotion(self, emotion: str) -> None:
        """感情をスレッドセーフに受け取り、次の描画フレームで反映する。"""
        self._controller.set_emotion(emotion)

    def set_expression(self, name: str | None) -> None:
        """表情名をスレッドセーフに受け取り、次の描画フレームで反映する。"""
        self._controller.set_expression(name)

    def set_activity(self, activity: str) -> None:
        """GUI状態に応じた簡単なアバター動作を設定する。"""
        with self._activity_lock:
            self._activity = activity
        self.update()

    def _get_activity(self) -> str:
        with self._activity_lock:
            return self._activity

    def initializeGL(self) -> None:
        """Qtが用意したOpenGLコンテキスト内でCubismを初期化する。"""
        try:
            from OpenGL import GL

            self._gl = GL
            if not self._enabled:
                return
            if not self._controller._load_model_metadata():
                self._fallback_message = "モデルファイルを読み込めません。"
                return
            if not self._controller._check_dependencies():
                self._fallback_message = "Live2DのPythonパッケージがありません。"
                return
            if not self._controller._prepare_core_dll():
                self._fallback_message = "Cubism Core DLLを利用できません。"
                return

            import live2d

            self._live2d = live2d
            live2d.init()
            self._live2d_initialized = True
            live2d.glInit()

            model = live2d.Model()
            model.LoadModelJson(
                str(self._controller.model_path),
                create_renderer=False,
            )
            model.CreateRenderer()
            self._renderer_created = True
            model.Resize(max(1, self.width()), max(1, self.height()))
            model.SetAutoBlink(False)
            model.SetAutoBreath(False)
            self._model = model

            parameter_ids = set(model.GetParamIds())
            (
                self._resolved_parameters,
                self._resolved_sway,
                self._resolved_face,
                self._resolved_natural,
            ) = self._controller._resolve_model_parameters(parameter_ids)

            if self._controller._idle_motion_count > 0:
                try:
                    model.StartMotion(
                        "Idle",
                        0,
                        live2d.MotionPriority.IDLE,
                    )
                    logger.info("Live2DのIdleモーションを開始しました。")
                except Exception as exc:
                    logger.warning("Live2DのIdleモーションを開始できません: %s", exc)

            self._initial_time = time.monotonic()
            self._last_frame_time = self._initial_time
            self._model_ready = True
            self._fallback_message = ""
            logger.info("Live2DをGUIウィンドウへ統合しました。")
        except Exception as exc:
            self._fallback_message = f"Live2Dを利用できません。\n{exc}"
            logger.warning(
                "GUI内のLive2D初期化に失敗しました。"
                "アバターなしでテキスト会話と音声を継続します。原因: %s",
                exc,
                exc_info=True,
            )
            self._model_ready = False

    def resizeGL(self, width: int, height: int) -> None:
        if self._gl is not None:
            ratio = self.devicePixelRatioF()
            self._gl.glViewport(
                0,
                0,
                max(1, int(width * ratio)),
                max(1, int(height * ratio)),
            )
        if self._model is not None:
            self._model.Resize(max(1, width), max(1, height))

    def _draw_placeholder(self) -> None:
        if self._gl is not None:
            self._gl.glClearColor(0.10, 0.12, 0.16, 1.0)
            self._gl.glClear(self._gl.GL_COLOR_BUFFER_BIT)
        painter = QPainter(self)
        painter.setPen(QColor("#D7DEE8"))
        painter.drawText(
            self.rect().adjusted(24, 24, -24, -24),
            Qt.AlignmentFlag.AlignCenter,
            self._fallback_message or "Live2Dを利用できません。",
        )
        painter.end()

    def paintGL(self) -> None:
        """描画・モデルパラメータ更新はQt UIスレッド内で行う。"""
        if not self._model_ready or self._model is None or self._live2d is None:
            self._draw_placeholder()
            return

        try:
            now = time.monotonic()
            delta_time = max(0.0, min(now - self._last_frame_time, 0.1))
            self._last_frame_time = now

            emotion, expression_name, expression_revision = (
                self._controller._get_expression_request()
            )
            if expression_revision != self._last_expression_revision:
                (
                    self._use_face_fallback,
                    self._active_face_preset,
                ) = self._controller._activate_expression(
                    self._model,
                    emotion,
                    expression_name,
                    self._resolved_face,
                )
                self._last_expression_revision = expression_revision

            self._model.Update(delta_time)
            if self._use_face_fallback:
                self._controller._apply_face_preset(
                    self._model,
                    self._resolved_face,
                    self._active_face_preset,
                )

            mouth_id = self._resolved_parameters.get("mouth")
            mouth_open, last_mouth_activity = self._controller._get_mouth_state()
            if mouth_id is not None:
                self._model.SetParamById(mouth_id, mouth_open)

            activity = self._get_activity()
            resting = (
                self._controller.natural_idle
                and activity != "thinking"
                and now - last_mouth_activity
                >= self._controller.NATURAL_IDLE_THRESHOLD
            )
            rest_eye_open = 1.0
            rest_elapsed = 0.0

            if resting:
                if self._rest_started_at is None:
                    self._rest_started_at = now
                    self._next_rest_close_time = now + self._next_rest_close_delay
                    logger.info("Live2Dが自然な休憩状態に入りました。")
                rest_elapsed = now - self._rest_started_at

                if (
                    self._rest_close_start_time is None
                    and self._next_rest_close_time is not None
                    and now >= self._next_rest_close_time
                ):
                    self._rest_close_start_time = now

                if self._rest_close_start_time is not None:
                    close_elapsed = now - self._rest_close_start_time
                    close_end = self._controller.REST_EYE_CLOSE_SECONDS
                    hold_end = close_end + self._controller.REST_EYE_HOLD_SECONDS
                    open_end = hold_end + self._controller.REST_EYE_OPEN_SECONDS

                    if close_elapsed < close_end:
                        rest_eye_open = 1.0 - close_elapsed / close_end
                    elif close_elapsed < hold_end:
                        rest_eye_open = 0.0
                    elif close_elapsed < open_end:
                        rest_eye_open = (
                            close_elapsed - hold_end
                        ) / self._controller.REST_EYE_OPEN_SECONDS
                    else:
                        self._rest_close_start_time = None
                        self._next_rest_close_time = now + random.uniform(18.0, 28.0)
            else:
                if self._rest_started_at is not None:
                    logger.info("Live2Dが通常の待機状態に戻りました。")
                self._rest_started_at = None
                self._next_rest_close_time = None
                self._rest_close_start_time = None

            if self._blink_start_time is None and now >= self._next_blink_time:
                self._blink_start_time = now

            blink_open = 1.0
            if self._blink_start_time is not None:
                progress = (
                    now - self._blink_start_time
                ) / self._controller.BLINK_DURATION
                if progress >= 1.0:
                    self._blink_start_time = None
                    self._next_blink_time = now + random.uniform(3.0, 5.0)
                elif progress < 0.5:
                    blink_open = 1.0 - progress * 2.0
                else:
                    blink_open = (progress - 0.5) * 2.0
                blink_open = max(0.0, min(1.0, blink_open))
            blink_open *= rest_eye_open

            for role in ("left_eye", "right_eye"):
                parameter_id = self._resolved_parameters.get(role)
                if parameter_id is not None:
                    self._model.SetParamById(parameter_id, blink_open)

            breath_id = self._resolved_parameters.get("breath")
            if breath_id is not None:
                elapsed = now - self._initial_time
                breath_value = 0.5 + 0.5 * math.sin(
                    elapsed * 2.0 * math.pi / 4.5
                )
                self._model.SetParamById(breath_id, breath_value)

            if self._controller.idle_sway and self._resolved_sway:
                elapsed = now - self._initial_time
                for role, parameter_id in self._resolved_sway.items():
                    _, amplitude, period, phase = (
                        self._controller.SWAY_PARAMETERS[role]
                    )
                    sway_value = amplitude * math.sin(
                        elapsed * 2.0 * math.pi / period + phase
                    )
                    self._model.AddParamById(parameter_id, sway_value)

            if activity == "thinking":
                gaze_y_id = self._resolved_natural.get("eye_ball_y")
                if gaze_y_id is not None:
                    self._model.AddParamById(gaze_y_id, 0.16)
            elif resting:
                gaze_x_id = self._resolved_natural.get("eye_ball_x")
                if gaze_x_id is not None:
                    gaze_x = 0.16 * math.sin(
                        rest_elapsed * 2.0 * math.pi / 11.0
                        + self._rest_gaze_phase
                    )
                    self._model.AddParamById(gaze_x_id, gaze_x)

                gaze_y_id = self._resolved_natural.get("eye_ball_y")
                if gaze_y_id is not None:
                    gaze_y = 0.10 * math.sin(
                        rest_elapsed * 2.0 * math.pi / 14.0
                        + self._rest_gaze_phase * 0.7
                    )
                    self._model.AddParamById(gaze_y_id, gaze_y)

                head_angle_id = self._resolved_natural.get("angle_x")
                if head_angle_id is not None:
                    turn_phase = rest_elapsed % 29.0
                    head_turn = (
                        1.2 * math.sin(math.pi * turn_phase / 7.0)
                        if turn_phase < 7.0
                        else 0.0
                    )
                    self._model.AddParamById(head_angle_id, head_turn)

            self._live2d.clearBuffer(0.10, 0.12, 0.16, 1.0)
            self._model.Draw()
        except Exception as exc:
            logger.exception("GUI内のLive2D描画に失敗しました。")
            self._fallback_message = f"Live2D描画を停止しました。\n{exc}"
            self._model_ready = False
            self.update()

    def shutdown(self) -> None:
        """OpenGLリソースをQtのコンテキストが有効な間に解放する。"""
        if self._shutdown:
            return
        self._shutdown = True
        self._timer.stop()

        if self._model is None and not self._live2d_initialized:
            self._controller._release_dll_resources()
            return

        try:
            self.makeCurrent()
            if self._model is not None and self._renderer_created:
                try:
                    self._model.DestroyRenderer()
                except Exception:
                    logger.debug("Live2D Rendererの解放に失敗しました。", exc_info=True)
            self._model = None
            if self._live2d_initialized and self._live2d is not None:
                try:
                    self._live2d.dispose()
                except Exception:
                    logger.debug("Live2Dの解放に失敗しました。", exc_info=True)
                self._live2d_initialized = False
            self._renderer_created = False
        except Exception:
            logger.debug("GUI内Live2Dの終了処理に失敗しました。", exc_info=True)
        finally:
            try:
                self.doneCurrent()
            except Exception:
                pass
            self._controller._release_dll_resources()
            self._model_ready = False
