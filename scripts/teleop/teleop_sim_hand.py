"""LEAP hand teleoperation via ZED stereo camera + MediaPipe Hands.

Streams a ZED camera in a background thread, runs two MediaPipe Hands
instances (one per view), and drives the LEAP hand MuJoCo simulation in
real time via the teleop_callback pattern.

Architecture
------------
[Camera thread ~30 Hz]                [Sim main thread 200 Hz]
  ZEDCamera.get_frames()               HandSim run loop
  → StereoHandDetector.process()         → teleop_callback(sim)
  → write to _obs (Lock)                   → read _obs (non-blocking)
                                           → LeapRetargeter.retarget()
                                           → sim.set_finger_targets()
                                           → sim.set_wrist_pos()

Wrist position
--------------
MediaPipe world landmarks are rooted at the wrist in a hand-relative
metric frame — they do not give absolute camera-frame position.
Currently the wrist is fixed at a default world position.
TODO: integrate stereo_capture.py triangulation for 3-D wrist tracking.

Prerequisites
-------------
1. Copy LEAP MJCF assets from Binocular-Teleop into this repo::

       git clone https://github.com/SAPIEN-AIT/Binocular-Teleop /tmp/binocularteleop
       cp -r /tmp/binocularteleop/robots/leap_hand /path/to/Mina/mjcf/

2. ZED camera connected (or pass --camera with a regular webcam index).

Usage
-----
    mjpython scripts/teleop/teleop_sim_hand.py
    mjpython scripts/teleop/teleop_sim_hand.py --camera 1 --scale 0.8

Run with mjpython (required on macOS for GLFW thread safety).
"""

from __future__ import annotations

import argparse
import sys
import threading
import time
from pathlib import Path

import cv2
import numpy as np

# ---------------------------------------------------------------------------
# Path setup — allows running from repo root without install
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "source" / "mina_teleop"))

from mina_teleop.environments.mujoco_hand import HandSim, HandSimConfig
from mina_teleop.inputs.vision.hand_detector import StereoHandDetector
from mina_teleop.inputs.vision.zed_engine import ZEDCamera
from mina_teleop.retargeters.hand import LeapRetargeter

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_PRINT_DT = 1.0 / 5.0   # terminal readout at 5 Hz

# Default wrist position in world frame (metres).
# Adjust to place the hand at a comfortable location in the scene.
_DEFAULT_WRIST_POS = np.array([0.0, 0.3, 0.3])

_JOINT_NAMES = [
    "idx_mcp", "idx_pip", "idx_dip", "idx_abd",
    "mid_mcp", "mid_pip", "mid_dip", "mid_abd",
    "rng_mcp", "rng_pip", "rng_dip", "rng_abd",
    "thb_cmc", "thb_axl", "thb_mcp", "thb_ip ",
]


# ---------------------------------------------------------------------------
# Shared detection buffer
# ---------------------------------------------------------------------------

