"""Bimanual MediaPipe → ArmSim teleoperation (both arms).

Reads the Mac webcam via MediaPipe Pose in a background thread and drives
both arms of the Berkeley Humanoid Lite arm simulation in real time.

Both arms are extracted from a single MediaPipe inference per frame —
no duplicate camera processing.

Calibration
-----------
  Auto-calibration: hold both arms relaxed at your sides. The script counts
  down 3 seconds then captures the neutral pose automatically.

  Manual re-calibration: type  c + Enter  in the terminal at any time.
  Type  q + Enter  to stop the teleop (MuJoCo window stays open).

Usage
-----
    mjpython scripts/teleop/teleop_sim_mediapipe.py
    mjpython scripts/teleop/teleop_sim_mediapipe.py --countdown 5 --smoothing 0.8
"""

from __future__ import annotations

import argparse
import sys
import threading
import time
from pathlib import Path

import numpy as np

# ---------------------------------------------------------------------------
# Path setup — allows running from repo root without install
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "source" / "mina_teleop"))
sys.path.insert(0, str(_REPO_ROOT / "scripts" / "teleop"))

from mina_teleop.pose.arm_retargeter import BimanualRetargeter
from mina_teleop.pose.mediapipe_estimator import BimanualArmEstimator
from sim_arm import ArmSim, ArmSimConfig

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_PRINT_DT = 1.0 / 5.0   # terminal refresh at 5 Hz

_LABELS = [
    "L sh_pitch", "L sh_roll ", "L sh_yaw  ", "L el_pitch", "L el_roll ",
    "R sh_pitch", "R sh_roll ", "R sh_yaw  ", "R el_pitch", "R el_roll ",
]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Bimanual MediaPipe teleoperation → ArmSim",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--camera",    type=int,   default=0)
    parser.add_argument("--kp",        type=float, default=20.0)
    parser.add_argument("--kd",        type=float, default=2.0)
    parser.add_argument("--smoothing", type=float, default=0.7)
    parser.add_argument("--countdown", type=int,   default=3,
                        help="Seconds before auto-calibration at launch")
    args = parser.parse_args()

    # ------------------------------------------------------------------
    # Components
    # ------------------------------------------------------------------

    estimator  = BimanualArmEstimator(camera_id=args.camera)
    retargeter = BimanualRetargeter(smoothing=args.smoothing)
    sim        = ArmSim(ArmSimConfig(kp=args.kp, kd=args.kd))

    estimator.start()

    # ------------------------------------------------------------------
    # Shared state
    # ------------------------------------------------------------------

    state = {
        "calibrated":          False,
        "calibrate_requested": False,
        "latest_targets":      np.zeros(10),
        "last_print":          0.0,
        "active":              True,
    }

    # ------------------------------------------------------------------
    # Background threads
    # ------------------------------------------------------------------

    def _auto_calibrate() -> None:
        print("\n[teleop] Hold BOTH arms relaxed at your sides.")
        for i in range(args.countdown, 0, -1):
            print(f"[teleop] Calibrating in {i}…")
            time.sleep(1.0)

        while state["active"]:
            lm = estimator.get_landmarks()
            if lm is not None:
                retargeter.calibrate(lm)
                state["calibrated"] = True
                print("[teleop] ✓ Calibrated — move your arms to control the sim.\n")
                return
            time.sleep(0.05)

    def _keyboard_listener() -> None:
        print("[teleop] Commands:  c + Enter = recalibrate   |   q + Enter = stop teleop\n")
        while state["active"]:
            try:
                cmd = input().strip().lower()
            except EOFError:
                break
            if cmd == "c":
                state["calibrate_requested"] = True
            elif cmd == "q":
                state["active"] = False
                print("[teleop] Teleop stopped. Close the MuJoCo window to fully exit.")

    threading.Thread(target=_auto_calibrate,    daemon=True).start()
    threading.Thread(target=_keyboard_listener, daemon=True).start()

    # ------------------------------------------------------------------
    # Teleoperation callback — runs on main thread at ~500 Hz
    # ------------------------------------------------------------------

    def teleop_callback(s: ArmSim) -> None:
        if not state["active"]:
            return

        lm = estimator.get_landmarks()

        if state["calibrate_requested"] and lm is not None:
            retargeter.calibrate(lm)
            state["calibrated"] = True
            state["calibrate_requested"] = False
            print("[teleop] ✓ Recalibrated.")

        if lm is not None and state["calibrated"]:
            targets = retargeter.retarget(lm)
            state["latest_targets"] = targets
            s.set_joint_targets(targets)

        now = time.perf_counter()
        if state["calibrated"] and now - state["last_print"] >= _PRINT_DT:
            state["last_print"] = now
            t = state["latest_targets"]
            left  = "  ".join(f"{l}:{v:+.2f}" for l, v in zip(_LABELS[:5],  t[:5]))
            right = "  ".join(f"{l}:{v:+.2f}" for l, v in zip(_LABELS[5:], t[5:]))
            print(f"\r[L] {left}", end="\n", flush=True)
            print(f"[R] {right}", end="\033[1A", flush=True)  # move cursor up 1 line

    # ------------------------------------------------------------------
    # Run — blocks on main thread (required by GLFW/macOS)
    # ------------------------------------------------------------------

    try:
        sim.run(teleop_callback=teleop_callback)
    finally:
        state["active"] = False
        estimator.stop()
        print("\n")


if __name__ == "__main__":
    main()
