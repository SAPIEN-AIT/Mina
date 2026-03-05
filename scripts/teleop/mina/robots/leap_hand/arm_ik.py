"""
arm_ik.py — Kinematic IK for the humanoid right arm.

Iterative damped least-squares on a *separate* MjData copy, then
directly sets qpos (kinematic control) with per-frame rate limiting.
"""

import numpy as np
import mujoco

_ARM_JOINT_NAMES = [
    "arm_right_shoulder_pitch_joint",
    "arm_right_shoulder_roll_joint",
    "arm_right_shoulder_yaw_joint",
    "arm_right_elbow_pitch_joint",
    "arm_right_elbow_roll_joint",
]

_ARM_ACT_NAMES = [
    "hold_arm_right_shoulder_pitch_joint",
    "hold_arm_right_shoulder_roll_joint",
    "hold_arm_right_shoulder_yaw_joint",
    "hold_arm_right_elbow_pitch_joint",
    "hold_arm_right_elbow_roll_joint",
]

_EE_BODY = "arm_right_elbow_roll"


def _quat_error(target_quat, current_quat):
    """Orientation error as a 3-D rotation vector (axis * angle)."""
    tw, tx, ty, tz = target_quat
    cw, cx, cy, cz = current_quat

    ew =  tw * cw + tx * cx + ty * cy + tz * cz
    ex = -tw * cx + tx * cw - ty * cz + tz * cy
    ey = -tw * cy + tx * cz + ty * cw - tz * cx
    ez = -tw * cz - tx * cy + ty * cx + tz * cw

    if ew < 0:
        ew, ex, ey, ez = -ew, -ex, -ey, -ez

    sin_half = np.sqrt(ex * ex + ey * ey + ez * ez)
    if sin_half < 1e-8:
        return np.zeros(3)

    angle = 2.0 * np.arctan2(sin_half, ew)
    axis = np.array([ex, ey, ez]) / sin_half
    return axis * angle


class ArmIKSolver:
    """Iterative DLS IK with direct kinematic qpos control."""

    def __init__(self, model: mujoco.MjModel,
                 damping: float = 1e-2,
                 max_iter: int = 50,
                 tol: float = 1e-3,
                 ik_step: float = 0.5,
                 max_delta_per_frame: float = 0.08,
                 orient_weight: float = 0.0):
        self.damping = damping
        self.max_iter = max_iter
        self.tol = tol
        self.ik_step = ik_step
        self.max_delta = max_delta_per_frame
        self.orient_weight = orient_weight

        self.ee_body_id = model.body(_EE_BODY).id
        self.n_arm = len(_ARM_JOINT_NAMES)

        self.jnt_ids = np.array(
            [model.joint(n).id for n in _ARM_JOINT_NAMES], dtype=int)
        self.qpos_adr = np.array(
            [model.jnt_qposadr[j] for j in self.jnt_ids], dtype=int)
        self.dof_adr = np.array(
            [model.jnt_dofadr[j] for j in self.jnt_ids], dtype=int)

        self.jnt_range = np.array(
            [model.jnt_range[j] for j in self.jnt_ids])

        self.act_indices = np.array(
            [mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_ACTUATOR, n)
             for n in _ARM_ACT_NAMES], dtype=int)

        self._ik_data = mujoco.MjData(model)
        self.jacp = np.zeros((3, model.nv))
        self.jacr = np.zeros((3, model.nv))

    def solve(self, model: mujoco.MjModel, data: mujoco.MjData,
              target_pos: np.ndarray, target_quat: np.ndarray) -> dict:
        """Run IK on a scratch copy and kinematically drive the arm.

        Returns a dict with 'deg' (joint angles in degrees) and 'err_mm'
        (position error in millimetres) for HUD display.
        """

        cur_arm_q = data.qpos[self.qpos_adr].copy()

        # --- Iterative IK on a separate MjData (no side effects) ---
        d = self._ik_data
        d.qpos[:] = data.qpos

        for _ in range(self.max_iter):
            mujoco.mj_forward(model, d)

            ee_pos = d.xpos[self.ee_body_id]
            ee_quat = d.xquat[self.ee_body_id]

            pos_err = target_pos - ee_pos
            rot_err = _quat_error(target_quat, ee_quat) * self.orient_weight
            err = np.concatenate([pos_err, rot_err])

            if np.linalg.norm(err) < self.tol:
                break

            mujoco.mj_jacBody(model, d, self.jacp, self.jacr,
                              self.ee_body_id)

            J = np.vstack([
                self.jacp[:, self.dof_adr],
                self.jacr[:, self.dof_adr],
            ])

            JtJ = J.T @ J + self.damping * np.eye(self.n_arm)
            dq = np.linalg.solve(JtJ, J.T @ err)

            arm_q = d.qpos[self.qpos_adr] + self.ik_step * dq
            arm_q = np.clip(arm_q, self.jnt_range[:, 0],
                             self.jnt_range[:, 1])
            d.qpos[self.qpos_adr] = arm_q

        ik_target = d.qpos[self.qpos_adr].copy()

        # --- Rate-limit the joint change per frame ---
        delta = ik_target - cur_arm_q
        delta = np.clip(delta, -self.max_delta, self.max_delta)
        new_q = cur_arm_q + delta
        new_q = np.clip(new_q, self.jnt_range[:, 0],
                         self.jnt_range[:, 1])

        # --- Write kinematically: qpos, zero velocity, matching ctrl ---
        data.qpos[self.qpos_adr] = new_q
        data.qvel[self.dof_adr] = 0.0
        for i, act_idx in enumerate(self.act_indices):
            data.ctrl[act_idx] = new_q[i]

        ee_pos = data.xpos[self.ee_body_id]
        err_mm = np.linalg.norm(target_pos - ee_pos) * 1000
        deg = np.degrees(new_q)
        return {"deg": deg, "err_mm": err_mm}

    def clamp_after_step(self, data: mujoco.MjData) -> None:
        """Enforce joint limits after mj_step (physics can violate them)."""
        q = data.qpos[self.qpos_adr]
        clamped = np.clip(q, self.jnt_range[:, 0], self.jnt_range[:, 1])
        if not np.allclose(q, clamped):
            data.qpos[self.qpos_adr] = clamped
            data.qvel[self.dof_adr] = 0.0
