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

    PARAMETER_CANDIDATES = {
        "mouth": ("ParamMouthOpenY", "PARAM_MOUTH_OPEN_Y"),
        "left_eye": ("ParamEyeLOpen", "PARAM_EYE_L_OPEN"),
        "right_eye": ("ParamEyeROpen", "PARAM_EYE_R_OPEN"),
        "breath": ("ParamBreath", "PARAM_BREATH"),
    }

    SWAY_PARAMETERS = {
        "angle_x": (("ParamAngleX", "PARAM_ANGLE_X"), 3.0, 6.3, 0.0),
        "angle_y": (("ParamAngleY", "PARAM_ANGLE_Y"), 2.0, 7.9, 0.8),
        "angle_z": (("ParamAngleZ", "PARAM_ANGLE_Z"), 1.5, 9.1, 1.7),
        "body_angle_x": (
            ("ParamBodyAngleX", "PARAM_BODY_ANGLE_X"),
            1.5,
            6.3,
            1.1,
        ),
        "body_angle_z": (
            ("ParamBodyAngleZ", "PARAM_BODY_ANGLE_Z"),
            1.0,
            10.3,
            2.8,
        ),
    }

    FACE_PARAMETER_CANDIDATES = {
        "eye_uru": ("Param_eye_uru", "PARAM_EYE_URU"),
        "eye_shitake": ("Param_eye_shitake", "PARAM_EYE_SHITAKE"),
        "tear": ("Param_tear", "PARAM_TEAR"),
        "cheek": ("Param_cheek", "PARAM_CHEEK"),
    }

    FACE_PRESETS = {
        "normal": {"eye_uru": 0.0, "eye_shitake": 0.0, "tear": 0.0, "cheek": 0.0},
        "happy": {"eye_uru": 0.0, "eye_shitake": 1.0, "tear": 0.0, "cheek": 0.35},
        "sad": {"eye_uru": 1.0, "eye_shitake": 0.0, "tear": 1.0, "cheek": 0.0},
        "shocked": {"eye_uru": 0.0, "eye_shitake": 0.0, "tear": 0.0, "cheek": 0.0},
        "shy": {"eye_uru": 0.25, "eye_shitake": 0.0, "tear": 0.0, "cheek": 1.0},
    }

    NATURAL_IDLE_PARAMETERS = {
        "eye_ball_x": ("ParamEyeBallX", "PARAM_EYE_BALL_X"),
        "eye_ball_y": ("ParamEyeBallY", "PARAM_EYE_BALL_Y"),
        "angle_x": ("ParamAngleX", "PARAM_ANGLE_X"),
        "angle_y": ("ParamAngleY", "PARAM_ANGLE_Y"),
        "body_angle_x": ("ParamBodyAngleX", "PARAM_BODY_ANGLE_X"),
        "body_angle_z": ("ParamBodyAngleZ", "PARAM_BODY_ANGLE_Z"),
    }

    EMOTIONS = ("normal", "happy", "sad", "shocked", "shy")
    DEFAULT_EXPRESSIONS = {
        "normal": None,
        "happy": "きらーん",
        "sad": "うるうる",
        "shocked": "がーん",
        "shy": "照れ",
    }
    NATURAL_IDLE_THRESHOLD = 20.0
    REST_EYE_CLOSE_SECONDS = 1.0
    REST_EYE_HOLD_SECONDS = 3.0
    REST_EYE_OPEN_SECONDS = 1.0
    REST_EYE_OPEN_INTERVAL_MIN_SECONDS = 18.0
    REST_EYE_OPEN_INTERVAL_MAX_SECONDS = 28.0
    REST_EYE_OPEN_HOLD_VARIANCE_SECONDS = 1.0

    POSE_FIELDS = (
        "angle_x",
        "angle_y",
        "angle_z",
        "body_angle_x",
        "body_angle_z",
        "eye_ball_x",
        "eye_ball_y",
    )
    POSE_BLEND_SECONDS = 0.48
    BREATHING_SWAY_SCALE = 0.10
    GAZE_HEAD_COUPLING_X = 3.0
    GAZE_HEAD_COUPLING_Y = 2.4
    POSE_INTERVALS = {
        "idle": (2.8, 5.2),
        "thinking": (2.0, 3.8),
        "synthesizing": (2.4, 4.2),
        "speaking": (1.5, 3.0),
        "listening": (3.0, 5.0),
    }
    POSE_SETS = {
        "idle": (
            ("idle:front", {
                "angle_x": 0.0, "angle_y": 0.0, "angle_z": 0.0,
                "body_angle_x": 0.0, "body_angle_z": 0.0,
                "eye_ball_x": 0.0, "eye_ball_y": 0.0,
            }),
            ("idle:look_left", {
                "angle_x": -7.0, "angle_y": 0.0, "angle_z": -1.0,
                "body_angle_x": -0.3, "body_angle_z": -0.8,
                "eye_ball_x": -0.70, "eye_ball_y": 0.02,
            }),
            ("idle:look_right", {
                "angle_x": 7.0, "angle_y": 0.0, "angle_z": 1.0,
                "body_angle_x": 0.3, "body_angle_z": 0.8,
                "eye_ball_x": 0.70, "eye_ball_y": 0.02,
            }),
            ("idle:look_down", {
                "angle_x": 0.0, "angle_y": -4.0, "angle_z": 0.8,
                "body_angle_x": 0.4, "body_angle_z": 0.5,
                "eye_ball_x": 0.05, "eye_ball_y": -0.48,
            }),
            ("idle:tilt_left", {
                "angle_x": -2.5, "angle_y": 1.0, "angle_z": -6.0,
                "body_angle_x": -0.6, "body_angle_z": -1.6,
                "eye_ball_x": -0.28, "eye_ball_y": 0.12,
            }),
            ("idle:tilt_right", {
                "angle_x": 2.5, "angle_y": 1.0, "angle_z": 6.0,
                "body_angle_x": 0.6, "body_angle_z": 1.6,
                "eye_ball_x": 0.28, "eye_ball_y": 0.12,
            }),
        ),
        "thinking": (
            ("thinking:look_up", {
                "angle_x": 0.0, "angle_y": 5.0, "angle_z": 0.0,
                "body_angle_x": 0.8, "body_angle_z": 0.0,
                "eye_ball_x": 0.04, "eye_ball_y": 0.55,
            }),
            ("thinking:upper_left", {
                "angle_x": -5.0, "angle_y": 4.5, "angle_z": -4.0,
                "body_angle_x": 0.6, "body_angle_z": -1.0,
                "eye_ball_x": -0.52, "eye_ball_y": 0.48,
            }),
            ("thinking:upper_right", {
                "angle_x": 5.0, "angle_y": 4.5, "angle_z": 4.0,
                "body_angle_x": 0.6, "body_angle_z": 1.0,
                "eye_ball_x": 0.52, "eye_ball_y": 0.48,
            }),
            ("thinking:reflect", {
                "angle_x": 6.0, "angle_y": 2.5, "angle_z": 5.0,
                "body_angle_x": 0.4, "body_angle_z": 1.2,
                "eye_ball_x": 0.42, "eye_ball_y": 0.30,
            }),
        ),
        "synthesizing": (
            ("synthesizing:focus", {
                "angle_x": 0.0, "angle_y": 0.5, "angle_z": 0.0,
                "body_angle_x": 0.0, "body_angle_z": 0.0,
                "eye_ball_x": 0.04, "eye_ball_y": 0.08,
            }),
            ("synthesizing:glance_left", {
                "angle_x": -4.5, "angle_y": 1.0, "angle_z": -1.5,
                "body_angle_x": -0.2, "body_angle_z": -0.5,
                "eye_ball_x": -0.48, "eye_ball_y": 0.08,
            }),
            ("synthesizing:glance_right", {
                "angle_x": 4.5, "angle_y": 1.0, "angle_z": 1.5,
                "body_angle_x": 0.2, "body_angle_z": 0.5,
                "eye_ball_x": 0.48, "eye_ball_y": 0.08,
            }),
            ("synthesizing:inspect", {
                "angle_x": -1.5, "angle_y": -2.5, "angle_z": -2.0,
                "body_angle_x": -0.4, "body_angle_z": -0.8,
                "eye_ball_x": -0.18, "eye_ball_y": -0.32,
            }),
        ),
        "speaking": (
            ("speaking:lean_forward", {
                "angle_x": 1.2, "angle_y": -3.8, "angle_z": 0.0,
                "body_angle_x": 1.2, "body_angle_z": 0.0,
                "eye_ball_x": 0.02, "eye_ball_y": -0.14,
            }),
            ("speaking:turn_left", {
                "angle_x": -3.8, "angle_y": -2.5, "angle_z": -1.8,
                "body_angle_x": 0.6, "body_angle_z": -0.8,
                "eye_ball_x": -0.27, "eye_ball_y": -0.10,
            }),
            ("speaking:turn_right", {
                "angle_x": 3.8, "angle_y": -2.5, "angle_z": 1.8,
                "body_angle_x": 0.6, "body_angle_z": 0.8,
                "eye_ball_x": 0.27, "eye_ball_y": -0.10,
            }),
            ("speaking:gentle_tilt", {
                "angle_x": -1.8, "angle_y": -2.0, "angle_z": -3.0,
                "body_angle_x": 0.5, "body_angle_z": -0.8,
                "eye_ball_x": -0.14, "eye_ball_y": -0.08,
            }),
        ),
        "listening": (
            ("listening:attentive", {
                "angle_x": 0.0, "angle_y": 1.0, "angle_z": 0.0,
                "body_angle_x": 0.0, "body_angle_z": 0.0,
                "eye_ball_x": 0.0, "eye_ball_y": 0.06,
            }),
            ("listening:small_nod", {
                "angle_x": 0.0, "angle_y": -1.8, "angle_z": 0.0,
                "body_angle_x": -0.8, "body_angle_z": 0.0,
                "eye_ball_x": 0.02, "eye_ball_y": -0.08,
            }),
            ("listening:glance_left", {
                "angle_x": -2.0, "angle_y": 0.5, "angle_z": -0.8,
                "body_angle_x": 0.0, "body_angle_z": -0.3,
                "eye_ball_x": -0.22, "eye_ball_y": 0.04,
            }),
            ("listening:glance_right", {
                "angle_x": 2.0, "angle_y": 0.5, "angle_z": 0.8,
                "body_angle_x": 0.0, "body_angle_z": 0.3,
                "eye_ball_x": 0.22, "eye_ball_y": 0.04,
            }),
        ),
    }

    def __init__(
        self,
        model_path: str,
        width: int = 480,
        height: int = 640,
        mouth_scale: float = 3.0,
        mouth_param: str = "",
        idle_sway: bool = True,
        expressions: dict[str, str | None] | None = None,
        natural_idle: bool = True,
        motion_enabled: bool = True,
        speaking_motion_scale: float = 2.0,
    ) -> None:
        """モデル、表情、口パク、待機中の動作を設定する。"""
        self.model_path = Path(model_path).expanduser().resolve()
        self.width = max(1, int(width))
        self.height = max(1, int(height))
        self.mouth_scale = max(0.0, float(mouth_scale))
        self.mouth_param = str(mouth_param or "").strip()
        self.idle_sway = bool(idle_sway)
        self.natural_idle = bool(natural_idle)
        self.motion_enabled = bool(motion_enabled)
        try:
            speaking_scale = float(speaking_motion_scale)
        except (TypeError, ValueError):
            speaking_scale = 2.0
        self.speaking_motion_scale = (
            max(0.0, min(5.0, speaking_scale))
            if math.isfinite(speaking_scale) else 2.0
        )

        self.expressions = dict(self.DEFAULT_EXPRESSIONS)
        if isinstance(expressions, dict):
            for emotion in self.EMOTIONS:
                if emotion in expressions:
                    value = expressions[emotion]
                    self.expressions[emotion] = (
                        str(value).strip()
                        if isinstance(value, str) and value.strip()
                        else None
                    )

        self._stop_event = threading.Event()
        self._ready_event = threading.Event()
        self._mouth_lock = threading.Lock()
        self._expression_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._activity_lock = threading.Lock()
        self._activity_state = "idle"
        self._gaze_target_x = 0.0
        self._gaze_target_y = 0.0
        self._gaze_current_x = 0.0
        self._gaze_current_y = 0.0
        self._next_gaze_change = 0.0
        self._last_motion_update = 0.0
        self._pose_state: str | None = None
        self._pose_name: str | None = None
        self._pose_target = {field: 0.0 for field in self.POSE_FIELDS}
        self._pose_current = {field: 0.0 for field in self.POSE_FIELDS}
        self._pose_next_change = 0.0

        self._mouth_open = 0.0
        self._last_mouth_activity = time.monotonic()
        self._requested_emotion = "normal"
        self._requested_expression = self.expressions.get("normal")
        self._expression_revision = 0

        self._running = False
        self._startup_success = False
        self._thread: threading.Thread | None = None

        self._dll_directory_handle: Any | None = None
        self._core_dll_handle: Any | None = None
        self._idle_motion_count = 0
        self._expression_names: set[str] = set()

        self._rest_active = False
        self._rest_started_at: float | None = None
        self._rest_initial_close_started_at: float | None = None
        self._rest_next_open_time: float | None = None
        self._rest_open_started_at: float | None = None
        self._rest_open_hold_duration = self.REST_EYE_HOLD_SECONDS


    ACTIVITY_STATES = {"idle", "thinking", "synthesizing", "speaking", "listening"}

    def set_activity_state(self, state: str) -> None:
        """会話状態を描画スレッドへ安全に通知する。"""
        normalized = str(state or "idle").strip().casefold()
        if normalized not in self.ACTIVITY_STATES:
            logger.debug("未知のLive2D活動状態 %r をidleとして扱います。", state)
            normalized = "idle"
        with self._activity_lock:
            self._activity_state = normalized
            if normalized != "idle":
                self._next_gaze_change = 0.0

    def get_activity_state(self) -> str:
        with self._activity_lock:
            return self._activity_state

    def compose_motion_offsets(
        self, now: float, initial_time: float, activity: str,
        mouth_open: float, resting: bool, rest_elapsed: float,
        rest_gaze_phase: float,
    ) -> dict[str, float]:
        """全モーションを合成し、パラメータごとの加算値を1つだけ返す。"""
        if not self.motion_enabled:
            return self._compose_legacy_motion_offsets(
                now, initial_time, activity, mouth_open,
                resting, rest_elapsed, rest_gaze_phase,
            )
        return self._compose_pose_offsets(
            now, initial_time, activity, mouth_open,
        )

    def _compose_pose_offsets(
        self,
        now: float,
        initial_time: float,
        activity: str,
        mouth_open: float,
    ) -> dict[str, float]:
        """状態ごとのポーズ目標を選択し、指数ブレンドで滑らかに追従させる。"""
        state = activity if activity in self.ACTIVITY_STATES else "idle"
        elapsed = now - initial_time
        with self._activity_lock:
            state = self._activity_state
            if state not in self.ACTIVITY_STATES:
                state = "idle"
            speaking = state == "speaking"

            if self._pose_state != state or now >= self._pose_next_change:
                candidates = self.POSE_SETS.get(state, self.POSE_SETS["idle"])
                available = [
                    candidate for candidate in candidates
                    if candidate[0] != self._pose_name
                ]
                pose_name, pose = random.choice(available or candidates)
                speaking_scale = (
                    self.speaking_motion_scale if state == "speaking" else 1.0
                )
                target = {
                    field: float(pose.get(field, 0.0)) * speaking_scale
                    for field in self.POSE_FIELDS
                }
                # 視線と頭の向きを同じ目標から作り、別々の追従処理が競合しないようにする。
                target["eye_ball_x"] = max(
                    -0.7, min(0.7, target["eye_ball_x"])
                )
                target["eye_ball_y"] = max(
                    -0.7, min(0.7, target["eye_ball_y"])
                )
                target["angle_x"] += (
                    target["eye_ball_x"] * self.GAZE_HEAD_COUPLING_X
                )
                target["angle_y"] += (
                    target["eye_ball_y"] * self.GAZE_HEAD_COUPLING_Y
                )
                self._pose_target = target
                self._pose_name = pose_name
                self._pose_state = state
                interval = self.POSE_INTERVALS.get(state, (2.8, 5.2))
                self._pose_next_change = now + random.uniform(*interval)

            dt = (
                max(0.0, min(now - self._last_motion_update, 0.1))
                if self._last_motion_update else 1.0 / 60.0
            )
            self._last_motion_update = now
            blend = 1.0 - math.exp(-dt / self.POSE_BLEND_SECONDS)
            for field in self.POSE_FIELDS:
                current = self._pose_current[field]
                target = self._pose_target[field]
                self._pose_current[field] = current + (target - current) * blend
            pose_values = dict(self._pose_current)

        offsets = pose_values
        if self.idle_sway:
            # 既存の周期揺れは呼吸に合わせた小さな微振動としてのみ残す。
            sway_scale = self.BREATHING_SWAY_SCALE * (
                self.speaking_motion_scale if speaking else 1.0
            )
            for role, (_, amplitude, period, phase) in self.SWAY_PARAMETERS.items():
                offsets[role] += amplitude * sway_scale * math.sin(
                    elapsed * 2.0 * math.pi / period + phase
                )

        if speaking:
            # v0.11.0 の声量連動うなずきは維持する。
            pulse = math.sin(elapsed * 5.8)
            scale = self.speaking_motion_scale
            offsets["angle_y"] += (
                scale * mouth_open * (2.6 + 1.2 * pulse)
            )
            offsets["angle_x"] += (
                scale * mouth_open * 0.9 * math.sin(elapsed * 3.0)
            )
            offsets["body_angle_x"] += (
                scale * mouth_open * 1.5 * math.sin(elapsed * 4.2)
            )
            offsets["body_angle_z"] += (
                scale * mouth_open * math.sin(elapsed * 2.6 + 0.8)
            )
        return offsets

    def _compose_legacy_motion_offsets(
        self,
        now: float,
        initial_time: float,
        activity: str,
        mouth_open: float,
        resting: bool,
        rest_elapsed: float,
        rest_gaze_phase: float,
    ) -> dict[str, float]:
        """ポーズ遷移を無効にしたときのv0.11.0のサイン波モーション。"""
        elapsed = now - initial_time
        offsets: dict[str, float] = {}
        if self.idle_sway:
            scale = self.speaking_motion_scale if speaking else 1.0
            for role, (_, amplitude, period, phase) in self.SWAY_PARAMETERS.items():
                offsets[role] = amplitude * scale * math.sin(
                    elapsed * 2.0 * math.pi / period + phase
                )

        with self._activity_lock:
            state = self._activity_state
            speaking = state == "speaking"
            if now >= self._next_gaze_change:
                if state == "thinking":
                    self._gaze_target_x = random.uniform(-0.58, 0.58)
                    self._gaze_target_y = random.uniform(0.20, 0.48)
                    delay = random.uniform(2.0, 3.8)
                elif state == "synthesizing":
                    self._gaze_target_x = random.uniform(-0.48, 0.48)
                    self._gaze_target_y = random.uniform(-0.08, 0.30)
                    delay = random.uniform(2.4, 4.2)
                elif state == "listening":
                    self._gaze_target_x = random.uniform(-0.30, 0.30)
                    self._gaze_target_y = random.uniform(-0.14, 0.14)
                    delay = random.uniform(3.0, 5.0)
                else:
                    self._gaze_target_x = random.uniform(-0.70, 0.70)
                    self._gaze_target_y = random.uniform(-0.30, 0.30)
                    delay = random.uniform(2.8, 5.2)
                self._next_gaze_change = now + delay
            dt = (
                max(0.0, min(now - self._last_motion_update, 0.1))
                if self._last_motion_update else 1.0 / 60.0
            )
            self._last_motion_update = now
            blend = 1.0 - math.exp(-dt / 0.38)
            self._gaze_current_x += (
                self._gaze_target_x - self._gaze_current_x
            ) * blend
            self._gaze_current_y += (
                self._gaze_target_y - self._gaze_current_y
            ) * blend
            gaze_x, gaze_y = self._gaze_current_x, self._gaze_current_y

        if state == "speaking":
            gaze_x *= 0.55
            gaze_y *= 0.55
        elif state == "thinking":
            offsets["angle_x"] = offsets.get("angle_x", 0.0) + 2.0 * math.sin(elapsed * 0.72)
            offsets["angle_y"] = offsets.get("angle_y", 0.0) + 1.8 + 0.8 * math.sin(elapsed * 0.48)
            offsets["body_angle_x"] = offsets.get("body_angle_x", 0.0) + 0.55 * math.sin(elapsed * 0.65)
        elif state == "synthesizing":
            offsets["angle_x"] = offsets.get("angle_x", 0.0) + 1.0 * math.sin(elapsed * 0.9)
            offsets["angle_y"] = offsets.get("angle_y", 0.0) + 0.7 * math.sin(elapsed * 0.62)
        elif state == "speaking":
            pulse = math.sin(elapsed * 5.8)
            scale = self.speaking_motion_scale
            offsets["angle_y"] = offsets.get("angle_y", 0.0) + scale * mouth_open * (2.6 + 1.2 * pulse)
            offsets["angle_x"] = offsets.get("angle_x", 0.0) + scale * mouth_open * 0.9 * math.sin(elapsed * 3.0)
            offsets["body_angle_x"] = offsets.get("body_angle_x", 0.0) + scale * mouth_open * 1.5 * math.sin(elapsed * 4.2)
            offsets["body_angle_z"] = offsets.get("body_angle_z", 0.0) + scale * mouth_open * math.sin(elapsed * 2.6 + 0.8)
        elif resting:
            offsets["angle_x"] = offsets.get("angle_x", 0.0) + 1.2 * math.sin(elapsed * 0.20)

        # 旧モードでも視線の移動に頭を少し追従させる。
        offsets["angle_x"] = offsets.get("angle_x", 0.0) + gaze_x * self.GAZE_HEAD_COUPLING_X
        offsets["angle_y"] = offsets.get("angle_y", 0.0) + gaze_y * self.GAZE_HEAD_COUPLING_Y
        offsets["eye_ball_x"] = gaze_x
        offsets["eye_ball_y"] = gaze_y
        return offsets

    def update_rest_eye_open(
        self,
        now: float,
        resting: bool,
    ) -> tuple[float, float]:
        """休憩中は閉眼を既定とし、18〜28秒ごとに短時間だけ開眼する。"""
        if not resting:
            if self._rest_active:
                logger.info("Live2Dが通常の待機状態に戻りました。")
            self._rest_active = False
            self._rest_started_at = None
            self._rest_initial_close_started_at = None
            self._rest_next_open_time = None
            self._rest_open_started_at = None
            self._rest_open_hold_duration = self.REST_EYE_HOLD_SECONDS
            return 1.0, 0.0

        if not self._rest_active:
            self._rest_active = True
            self._rest_started_at = now
            self._rest_initial_close_started_at = now
            self._rest_next_open_time = (
                now + random.uniform(
                    self.REST_EYE_OPEN_INTERVAL_MIN_SECONDS,
                    self.REST_EYE_OPEN_INTERVAL_MAX_SECONDS,
                )
            )
            self._rest_open_started_at = None
            logger.info("Live2Dが自然な休憩状態に入りました。")

        rest_started_at = self._rest_started_at if self._rest_started_at is not None else now
        rest_elapsed = max(0.0, now - rest_started_at)

        if self._rest_initial_close_started_at is not None:
            close_elapsed = now - self._rest_initial_close_started_at
            close_duration = max(0.001, self.REST_EYE_CLOSE_SECONDS)
            if close_elapsed < close_duration:
                return max(0.0, 1.0 - close_elapsed / close_duration), rest_elapsed
            self._rest_initial_close_started_at = None

        if (
            self._rest_open_started_at is None
            and self._rest_next_open_time is not None
            and now >= self._rest_next_open_time
        ):
            self._rest_open_started_at = now
            self._rest_open_hold_duration = max(
                2.0,
                min(
                    4.0,
                    self.REST_EYE_HOLD_SECONDS
                    + random.uniform(
                        -self.REST_EYE_OPEN_HOLD_VARIANCE_SECONDS,
                        self.REST_EYE_OPEN_HOLD_VARIANCE_SECONDS,
                    ),
                ),
            )

        if self._rest_open_started_at is None:
            return 0.0, rest_elapsed

        open_elapsed = now - self._rest_open_started_at
        open_ramp = max(0.001, self.REST_EYE_OPEN_SECONDS)
        close_ramp = max(0.001, self.REST_EYE_CLOSE_SECONDS)
        hold_end = open_ramp + self._rest_open_hold_duration
        close_end = hold_end + close_ramp

        if open_elapsed < open_ramp:
            eye_open = open_elapsed / open_ramp
        elif open_elapsed < hold_end:
            eye_open = 1.0
        elif open_elapsed < close_end:
            eye_open = 1.0 - (open_elapsed - hold_end) / close_ramp
        else:
            self._rest_open_started_at = None
            self._rest_next_open_time = (
                now + random.uniform(
                    self.REST_EYE_OPEN_INTERVAL_MIN_SECONDS,
                    self.REST_EYE_OPEN_INTERVAL_MAX_SECONDS,
                )
            )
            eye_open = 0.0

        return max(0.0, min(1.0, eye_open)), rest_elapsed

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

        expression_entries = references.get("Expressions", [])
        self._expression_names = {
            str(entry.get("Name")).strip()
            for entry in expression_entries
            if isinstance(entry, dict)
            and isinstance(entry.get("Name"), str)
            and entry.get("Name").strip()
        } if isinstance(expression_entries, list) else set()

        if self._expression_names:
            logger.info(
                "モデルJSONから表情定義を読み込みました: %s",
                ", ".join(sorted(self._expression_names)),
            )
        else:
            logger.info(
                "モデルJSONに表情定義がありません。パラメータ方式を使用します。"
            )
        return True

    @staticmethod
    def _resolve_parameter_id(
        parameter_ids: set[str],
        candidates: tuple[str, ...],
    ) -> str | None:
        """候補IDを大文字小文字を区別せず照合し、実際のIDを返す。"""
        available = {
            parameter_id.casefold(): parameter_id
            for parameter_id in parameter_ids
            if isinstance(parameter_id, str)
        }

        for candidate in candidates:
            resolved = available.get(candidate.casefold())
            if resolved is not None:
                return resolved

        return None

    def _resolve_model_parameters(
        self,
        parameter_ids: set[str],
    ) -> tuple[dict[str, str], dict[str, str], dict[str, str], dict[str, str]]:
        """口・眼・呼吸・揺れ・表情・自然な待機に使うIDを解決する。"""
        resolved: dict[str, str] = {}

        for role, candidates in self.PARAMETER_CANDIDATES.items():
            preferred_candidates = candidates
            if role == "mouth" and self.mouth_param:
                preferred_candidates = (self.mouth_param, *candidates)

            parameter_id = self._resolve_parameter_id(parameter_ids, preferred_candidates)
            if role == "mouth" and self.mouth_param and parameter_id is None:
                logger.warning(
                    "指定された口パラメータID %r がモデルにありません。"
                    "標準候補から自動判定します。",
                    self.mouth_param,
                )
                parameter_id = self._resolve_parameter_id(parameter_ids, candidates)

            if parameter_id is not None:
                resolved[role] = parameter_id
                logger.info("Live2Dパラメータを解決しました: %s = %s", role, parameter_id)
            elif role == "mouth":
                logger.warning(
                    "口の開閉パラメータが見つかりません。"
                    "live2d_mouth_paramを確認してください。口パクを無効にします。"
                )
            else:
                logger.info(
                    "Live2Dの%sパラメータは見つかりません。該当演出を省略します。",
                    role,
                )

        resolved_sway: dict[str, str] = {}
        for role, (candidates, amplitude, period, phase) in self.SWAY_PARAMETERS.items():
            parameter_id = self._resolve_parameter_id(parameter_ids, candidates)
            if parameter_id is not None:
                resolved_sway[role] = parameter_id
                if self.idle_sway:
                    logger.info(
                        "Live2D待機揺れ・ポーズ用パラメータを解決しました: %s = %s "
                        "(微振動振幅=%.2f, 周期=%.1f秒)",
                        role,
                        parameter_id,
                        amplitude * self.BREATHING_SWAY_SCALE,
                        period,
                    )
                else:
                    logger.info("Live2Dポーズ用パラメータを解決しました: %s = %s", role, parameter_id)
            else:
                logger.debug("モーションパラメータ %s はモデルにありません。", role)

        resolved_face: dict[str, str] = {}
        for role, candidates in self.FACE_PARAMETER_CANDIDATES.items():
            parameter_id = self._resolve_parameter_id(parameter_ids, candidates)
            if parameter_id is not None:
                resolved_face[role] = parameter_id
                logger.info("感情表現パラメータを解決しました: %s = %s", role, parameter_id)
            else:
                logger.debug("感情表現パラメータ %s はモデルにありません。", role)

        resolved_natural: dict[str, str] = {}
        for role, candidates in self.NATURAL_IDLE_PARAMETERS.items():
            parameter_id = self._resolve_parameter_id(parameter_ids, candidates)
            if parameter_id is not None:
                resolved_natural[role] = parameter_id
                logger.info("自然な待機・視線用パラメータを解決しました: %s = %s", role, parameter_id)
            else:
                logger.debug("自然な待機・視線パラメータ %s はモデルにありません。", role)

        return resolved, resolved_sway, resolved_face, resolved_natural

    def _get_expression_request(self) -> tuple[str, str | None, int]:
        """描画スレッド用に最新の感情リクエストを取得する。"""
        with self._expression_lock:
            return (
                self._requested_emotion,
                self._requested_expression,
                self._expression_revision,
            )

    def _activate_expression(
        self,
        model: Any,
        emotion: str,
        expression_name: str | None,
        resolved_face: dict[str, str],
    ) -> tuple[bool, dict[str, float]]:
        """モデルAPIを優先し、未対応時は表情パラメータのプリセットへ切り替える。"""
        available_names = {
            name.casefold(): name for name in self._expression_names
        }
        setter = getattr(model, "SetExpression", None)
        resetter = getattr(model, "ResetExpression", None)
        if not callable(resetter):
            resetter = getattr(model, "ResetExpressions", None)

        if expression_name is not None:
            actual_name = available_names.get(expression_name.casefold())
            if actual_name is not None and callable(setter):
                try:
                    setter(actual_name)
                    logger.info(
                        "Live2D表情を適用しました: emotion=%s, expression=%s",
                        emotion,
                        actual_name,
                    )
                    return False, {}
                except Exception as exc:
                    logger.warning(
                        "Live2D表情APIで %s を適用できません。"
                        "パラメータ方式へ切り替えます。原因: %s",
                        expression_name,
                        exc,
                    )
            elif actual_name is None:
                logger.warning(
                    "表情 %r はmodel3.jsonのExpressionsにありません。"
                    "パラメータ方式へフォールバックします。",
                    expression_name,
                )
            else:
                logger.warning(
                    "live2d-pyにSetExpression APIがありません。"
                    "パラメータ方式へフォールバックします。"
                )
        elif callable(resetter):
            try:
                resetter()
                logger.debug("Live2D表情をnormalに戻しました。")
                return False, {}
            except Exception as exc:
                logger.warning(
                    "Live2D表情をリセットできません。"
                    "パラメータ方式へ切り替えます。原因: %s",
                    exc,
                )

        # 直前にAPI表情を設定していた場合、パラメータ方式へ移る前に解除する。
        if callable(resetter):
            try:
                resetter()
            except Exception as exc:
                logger.debug("切替前の表情リセットに失敗しました: %s", exc)

        preset = self.FACE_PRESETS.get(emotion, self.FACE_PRESETS["normal"])
        if not resolved_face:
            logger.warning(
                "表情APIと代替パラメータの両方が利用できません。"
                "Live2D表示は継続しますが、感情表現は省略します。"
            )
        else:
            logger.info("感情表現をパラメータ方式で適用します: %s", emotion)
        return True, preset

    @staticmethod
    def _apply_face_preset(
        model: Any,
        resolved_face: dict[str, str],
        preset: dict[str, float],
    ) -> None:
        """解決済みの表情パラメータへプリセット値を反映する。"""
        for role, parameter_id in resolved_face.items():
            model.SetParamById(parameter_id, float(preset.get(role, 0.0)))

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

    def reset_mouth_activity(self) -> None:
        """描画開始時点を口の最終活動時刻として記録する。"""
        with self._mouth_lock:
            self._last_mouth_activity = time.monotonic()

    def set_mouth_open(self, value: float) -> None:
        """口の開き具合を0.0〜1.0に制限してスレッドセーフに保持する。"""
        try:
            mouth_value = float(value)
        except (TypeError, ValueError):
            mouth_value = 0.0

        if not math.isfinite(mouth_value):
            mouth_value = 0.0

        with self._mouth_lock:
            clamped_value = max(0.0, min(1.0, mouth_value))
            if (
                clamped_value >= 0.03
                or abs(clamped_value - self._mouth_open) >= 0.02
            ):
                self._last_mouth_activity = time.monotonic()
            self._mouth_open = clamped_value
        if clamped_value >= 0.03:
            with self._activity_lock:
                if self._activity_state == "synthesizing":
                    self._activity_state = "speaking"

    def _get_mouth_open(self) -> float:
        """描画スレッド用に口の開き具合を取得する。"""
        with self._mouth_lock:
            return self._mouth_open

    def _get_mouth_state(self) -> tuple[float, float]:
        """口の開き具合と最後に口が動いた時刻を取得する。"""
        with self._mouth_lock:
            return self._mouth_open, self._last_mouth_activity

    def set_emotion(self, emotion: str) -> None:
        """感情名をスレッドセーフに受け取り、描画スレッドへ反映を依頼する。"""
        emotion_key = str(emotion or "normal").strip().casefold()
        if emotion_key not in self.EMOTIONS:
            logger.warning("未対応の感情 %r をnormalとして扱います。", emotion)
            emotion_key = "normal"

        expression_name = self.expressions.get(emotion_key)
        with self._expression_lock:
            self._requested_emotion = emotion_key
            self._requested_expression = expression_name
            self._expression_revision += 1

    def set_expression(self, name: str | None) -> None:
        """表情名をスレッドセーフに受け取り、描画スレッドへ反映を依頼する。"""
        expression_name = (
            str(name).strip()
            if isinstance(name, str) and name.strip()
            else None
        )
        emotion = "normal"
        if expression_name is not None:
            for candidate_emotion, configured_name in self.expressions.items():
                if (
                    configured_name is not None
                    and configured_name.casefold() == expression_name.casefold()
                ):
                    emotion = candidate_emotion
                    break

        with self._expression_lock:
            self._requested_emotion = emotion
            self._requested_expression = expression_name
            self._expression_revision += 1

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
            (
                resolved_parameters,
                resolved_sway,
                resolved_face,
                resolved_natural,
            ) = self._resolve_model_parameters(parameter_ids)

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
            self.reset_mouth_activity()
            next_blink_time = initial_time + random.uniform(3.0, 5.0)
            blink_start_time: float | None = None

            last_expression_revision = -1
            use_face_fallback = False
            active_face_preset: dict[str, float] = {}

            rest_gaze_phase = random.uniform(0.0, math.tau)

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

                emotion, expression_name, expression_revision = (
                    self._get_expression_request()
                )
                if expression_revision != last_expression_revision:
                    (
                        use_face_fallback,
                        active_face_preset,
                    ) = self._activate_expression(
                        model,
                        emotion,
                        expression_name,
                        resolved_face,
                    )
                    last_expression_revision = expression_revision

                model.Update(delta_time)

                if use_face_fallback:
                    self._apply_face_preset(
                        model,
                        resolved_face,
                        active_face_preset,
                    )

                mouth_id = resolved_parameters.get("mouth")
                if mouth_id is not None:
                    model.SetParamById(
                        mouth_id,
                        self._get_mouth_open(),
                    )

                # 会話中は休憩状態へ入らず、無操作のidle時だけ自然な休憩へ移行する。
                activity = self.get_activity_state()
                _, last_mouth_activity = self._get_mouth_state()
                resting = (
                    self.natural_idle
                    and activity == "idle"
                    and now - last_mouth_activity >= self.NATURAL_IDLE_THRESHOLD
                )

                rest_eye_open, rest_elapsed = self.update_rest_eye_open(
                    now,
                    resting,
                )

                # 通常のまばたき。休憩中はゆっくりした閉眼動作を重ねる。
                if blink_start_time is None and now >= next_blink_time:
                    blink_start_time = now

                blink_open = 1.0
                if blink_start_time is not None:
                    progress = (now - blink_start_time) / self.BLINK_DURATION
                    if progress >= 1.0:
                        blink_start_time = None
                        next_blink_time = now + random.uniform(3.0, 5.0)
                    elif progress < 0.5:
                        blink_open = 1.0 - progress * 2.0
                    else:
                        blink_open = (progress - 0.5) * 2.0
                    blink_open = max(0.0, min(1.0, blink_open))

                blink_open *= rest_eye_open

                left_eye_id = resolved_parameters.get("left_eye")
                if left_eye_id is not None:
                    model.SetParamById(left_eye_id, blink_open)

                right_eye_id = resolved_parameters.get("right_eye")
                if right_eye_id is not None:
                    model.SetParamById(right_eye_id, blink_open)

                # 呼吸
                breath_id = resolved_parameters.get("breath")
                if breath_id is not None:
                    elapsed = now - initial_time
                    breath_value = (
                        0.5
                        + 0.5 * math.sin(
                            elapsed * 2.0 * math.pi / 4.5
                        )
                    )
                    model.SetParamById(breath_id, breath_value)

                motion_offsets = self.compose_motion_offsets(
                    now, initial_time, activity, self._get_mouth_open(),
                    resting, rest_elapsed, rest_gaze_phase,
                )
                for role, value in motion_offsets.items():
                    parameter_id = resolved_sway.get(role) or resolved_natural.get(role)
                    if parameter_id is not None:
                        model.AddParamById(parameter_id, value)

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
                    logger.debug(
                        "OpenGLコンテキストの再設定に失敗しました。",
                        exc_info=True,
                    )

            if model is not None and renderer_created:
                try:
                    model.DestroyRenderer()
                except Exception:
                    logger.debug(
                        "Live2D Rendererの解放に失敗しました。",
                        exc_info=True,
                    )

            if live2d is not None and live2d_initialized:
                try:
                    live2d.dispose()
                except Exception:
                    logger.debug(
                        "Live2Dの解放に失敗しました。",
                        exc_info=True,
                    )

            if glfw is not None:
                if window:
                    try:
                        glfw.destroy_window(window)
                    except Exception:
                        logger.debug(
                            "Live2Dウィンドウの解放に失敗しました。",
                            exc_info=True,
                        )

                if glfw_initialized:
                    try:
                        glfw.terminate()
                    except Exception:
                        logger.debug(
                            "GLFWの終了に失敗しました。",
                            exc_info=True,
                        )

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
