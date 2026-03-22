"""Standalone Isaac Sim sim2sim playback — no ROS, no gym.

Loads a trained ONNX policy and runs it against a single robot in Isaac Sim
with PD joint control at 25 Hz. Spawns the robot from the assets package USD
into a clean stage — the same USD files Isaac Lab uses for training.

Usage:
    uv run ./scripts/sim2sim/play_isaacsim.py --config ./configs/policy_humanoid.yaml
"""

import argparse
import sys
import time
import os

import numpy as np

# ── AppLauncher must come first — it sets up the kit framework and handles
#    LIVESTREAM automatically (same path play.py takes, so streaming works).
from isaaclab.app import AppLauncher

parser = argparse.ArgumentParser(description="Isaac Sim sim2sim playback (no ROS)")
parser.add_argument("--config", type=str, default="./configs/policy_humanoid.yaml",
                    help="Path to policy configuration YAML")
AppLauncher.add_app_launcher_args(parser)
args = parser.parse_args()

app_launcher = AppLauncher(args)
simulation_app = app_launcher.app

# omni.isaac.core is not in the gym headless kit — enable it explicitly.
import omni.kit.app
omni.kit.app.get_app().get_extension_manager().set_extension_enabled_immediate("omni.isaac.core", True)

# ── Now safe to import Omni / Isaac Sim modules ─────────────────────────
from omni.isaac.core import World
from omni.isaac.core.articulations import Articulation
from omni.isaac.core.utils.stage import add_reference_to_stage
from omni.isaac.core.utils.types import ArticulationAction

from omegaconf import OmegaConf
from berkeley_humanoid_lite_lowlevel.policy.rl_controller import RlController
from berkeley_humanoid_lite_lowlevel.policy.gamepad import Se2Gamepad


# ── Robot USD paths (relative to project root) ──────────────────────────
ROBOT_USD_DIR = os.path.join(
    "source", "berkeley_humanoid_lite_assets", "data", "robots",
    "berkeley_humanoid", "berkeley_humanoid_lite", "usd",
)
ROBOT_USD = {
    22: os.path.join(ROBOT_USD_DIR, "berkeley_humanoid_lite.usd"),
    12: os.path.join(ROBOT_USD_DIR, "berkeley_humanoid_lite_biped.usd"),
}

ROBOT_PRIM = "/World/Robot"
SPAWN_HEIGHT = 0.0  # metres — match Isaac Lab training (PhysX resolves ground penetration)

# Isaac Lab trains at 0.005s physics dt with decimation=8 → 25 Hz policy.
SIM_PHYSICS_DT = 0.005


def quat_rotate_inverse(q, v):
    """Rotate vector v by the inverse of quaternion q [w,x,y,z]."""
    q_w = q[0]
    q_vec = q[1:4]
    a = v * (2.0 * q_w ** 2 - 1.0)
    b = np.cross(q_vec, v) * q_w * 2.0
    c = q_vec * (np.dot(q_vec, v)) * 2.0
    return a - b + c


