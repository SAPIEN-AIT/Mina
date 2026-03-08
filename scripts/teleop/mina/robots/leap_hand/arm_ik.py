"""
arm_ik.py — Hybrid delta IK for the humanoid arms (left or right).

Single-pass damped least-squares: computes Δq from the real EE error each
frame (no iterative loop, no scratch MjData copy).  Because the error is
measured against the *actual* EE position, drift is self-correcting.
"""

import numpy as np
import mujoco

# ── Joint / actuator / EE names per side ─────────────────────────────────────
_RIGHT_JOINTS = [
    "arm_right_shoulder_pitch_joint",
    "arm_right_shoulder_roll_joint",
    "arm_right_shoulder_yaw_joint",
    "arm_right_elbow_pitch_joint",
    "arm_right_elbow_roll_joint",
]
_RIGHT_ACTS = [
    "hold_arm_right_shoulder_pitch_joint",
    "hold_arm_right_shoulder_roll_joint",
    "hold_arm_right_shoulder_yaw_joint",
    "hold_arm_right_elbow_pitch_joint",
    "hold_arm_right_elbow_roll_joint",
]
_RIGHT_EE = "arm_right_elbow_roll"

_LEFT_JOINTS = [
    "arm_left_shoulder_pitch_joint",
    "arm_left_shoulder_roll_joint",
    "arm_left_shoulder_yaw_joint",
    "arm_left_elbow_pitch_joint",
    "arm_left_elbow_roll_joint",
]
_LEFT_ACTS = [
    "hold_arm_left_shoulder_pitch_joint",
    "hold_arm_left_shoulder_roll_joint",
    "hold_arm_left_shoulder_yaw_joint",
    "hold_arm_left_elbow_pitch_joint",
    "hold_arm_left_elbow_roll_joint",
]
_LEFT_EE = "arm_left_elbow_roll"


class ArmIKSolver:
    """Hybrid delta IK with direct kinematic qpos control.

    Each call to ``solve()`` performs a single-pass DLS resolution on
    the *real* simulation data (no scratch copy).  The error vector is
    ``target_pos − current_ee_pos``, which naturally corrects drift.

    Parameters
    ----------
    model : MjModel
    side : str
        ``"right"`` or ``"left"``.
    damping : float
        DLS regularisation (λ).
    ik_step : float
        Gain applied to Δq (0 < gain ≤ 1).  Higher = more reactive.
    """

    def __init__(self, model: mujoco.MjModel,
                 side: str = "right",
                 damping: float = 1e-2,
                 ik_step: float = 0.8):
        self.side = side
        self.damping = damping
        self.ik_step = ik_step

        if side == "right":
            jnt_names, act_names, ee_body = _RIGHT_JOINTS, _RIGHT_ACTS, _RIGHT_EE
        elif side == "left":
            jnt_names, act_names, ee_body = _LEFT_JOINTS, _LEFT_ACTS, _LEFT_EE
        else:
            raise ValueError(f"side must be 'right' or 'left', got {side!r}")

        self.ee_body_id = model.body(ee_body).id
        self.n_arm = len(jnt_names)

        self.jnt_ids = np.array(
            [model.joint(n).id for n in jnt_names], dtype=int)
        self.qpos_adr = np.array(
            [model.jnt_qposadr[j] for j in self.jnt_ids], dtype=int)
        self.dof_adr = np.array(
            [model.jnt_dofadr[j] for j in self.jnt_ids], dtype=int)

        self.jnt_range = np.array(
            [model.jnt_range[j] for j in self.jnt_ids])

        self.act_indices = np.array(
            [mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_ACTUATOR, n)
             for n in act_names], dtype=int)

        self.jacp = np.zeros((3, model.nv))
        self._last_q = None

    def solve(self, model: mujoco.MjModel, data: mujoco.MjData,
              target_pos: np.ndarray, target_quat: np.ndarray) -> dict:
        """Single-pass hybrid delta IK.

        1. ``mj_forward`` on real data to get current EE pose + Jacobian
        2. Δx = target − ee_pos  (self-correcting: always chases the real EE)
        3. Δq = (Jp^T Jp + λI)^{-1} Jp^T Δx
        4. q_new = q + gain * Δq, clamped to joint limits

        Returns a dict with 'deg' and 'err_mm' for HUD display.
        """
        mujoco.mj_forward(model, data)

        ee_pos = data.xpos[self.ee_body_id]
        pos_err = target_pos - ee_pos

        mujoco.mj_jacBody(model, data, self.jacp, None, self.ee_body_id)
        Jp = self.jacp[:, self.dof_adr]

        JtJ = Jp.T @ Jp + self.damping * np.eye(self.n_arm)
        dq = np.linalg.solve(JtJ, Jp.T @ pos_err)

        cur_q = data.qpos[self.qpos_adr]
        new_q = cur_q + self.ik_step * dq
        new_q = np.clip(new_q, self.jnt_range[:, 0], self.jnt_range[:, 1])

        data.qpos[self.qpos_adr] = new_q
        data.qvel[self.dof_adr] = 0.0
        for i, act_idx in enumerate(self.act_indices):
            data.ctrl[act_idx] = new_q[i]

        self._last_q = new_q.copy()

        err_mm = np.linalg.norm(pos_err) * 1000
        deg = np.degrees(new_q)
        return {"deg": deg, "err_mm": err_mm}

    def clamp_after_step(self, data: mujoco.MjData) -> None:
        """Re-assert arm joints after mj_step so physics drift doesn't accumulate."""
        if self._last_q is not None:
            data.qpos[self.qpos_adr] = self._last_q
            data.qvel[self.dof_adr] = 0.0