class _DetectionBuffer:
    """Thread-safe buffer for the latest stereo hand observation."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._landmarks: np.ndarray | None = None   # (21, 3)
        self._frame: np.ndarray | None = None        # annotated left frame

    def write(
        self,
        landmarks: np.ndarray | None,
        frame: np.ndarray | None,
    ) -> None:
        with self._lock:
            self._landmarks = landmarks
            self._frame = frame

    def read(self) -> tuple[np.ndarray | None, np.ndarray | None]:
        with self._lock:
            return self._landmarks, self._frame


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="LEAP hand ZED+MediaPipe teleoperation → HandSim",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--camera", type=int,   default=0,
                        help="OpenCV camera device index for the ZED")
    parser.add_argument("--y-offset", type=int, default=0,
                        help="Vertical pixel offset for right ZED frame")
    parser.add_argument("--scale",  type=float, default=1.0,
                        help="Global retargeter output scale [0, 1]")
    parser.add_argument("--physics-hz", type=float, default=200.0)
    parser.add_argument("--no-preview", action="store_true",
                        help="Disable OpenCV skeleton preview window")
    args = parser.parse_args()

    # ------------------------------------------------------------------
    # Components
    # ------------------------------------------------------------------

    camera   = ZEDCamera(camera_id=args.camera, y_offset=args.y_offset)
    detector = StereoHandDetector(max_hands=1)
    retargeter = LeapRetargeter(scale=args.scale)
    sim      = HandSim(HandSimConfig(physics_hz=args.physics_hz))

    buf = _DetectionBuffer()

    # ------------------------------------------------------------------
    # Camera thread — ~30 Hz
    # ------------------------------------------------------------------

    active = {"flag": True}

    def _camera_loop() -> None:
        while active["flag"]:
            try:
                left, right = camera.get_frames()
            except RuntimeError as exc:
                print(f"[camera] {exc}")
                time.sleep(0.1)
                continue

            obs = detector.process(left, right)

            # Prefer left camera landmarks for retargeting; fall back to right
            landmarks = None
            if obs.left_cam is not None and obs.left_cam.valid:
                landmarks = obs.left_cam.points
            elif obs.right_cam is not None and obs.right_cam.valid:
                landmarks = obs.right_cam.points

            # Annotated preview frame (left view)
            if not args.no_preview:
                preview = left.copy()
                detected = landmarks is not None
                color = (0, 220, 0) if detected else (0, 60, 220)
                label = "Hand detected" if detected else "No hand"
                cv2.putText(
                    preview, label, (12, 32),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.9, color, 2,
                )
                buf.write(landmarks, preview)
            else:
                buf.write(landmarks, None)

    threading.Thread(target=_camera_loop, daemon=True).start()

    # ------------------------------------------------------------------
    # Keyboard listener thread
    # ------------------------------------------------------------------

    def _keyboard_listener() -> None:
        print("[teleop] Commands:  q + Enter = stop\n")
        while active["flag"]:
            try:
                cmd = input().strip().lower()
            except EOFError:
                break
            if cmd == "q":
                active["flag"] = False
                print("[teleop] Stopping… close the MuJoCo window to exit.")

    threading.Thread(target=_keyboard_listener, daemon=True).start()

    # ------------------------------------------------------------------
    # Teleoperation callback — runs on main thread at physics_hz
    # ------------------------------------------------------------------

    last_print = [0.0]
    last_targets = [np.zeros(16)]

    def teleop_callback(s: HandSim) -> None:
        if not active["flag"]:
            return

        landmarks, frame = buf.read()

        if landmarks is not None:
            targets = retargeter.retarget(landmarks)
            last_targets[0] = targets
            s.set_finger_targets(targets)

        # Wrist fixed at default until stereo triangulation is available
        s.set_wrist_pos(_DEFAULT_WRIST_POS)

        # Show OpenCV preview (non-blocking)
        if not args.no_preview and frame is not None:
            cv2.imshow("LEAP Hand Teleop — left view", frame)
            cv2.waitKey(1)

        # Terminal readout at 5 Hz
        now = time.perf_counter()
        if now - last_print[0] >= _PRINT_DT:
            last_print[0] = now
            detected = "✓ hand" if landmarks is not None else "✗ no hand"
            t = last_targets[0]
            row1 = "  ".join(f"{n}:{v:+.2f}" for n, v in zip(_JOINT_NAMES[:8],  t[:8]))
            row2 = "  ".join(f"{n}:{v:+.2f}" for n, v in zip(_JOINT_NAMES[8:], t[8:]))
            print(f"\r[{detected}]  {row1}", flush=True)
            print(f"             {row2}", end="\033[1A", flush=True)

    # ------------------------------------------------------------------
    # Run — blocks on main thread (GLFW/macOS requirement)
    # ------------------------------------------------------------------

    print(
        "\n[teleop] LEAP Hand simulation starting…\n"
        f"  Camera  : {args.camera}  (ZED stereo)\n"
        f"  Scale   : {args.scale}\n"
        f"  Preview : {'off' if args.no_preview else 'on'}\n"
    )

    try:
        sim.run(teleop_callback=teleop_callback)
    finally:
        active["flag"] = False
        detector.close()
        camera.close()
        if not args.no_preview:
            cv2.destroyAllWindows()
        print("\n[teleop] Done.")


if __name__ == "__main__":
    main()
