"""Live2Dの自然な休憩モーションのテスト。"""

import threading
import unittest

from app.live2d_viewer import Live2DViewer


class RestingMotionTests(unittest.TestCase):
    def make_viewer(self, motion_enabled: bool = True) -> Live2DViewer:
        viewer = Live2DViewer.__new__(Live2DViewer)
        viewer.motion_enabled = motion_enabled
        viewer.idle_sway = True
        viewer.speaking_motion_scale = 2.0
        viewer._activity_lock = threading.Lock()
        viewer._activity_state = "idle"
        viewer._pose_state = None
        viewer._pose_name = None
        viewer._pose_target = {field: 0.0 for field in viewer.POSE_FIELDS}
        viewer._pose_current = {field: 0.0 for field in viewer.POSE_FIELDS}
        viewer._pose_next_change = 0.0
        viewer._last_motion_update = 0.0
        viewer._next_gaze_change = 0.0
        viewer._gaze_target_x = 0.0
        viewer._gaze_target_y = 0.0
        viewer._gaze_current_x = 0.0
        viewer._gaze_current_y = 0.0
        return viewer

    def test_pose_mode_uses_a_fixed_sleep_pose(self) -> None:
        viewer = self.make_viewer()
        offsets = viewer.compose_motion_offsets(
            now=10.0, initial_time=0.0, activity="idle", mouth_open=0.0,
            resting=True, rest_elapsed=3.0, rest_gaze_phase=0.0,
        )
        self.assertEqual(viewer._pose_state, "resting")
        self.assertEqual(viewer._pose_name, "resting:sleep")
        self.assertEqual(viewer._pose_next_change, float("inf"))
        self.assertAlmostEqual(offsets["eye_ball_x"], 0.0)
        self.assertLess(viewer._pose_target["angle_z"], 0.0)

    def test_legacy_mode_uses_sleep_pose_without_gaze_sweeps(self) -> None:
        viewer = self.make_viewer(motion_enabled=False)
        offsets = viewer.compose_motion_offsets(
            now=10.0, initial_time=0.0, activity="idle", mouth_open=0.0,
            resting=True, rest_elapsed=3.0, rest_gaze_phase=0.0,
        )
        self.assertEqual(offsets["eye_ball_x"], 0.0)
        self.assertEqual(offsets["eye_ball_y"], viewer.RESTING_POSE["eye_ball_y"])
        self.assertEqual(offsets["angle_z"], viewer.RESTING_POSE["angle_z"])

    def test_mouth_form_parameter_candidates_are_registered(self) -> None:
        self.assertEqual(
            Live2DViewer.PARAMETER_CANDIDATES["mouth_form"],
            ("ParamMouthForm", "PARAM_MOUTH_FORM"),
        )


if __name__ == "__main__":
    unittest.main()
