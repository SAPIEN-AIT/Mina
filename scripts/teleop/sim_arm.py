"""MuJoCo arm simulation for teleoperation development.

Loads the Berkeley Humanoid Lite arm model (both arms, torso fixed to world)
and runs a real-time physics simulation with PD control.

Plug your teleoperation system into `ArmSim.run()` via the `teleop_callback`
parameter. The callback receives the simulator instance every physics step and
should call `sim.set_joint_targets(targets)` with a (10,) numpy array.

Joint ordering (10 DOF):
    Index  Name
    -----  ----
    0      arm_left_shoulder_pitch
    1      arm_left_shoulder_roll
    2      arm_left_shoulder_yaw
    3      arm_left_elbow_pitch
    4      arm_left_elbow_roll
    5      arm_right_shoulder_pitch
    6      arm_right_shoulder_roll
    7      arm_right_shoulder_yaw
    8      arm_right_elbow_pitch
    9      arm_right_elbow_roll

Usage:
    # Built-in sinusoidal demo
    python scripts/teleop/sim_arm.py

    # Custom scene path
    python scripts/teleop/sim_arm.py --scene /path/to/bhl_arm_scene.xml

    # Tune PD gains
    python scripts/teleop/sim_arm.py --kp 30 --kd 3
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import mujoco
import mujoco.viewer
import numpy as np

# Path setup — allows running from repo root without install
_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "source" / "mina_assets"))

from mina_assets import ARM_SCENE

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

DEFAULT_SCENE = ARM_SCENE

# ---------------------------------------------------------------------------
# Joint definitions
# ---------------------------------------------------------------------------

JOINT_NAMES: list[str] = [
    "arm_left_shoulder_pitch_joint",
    "arm_left_shoulder_roll_joint",
    "arm_left_shoulder_yaw_joint",
    "arm_left_elbow_pitch_joint",
    "arm_left_elbow_roll_joint",
    "arm_right_shoulder_pitch_joint",
    "arm_right_shoulder_roll_joint",
    "arm_right_shoulder_yaw_joint",
    "arm_right_elbow_pitch_joint",
    "arm_right_elbow_roll_joint",
]
NUM_JOINTS: int = len(JOINT_NAMES)  # 10


# ---------------------------------------------------------------------------
# Configuration dataclass
# ---------------------------------------------------------------------------

@dataclass
class ArmSimConfig:
    """Simulation and control parameters."""

    scene_path: Path = field(default_factory=lambda: DEFAULT_SCENE)

    # PD gains (applied uniformly to all joints)
    kp: float = 20.0   # proportional gain  [N·m / rad]
    kd: float = 2.0    # derivative gain    [N·m / (rad/s)]

    # Safety
    torque_limit: float = 20.0  # N·m — matches model forcerange

    # Physics
    physics_hz: float = 500.0   # simulation rate (MuJoCo timestep = 1/physics_hz)

    # Viewer camera defaults
    cam_distance: float = 1.6
    cam_azimuth: float = 135.0
    cam_elevation: float = -20.0
    cam_lookat: tuple[float, float, float] = (0.0, 0.0, 0.8)


# ---------------------------------------------------------------------------
# Observation dataclass
# ---------------------------------------------------------------------------

@dataclass
class ArmObs:
    """Snapshot of the arm state returned each step."""

    joint_pos: np.ndarray   # shape (10,) — radians
    joint_vel: np.ndarray   # shape (10,) — rad/s
    joint_torque: np.ndarray  # shape (10,) — N·m (measured)

    left_ee_pos: np.ndarray    # shape (3,) — world position of left end-effector
    left_ee_quat: np.ndarray   # shape (4,) — wxyz quaternion
    right_ee_pos: np.ndarray   # shape (3,)
    right_ee_quat: np.ndarray  # shape (4,)


# ---------------------------------------------------------------------------
# ArmSim
# ---------------------------------------------------------------------------

class ArmSim:
    """Real-time arm simulation with external PD control.

    Parameters
    ----------
    cfg:
        Simulation configuration.  Pass ``ArmSimConfig()`` for defaults.
    """

    def __init__(self, cfg: ArmSimConfig | None = None) -> None:
        self.cfg = cfg or ArmSimConfig()

        self.model = mujoco.MjModel.from_xml_path(str(self.cfg.scene_path))
        self.data = mujoco.MjData(self.model)

        # Override timestep from config
        self.model.opt.timestep = 1.0 / self.cfg.physics_hz

        # Pre-resolve MuJoCo IDs for fast per-step lookup
        self._act_ids = self._resolve_ids(
            mujoco.mjtObj.mjOBJ_ACTUATOR, JOINT_NAMES
        )
        self._qpos_ids = [
            self.model.joint(name).qposadr[0] for name in JOINT_NAMES
        ]
        self._qvel_ids = [
            self.model.joint(name).dofadr[0] for name in JOINT_NAMES
        ]
        self._sensor = {
            name: mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_SENSOR, name)
            for name in [
                "arm_left_shoulder_pitch_torque",
                "arm_left_shoulder_roll_torque",
                "arm_left_shoulder_yaw_torque",
                "arm_left_elbow_pitch_torque",
                "arm_left_elbow_roll_torque",
                "arm_right_shoulder_pitch_torque",
                "arm_right_shoulder_roll_torque",
                "arm_right_shoulder_yaw_torque",
                "arm_right_elbow_pitch_torque",
                "arm_right_elbow_roll_torque",
                "arm_left_ee_pos",
                "arm_left_ee_quat",
                "arm_right_ee_pos",
                "arm_right_ee_quat",
            ]
        }

        # Desired joint positions — updated by set_joint_targets()
        self._targets = np.zeros(NUM_JOINTS)

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def set_joint_targets(self, targets: np.ndarray) -> None:
        """Set desired joint angles for the next PD control step.

        Parameters
        ----------
        targets:
            Shape ``(10,)`` array of desired joint positions in radians,
            ordered according to ``JOINT_NAMES``.
        """
        if targets.shape != (NUM_JOINTS,):
            raise ValueError(
                f"targets must have shape ({NUM_JOINTS},), got {targets.shape}"
            )
        self._targets[:] = targets

    def get_obs(self) -> ArmObs:
        """Return the current arm state as an :class:`ArmObs`."""
        q   = np.array([self.data.qpos[i] for i in self._qpos_ids])
        qd  = np.array([self.data.qvel[i] for i in self._qvel_ids])
        tau = self._read_sensor_block(
            [f"arm_{side}_{j}_torque"
             for side in ("left", "right")
             for j in ("shoulder_pitch", "shoulder_roll", "shoulder_yaw",
                       "elbow_pitch", "elbow_roll")]
        )
        return ArmObs(
            joint_pos=q,
            joint_vel=qd,
            joint_torque=tau,
            left_ee_pos=self._read_sensor("arm_left_ee_pos"),
            left_ee_quat=self._read_sensor("arm_left_ee_quat"),
            right_ee_pos=self._read_sensor("arm_right_ee_pos"),
            right_ee_quat=self._read_sensor("arm_right_ee_quat"),
        )

    def run(
        self,
        teleop_callback: Callable[[ArmSim], None] | None = None,
    ) -> None:
        """Launch the MuJoCo viewer and run the simulation loop.

        Parameters
        ----------
        teleop_callback:
            Called once per physics step with ``self`` as the only argument.
            Use it to read observations and push new joint targets::

                def my_teleop(sim: ArmSim) -> None:
                    obs = sim.get_obs()
                    targets = my_system.compute(obs)
                    sim.set_joint_targets(targets)

                sim.run(teleop_callback=my_teleop)
        """
        cfg = self.cfg

        with mujoco.viewer.launch_passive(self.model, self.data) as viewer:
            # Default camera pose for a clear arm view
            viewer.cam.distance  = cfg.cam_distance
            viewer.cam.azimuth   = cfg.cam_azimuth
            viewer.cam.elevation = cfg.cam_elevation
            viewer.cam.lookat[:] = cfg.cam_lookat

            print(
                f"\n[ArmSim] Running — close the viewer window to exit.\n"
                f"  Scene   : {cfg.scene_path}\n"
                f"  DOF     : {NUM_JOINTS}\n"
                f"  Kp / Kd : {cfg.kp} / {cfg.kd}\n"
                f"  Hz      : {cfg.physics_hz}\n"
            )

            dt = self.model.opt.timestep

            while viewer.is_running():
                t_start = time.perf_counter()

                # 1. Teleoperation callback (user sets targets here)
                if teleop_callback is not None:
                    teleop_callback(self)

                # 2. PD control → write torques to ctrl
                self._apply_pd_control()

                # 3. Physics step
                mujoco.mj_step(self.model, self.data)

                # 4. Render
                viewer.sync()

                # 5. Real-time pacing
                elapsed = time.perf_counter() - t_start
                if elapsed < dt:
                    time.sleep(dt - elapsed)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _apply_pd_control(self) -> None:
        kp, kd = self.cfg.kp, self.cfg.kd
        limit  = self.cfg.torque_limit
        for i, (act_id, qpos_id, qvel_id) in enumerate(
            zip(self._act_ids, self._qpos_ids, self._qvel_ids)
        ):
            err_pos = self._targets[i] - self.data.qpos[qpos_id]
            err_vel = -self.data.qvel[qvel_id]
            torque  = kp * err_pos + kd * err_vel
            self.data.ctrl[act_id] = float(np.clip(torque, -limit, limit))

    def _resolve_ids(
        self,
        obj_type: mujoco.mjtObj,
        names: list[str],
    ) -> list[int]:
        ids = [mujoco.mj_name2id(self.model, obj_type, n) for n in names]
        missing = [n for n, i in zip(names, ids) if i < 0]
        if missing:
            raise RuntimeError(f"MuJoCo names not found in model: {missing}")
        return ids

    def _read_sensor(self, name: str) -> np.ndarray:
        sid   = self._sensor[name]
        start = self.model.sensor_adr[sid]
        dim   = self.model.sensor_dim[sid]
        return self.data.sensordata[start : start + dim].copy()

    def _read_sensor_block(self, names: list[str]) -> np.ndarray:
        return np.array([self._read_sensor(n)[0] for n in names])


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _demo_callback(sim: ArmSim) -> None:
    """Sinusoidal sweep — replace with your teleoperation system."""
    t = sim.data.time
    targets = np.zeros(NUM_JOINTS)
    # Left elbow pitch: 0 → ~45°
    targets[3] =  0.4 * np.sin(0.5 * t)
    # Right elbow pitch (mirrored)
    targets[8] = -0.4 * np.sin(0.5 * t)
    # Gentle shoulder roll outward on both sides
    targets[1] = 0.15 * np.sin(0.3 * t + 0.5)
    targets[6] = 0.15 * np.sin(0.3 * t + 0.5)
    sim.set_joint_targets(targets)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Berkeley Humanoid Lite — arm MuJoCo simulation",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--scene",
        type=Path,
        default=DEFAULT_SCENE,
        help="Path to the MuJoCo scene XML",
    )
    parser.add_argument("--kp",  type=float, default=20.0, help="PD proportional gain")
    parser.add_argument("--kd",  type=float, default=2.0,  help="PD derivative gain")
    parser.add_argument("--hz",  type=float, default=500.0, help="Physics simulation rate")
    args = parser.parse_args()

    cfg = ArmSimConfig(
        scene_path=args.scene,
        kp=args.kp,
        kd=args.kd,
        physics_hz=args.hz,
    )

    sim = ArmSim(cfg)

    # -----------------------------------------------------------------------
    # Plug your teleoperation system here.
    # Replace `_demo_callback` with your own function or callable object.
    # -----------------------------------------------------------------------
    sim.run(teleop_callback=_demo_callback)


if __name__ == "__main__":
    main()