def main():
    print(f"Loading config from {args.config}")
    with open(args.config, "r") as f:
        cfg = OmegaConf.load(f)

    robot_usd = os.path.abspath(ROBOT_USD[cfg.num_joints])
    assert os.path.exists(robot_usd), f"Robot USD not found: {robot_usd}"

    # ── Create simulation world ──────────────────────────────────────
    world = World(physics_dt=SIM_PHYSICS_DT, rendering_dt=1.0 / 60.0)
    world.scene.add_default_ground_plane()

    import omni.usd
    stage = omni.usd.get_context().get_stage()
    from pxr import UsdPhysics, UsdShade

    # Ground friction matching training terrain (static=1.0, dynamic=1.0)
    mat_prim = stage.DefinePrim("/World/GroundMaterial", "Material")
    physics_mat = UsdPhysics.MaterialAPI.Apply(mat_prim)
    physics_mat.CreateStaticFrictionAttr().Set(1.0)
    physics_mat.CreateDynamicFrictionAttr().Set(1.0)
    physics_mat.CreateRestitutionAttr().Set(0.0)
    ground_prim = stage.GetPrimAtPath("/World/defaultGroundPlane/GroundPlane/CollisionPlane")
    if not ground_prim.IsValid():
        ground_prim = stage.GetPrimAtPath("/World/defaultGroundPlane")
    if ground_prim.IsValid():
        UsdShade.MaterialBindingAPI.Apply(ground_prim).Bind(
            UsdShade.Material(mat_prim),
            UsdShade.Tokens.weakerThanDescendants,
            "physics",
        )

    # ── Spawn robot ──────────────────────────────────────────────────
    add_reference_to_stage(robot_usd, ROBOT_PRIM)

    from pxr import UsdGeom, Gf
    xformable = UsdGeom.Xformable(stage.GetPrimAtPath(ROBOT_PRIM))
    xformable.ClearXformOpOrder()
    xformable.AddTranslateOp().Set(Gf.Vec3d(0.0, 0.0, SPAWN_HEIGHT))

    # ── Joint properties matching Isaac Lab ArticulationCfg ──────────
    from pxr import PhysxSchema
    JOINT_ARMATURE = {
        "arm_":   0.002,
        "leg_":   0.007,
        "ankle_": 0.002,
    }
    for prim in stage.Traverse():
        if not prim.GetPath().pathString.startswith(ROBOT_PRIM):
            continue
        if not prim.IsA(UsdPhysics.Joint):
            continue
        jname = prim.GetName()
        armature = 0.002
        for prefix, val in JOINT_ARMATURE.items():
            if prefix in jname:
                armature = val
                break
        physx_joint = PhysxSchema.PhysxJointAPI.Apply(prim)
        physx_joint.CreateArmatureAttr().Set(armature)
        physx_joint.CreateJointFrictionAttr().Set(0.1)
        physx_joint.CreateMaxJointVelocityAttr().Set(10.0 * 180.0 / np.pi)

    # ── Find articulation root ───────────────────────────────────────
    art_path = None
    for prim in stage.Traverse():
        ppath = str(prim.GetPath())
        if ppath.startswith(ROBOT_PRIM) and prim.HasAPI(UsdPhysics.ArticulationRootAPI):
            art_path = ppath
            break

    if art_path is None:
        for candidate in [f"{ROBOT_PRIM}/base", ROBOT_PRIM]:
            if stage.GetPrimAtPath(candidate).IsValid():
                art_path = candidate
                break

    if art_path is None:
        print("[Error] Could not find articulation root.")
        simulation_app.close()
        sys.exit(1)

    # Solver settings matching Isaac Lab ArticulationRootPropertiesCfg
    art_prim = stage.GetPrimAtPath(art_path)
    physx_art = PhysxSchema.PhysxArticulationAPI.Apply(art_prim)
    physx_art.CreateSolverPositionIterationCountAttr().Set(8)
    physx_art.CreateSolverVelocityIterationCountAttr().Set(4)
    PhysxSchema.PhysxRigidBodyAPI.Apply(art_prim).CreateMaxDepenetrationVelocityAttr().Set(1.0)

    robot = world.scene.add(Articulation(prim_path=art_path, name="robot"))
    world.reset()

    # ── Joint index mapping (config order → articulation order) ─────
    art_name_to_idx = {name: i for i, name in enumerate(robot.dof_names)}
    cfg_to_art = np.array([
        art_name_to_idx.get(jname, 0) for jname in cfg.joints
    ], dtype=np.int32)

    # ── Configure position drives ────────────────────────────────────
    default_positions = np.array(cfg.default_joint_positions, dtype=np.float32)
    num_dofs = robot.num_dof

    art_kp         = np.zeros(num_dofs, dtype=np.float32)
    art_kd         = np.zeros(num_dofs, dtype=np.float32)
    art_max_effort = np.zeros(num_dofs, dtype=np.float32)
    for i, art_idx in enumerate(cfg_to_art):
        art_kp[art_idx]         = cfg.joint_kp[i]
        art_kd[art_idx]         = cfg.joint_kd[i]
        art_max_effort[art_idx] = cfg.effort_limits[i]

    art_controller = robot.get_articulation_controller()
    art_controller.switch_control_mode("position")
    art_controller.set_gains(art_kp, art_kd)
    art_controller.set_max_efforts(art_max_effort)

    # Set initial pose
    art_positions = np.zeros(num_dofs, dtype=np.float32)
    for i, art_idx in enumerate(cfg_to_art):
        art_positions[art_idx] = default_positions[i]
    robot.set_joint_positions(art_positions)
    robot.set_joint_velocities(np.zeros(num_dofs, dtype=np.float32))

    # ── Load policy ──────────────────────────────────────────────────
    controller = RlController(cfg)
    controller.load_policy()

    # ── Gamepad ──────────────────────────────────────────────────────
    try:
        gamepad = Se2Gamepad()
        gamepad.run()
        has_gamepad = True
        print("Gamepad connected.")
    except Exception:
        has_gamepad = False
        print("No gamepad — using zero velocity commands.")

    mode = 3.0
    cmd_vel = np.zeros(3, dtype=np.float32)
    action_indices = list(cfg.action_indices)
    physics_substeps = int(np.round(cfg.policy_dt / SIM_PHYSICS_DT))
    first_tick = True

    print(f"Policy: {1.0/cfg.policy_dt:.0f} Hz | Physics: {1.0/SIM_PHYSICS_DT:.0f} Hz | Substeps: {physics_substeps}")

    # ── Main loop ────────────────────────────────────────────────────
    try:
        while simulation_app.is_running():
            step_start = time.perf_counter()

            if has_gamepad:
                gp = gamepad.commands
                if gp["mode_switch"] != 0:
                    mode = float(gp["mode_switch"])
                cmd_vel[0] = gp["velocity_x"]
                cmd_vel[1] = gp["velocity_y"] * 0.5
                cmd_vel[2] = gp["velocity_yaw"]

            art_joint_pos = robot.get_joint_positions()
            art_joint_vel = robot.get_joint_velocities()
            joint_pos = art_joint_pos[cfg_to_art]
            joint_vel = art_joint_vel[cfg_to_art]

            if first_tick:
                joint_pos = np.zeros_like(joint_pos)
                joint_vel = np.zeros_like(joint_vel)
                first_tick = False

            base_pos, base_quat = robot.get_world_pose()
            base_quat = base_quat.astype(np.float32)

            base_ang_vel_world = robot.get_angular_velocity()
            if base_ang_vel_world is None:
                base_ang_vel_world = np.zeros(3, dtype=np.float32)
            base_ang_vel_body = quat_rotate_inverse(base_quat, base_ang_vel_world).astype(np.float32)

            obs = np.concatenate([
                base_quat,
                base_ang_vel_body,
                joint_pos[action_indices],
                joint_vel[action_indices],
                [mode],
                cmd_vel,
            ]).astype(np.float32)

            actions = controller.update(obs)
            if actions is None:
                actions = default_positions[action_indices]

            target_positions = default_positions.copy()
            for i, idx in enumerate(action_indices):
                target_positions[idx] = actions[i]

            art_targets = np.zeros(num_dofs, dtype=np.float32)
            for i, art_idx in enumerate(cfg_to_art):
                art_targets[art_idx] = target_positions[i]
            art_controller.apply_action(ArticulationAction(joint_positions=art_targets))

            for _ in range(physics_substeps):
                world.step(render=False)
            world.render()

            elapsed = time.perf_counter() - step_start
            remaining = cfg.policy_dt - elapsed
            if remaining > 0:
                time.sleep(remaining)

    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        if has_gamepad:
            gamepad.stop()
        simulation_app.close()


if __name__ == "__main__":
    main()
