"""Monocular MediaPipe → ArmSim teleoperation (right arm).

Reads the Mac webcam via MediaPipe Pose in a background thread and drives
the right arm of the Berkeley Humanoid Lite arm simulation in real time.

Controls
--------
    c   Calibrate neutral pose (hold arms relaxed at your sides, then press c)
    q   Quit

Usage
-----
    python scripts/teleop/teleop_sim_mediapipe.py [--side right|left] [--camera 0]
    python scripts/teleop/teleop_sim_mediapipe.py --kp 25 --kd 2.5
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2
import numpy as np

# Allow running from repo root without install
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "source" / "mina_teleop"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mina_teleop.pose.arm_retargeter import ArmRetargeter
from mina_teleop.pose.mediapipe_estimator import MediaPipeArmEstimator

from scripts.teleop.sim_arm import ArmSim, ArmSimConfig

# ---------------------------------------------------------------------------
# Arm index offsets per side
# ---------------------------------------------------------------------------

_JOINT_OFFSET = {"left": 0, "right": 5}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="MediaPipe monocular teleoperation → ArmSim",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--side",   default="right", choices=["right", "left"])
    parser.add_argument("--camera", type=int, default=0)
    parser.add_argument("--kp",     type=float, default=20.0)
    parser.add_argument("--kd",     type=float, default=2.0)
    parser.add_argument("--smoothing", type=float, default=0.7,
                        help="EMA smoothing (0=none, 0.9=heavy)")
    args = parser.parse_args()

    # --- MediaPipe estimator (background thread) ---
    estimator = MediaPipeArmEstimator(side=args.side, camera_id=args.camera)
    estimator.start()

    # --- Retargeter ---
    retargeter = ArmRetargeter(side=args.side, smoothing=args.smoothing)

    # --- Sim ---
    sim_cfg = ArmSimConfig(kp=args.kp, kd=args.kd)
    sim = ArmSim(sim_cfg)

    joint_offset = _JOINT_OFFSET[args.side]

    # Shared state — written by callback, read by display loop
    _latest_angles: np.ndarray = np.zeros(5)
    _calibrated: bool = False

    print(
        "\n[teleop_sim_mediapipe]"
        "\n  Press  c  in the OpenCV window to calibrate neutral pose"
        "\n  Press  q  in the OpenCV window to quit\n"
    )

    # --- Teleoperation callback (runs at sim rate, ~500 Hz) ---
    def teleop_callback(s: ArmSim) -> None:
        nonlocal _latest_angles, _calibrated

        lm = estimator.get_landmarks()
        if lm is None:
            return

        if not _calibrated:
            # Hold at zero until the user calibrates
            return

        angles = retargeter.retarget(lm)
        _latest_angles = angles

        targets = np.zeros(10)
        targets[joint_offset : joint_offset + 5] = angles
        s.set_joint_targets(targets)

    # --- Display loop (runs alongside sim in same thread via OpenCV poll) ---
    # We launch the sim in a background thread so we can poll the OpenCV
    # window for keypresses on the main thread.
    import threading

    sim_done = threading.Event()

    def sim_thread() -> None:
        sim.run(teleop_callback=teleop_callback)
        sim_done.set()

    t = threading.Thread(target=sim_thread, daemon=True)
    t.start()

    while not sim_done.is_set():
        frame = estimator.get_debug_frame()
        if frame is not None:
            # Overlay current joint angles
            labels = ["sh_pitch", "sh_roll", "sh_yaw", "el_pitch", "el_roll"]
            for i, (label, val) in enumerate(zip(labels, _latest_angles)):
                text = f"{label}: {val:+.2f} rad"
                color = (0, 255, 0) if _calibrated else (0, 180, 255)
                cv2.putText(frame, text, (10, 25 + i * 22),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 1)

            status = "CALIBRATED" if _calibrated else "Press 'c' to calibrate"
            cv2.putText(frame, status, (10, frame.shape[0] - 12),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                        (0, 255, 0) if _calibrated else (0, 100, 255), 2)

            cv2.imshow("MediaPipe Teleop", frame)

        key = cv2.waitKey(1) & 0xFF
        if key == ord("q"):
            break
        elif key == ord("c"):
            lm = estimator.get_landmarks()
            if lm is not None:
                retargeter.calibrate(lm)
                _calibrated = True
            else:
                print("[teleop] No landmarks detected — move into camera view first.")

    cv2.destroyAllWindows()
    estimator.stop()


if __name__ == "__main__":
    main()
