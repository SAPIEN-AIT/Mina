# Copyright (c) 2025, The Berkeley Humanoid Lite Project Developers.

"""
Isaac Sim scene setup — NVIDIA reference architecture.

Run this from the Isaac Sim Script Editor after opening the robot scene
(or let it load the sensor USD automatically if no robot is present).

What it does:
  1. PhysicsScene  — CPU physics at 200 Hz, MBP broadphase
  2. Joint drives  — gain-scaled PD (KP×0.3, KD×0.7) + armature per joint group
  3. OmniGraph     — three graphs triggered by IsaacOnPhysicsStep (not render tick):
       ROS_Imu         → publishes /imu at physics rate
       ROS_JointStates → publishes /joint_states, subscribes /joint_command
       ROS_Clock       → publishes /clock

After running this script press Play, then start the external policy node:
    ./docker_mina/run.sh ros-policy configs/policy_humanoid.yaml

References:
  https://docs.isaacsim.omniverse.nvidia.com/5.0.0/ros2_tutorials/tutorial_ros2_rl_controller.html
  learnings/1st_hurdle_sim2rossim.md
"""

import math

import omni.usd
import omni.graph.core as og
from pxr import UsdPhysics, PhysxSchema, Usd

# ── Paths ─────────────────────────────────────────────────────────────────────

# Sensor USD — includes IMU prim, physics config, and robot meshes.
# Adjust if running on host (replace /workspace/source → /home/alex/dev/Mina/source).
ROBOT_USD = (
    "/workspace/source/berkeley_humanoid_lite_assets/data/robots/"
    "berkeley_humanoid/berkeley_humanoid_lite/usd/configuration/"
    "berkeley_humanoid_lite_sensor.usd"
)

ROBOT_PRIM  = "/World/robot"
SCENE_PRIM  = "/World/PhysicsScene"

# ── Physics ───────────────────────────────────────────────────────────────────

PHYSICS_HZ = 200  # CPU can sustain this; OmniGraph ticks ~20 Hz at 200 Hz physics

# Gain scaling: 200 Hz deployment vs 2000 Hz training.
# KP_SCALE = target / training = 200/2000 = 0.1 → empirically 0.3 works better.
# KD_SCALE = sqrt(0.3) * 1.3 (extra damping for ROS2 feedback latency) ≈ 0.7
KP_SCALE = 0.3
KD_SCALE  = 0.7

# ── Joint parameters (from ImplicitActuatorCfg in training) ──────────────────
#
# Group        | joints                                   | kp   | kd  | effort | armature
# Arms         | shoulder_pitch/roll/yaw, elbow_pitch/roll| 10.0 | 2.0 | 4.0   | 0.002
# Hips+Knees   | hip_roll/yaw/pitch, knee_pitch           | 20.0 | 2.0 | 6.0   | 0.007
# Ankles       | ankle_pitch, ankle_roll                  | 20.0 | 2.0 | 6.0   | 0.002

# Default joint positions from policy_humanoid.yaml (radians, YAML order).
DEFAULT_POSITIONS_RAD = {
    # Arms — all zero
    "arm_left_shoulder_pitch_joint":  0.0,
    "arm_left_shoulder_roll_joint":   0.0,
    "arm_left_shoulder_yaw_joint":    0.0,
    "arm_left_elbow_pitch_joint":     0.0,
    "arm_left_elbow_roll_joint":      0.0,
    "arm_right_shoulder_pitch_joint": 0.0,
    "arm_right_shoulder_roll_joint":  0.0,
    "arm_right_shoulder_yaw_joint":   0.0,
    "arm_right_elbow_pitch_joint":    0.0,
    "arm_right_elbow_roll_joint":     0.0,
    # Left leg
    "leg_left_hip_roll_joint":        0.0,
    "leg_left_hip_yaw_joint":         0.0,
    "leg_left_hip_pitch_joint":      -0.2,
    "leg_left_knee_pitch_joint":      0.4,
    "leg_left_ankle_pitch_joint":    -0.3,
    "leg_left_ankle_roll_joint":      0.0,
    # Right leg
    "leg_right_hip_roll_joint":       0.0,
    "leg_right_hip_yaw_joint":        0.0,
    "leg_right_hip_pitch_joint":     -0.2,
    "leg_right_knee_pitch_joint":     0.4,
    "leg_right_ankle_pitch_joint":   -0.3,
    "leg_right_ankle_roll_joint":     0.0,
}


def _joint_params(joint_name: str):
    """Return (kp, kd, effort_limit, armature) for a joint based on its name."""
    n = joint_name.lower()
    if any(k in n for k in ("shoulder", "elbow")):
        return 10.0, 2.0, 4.0, 0.002   # arm
    if any(k in n for k in ("ankle",)):
        return 20.0, 2.0, 6.0, 0.002   # ankle
    return 20.0, 2.0, 6.0, 0.007       # hip / knee


# ── Step 1: Physics scene ─────────────────────────────────────────────────────

def setup_physics_scene(stage):
    prim = stage.GetPrimAtPath(SCENE_PRIM)
    if not prim.IsValid():
        UsdPhysics.Scene.Define(stage, SCENE_PRIM)
        prim = stage.GetPrimAtPath(SCENE_PRIM)

    physx = PhysxSchema.PhysxSceneAPI.Apply(prim)
    UsdPhysics.Scene(prim).CreateTimeStepsPerSecondAttr(PHYSICS_HZ)

    physx.CreateEnableGPUDynamicsAttr(False)        # CPU physics
    physx.CreateBroadphaseTypeAttr("MBP")           # Multi Box Pruning (CPU broadphase)
    physx.CreateEnableCCDAttr(True)                 # continuous collision detection

    print(f"[setup] PhysicsScene: {PHYSICS_HZ} Hz, CPU, MBP broadphase")


# ── Step 2: Joint drives ──────────────────────────────────────────────────────

def setup_joint_drives(stage):
    robot_prim = stage.GetPrimAtPath(ROBOT_PRIM)
    if not robot_prim.IsValid():
        print(f"[setup] ERROR: no robot prim at {ROBOT_PRIM}")
        return

    n = 0
    for prim in Usd.PrimRange(robot_prim):
        if prim.GetTypeName() != "PhysicsRevoluteJoint":
            continue

        joint_name = prim.GetName()
        kp, kd, effort, armature = _joint_params(joint_name)
        default_deg = math.degrees(DEFAULT_POSITIONS_RAD.get(joint_name, 0.0))

        # Position drive (force mode — PhysX runs PD at physics rate)
        drive = UsdPhysics.DriveAPI.Apply(prim, "angular")
        drive.CreateTypeAttr("force")
        drive.CreateStiffnessAttr(kp * KP_SCALE)
        drive.CreateDampingAttr(kd * KD_SCALE)
        drive.CreateMaxForceAttr(effort)
        drive.CreateTargetPositionAttr(default_deg)
        drive.CreateTargetVelocityAttr(0.0)

        # Armature — prevents high-freq oscillation under PD (not in URDF, must add)
        PhysxSchema.PhysxJointAPI.Apply(prim).CreateArmatureAttr(armature)

        # Initial pose — prevents collapse before first policy command arrives
        UsdPhysics.JointStateAPI.Apply(prim, "angular").CreatePositionAttr(default_deg)

        n += 1

    print(f"[setup] Drives on {n} joints (KP×{KP_SCALE}, KD×{KD_SCALE})")


# ── Step 3: OmniGraph ─────────────────────────────────────────────────────────

def _on_demand_stage():
    """Return the on-demand pipeline stage enum (physics-step triggered)."""
    try:
        return og.GraphPipelineStage.GRAPH_PIPELINE_STAGE_ON_DEMAND
    except AttributeError:
        # Fallback for older Isaac Sim builds
        return og.GraphPipelineStage.GRAPH_PIPELINE_STAGE_SIMULATION


def _find_imu_prim(stage):
    """Dynamically find the IsaacImuSensor prim path under the robot."""
    robot = stage.GetPrimAtPath(ROBOT_PRIM)
    if robot.IsValid():
        for prim in Usd.PrimRange(robot):
            if prim.GetTypeName() == "IsaacImuSensor":
                return str(prim.GetPath())
    return None


def create_graph_imu(stage):
    imu_path = _find_imu_prim(stage)
    if imu_path is None:
        print(f"[setup] WARNING: no IsaacImuSensor found under {ROBOT_PRIM} — skipping ROS_Imu")
        return

    keys = og.Controller.Keys
    og.Controller.edit(
        {
            "graph_path": "/World/ROS_Imu",
            "evaluator_name": "execution",
            "pipeline_stage": _on_demand_stage(),
        },
        {
            keys.CREATE_NODES: [
                ("OnPhysicsStep", "omni.isaac.core_nodes.IsaacOnPhysicsStep"),
                ("SimTime",       "omni.isaac.core_nodes.IsaacReadSimulationTime"),
                ("Context",       "omni.isaac.ros2_bridge.ROS2Context"),
                ("ReadIMU",       "omni.isaac.sensor.IsaacReadIMU"),
                ("PublishIMU",    "omni.isaac.ros2_bridge.ROS2PublishImu"),
            ],
            keys.SET_VALUES: [
                ("ReadIMU.inputs:imuPrim",                      [imu_path]),
                ("ReadIMU.inputs:readGravity",                  False),
                ("PublishIMU.inputs:topicName",                 "imu"),
                ("PublishIMU.inputs:frameId",                   "base"),
                ("PublishIMU.inputs:publishOrientation",        True),
                ("PublishIMU.inputs:publishAngularVelocity",    True),
                ("PublishIMU.inputs:publishLinearAcceleration", True),
            ],
            keys.CONNECT: [
                ("OnPhysicsStep.outputs:step",         "SimTime.inputs:execIn"),
                ("OnPhysicsStep.outputs:step",         "ReadIMU.inputs:execIn"),
                ("SimTime.outputs:simulationTime",     "PublishIMU.inputs:timeStamp"),
                ("Context.outputs:context",            "PublishIMU.inputs:context"),
                ("ReadIMU.outputs:execOut",            "PublishIMU.inputs:execIn"),
                ("ReadIMU.outputs:orientation",        "PublishIMU.inputs:orientation"),
                ("ReadIMU.outputs:angularVelocity",    "PublishIMU.inputs:angularVelocity"),
                ("ReadIMU.outputs:linearAcceleration", "PublishIMU.inputs:linearAcceleration"),
            ],
        },
    )
    print(f"[setup] ROS_Imu graph (imu: {imu_path})")


def create_graph_joint_states(stage):
    keys = og.Controller.Keys
    og.Controller.edit(
        {
            "graph_path": "/World/ROS_JointStates",
            "evaluator_name": "execution",
            "pipeline_stage": _on_demand_stage(),
        },
        {
            keys.CREATE_NODES: [
                ("OnPhysicsStep",    "omni.isaac.core_nodes.IsaacOnPhysicsStep"),
                ("SimTime",          "omni.isaac.core_nodes.IsaacReadSimulationTime"),
                ("Context",          "omni.isaac.ros2_bridge.ROS2Context"),
                ("PublishJoints",    "omni.isaac.ros2_bridge.ROS2PublishJointState"),
                ("SubscribeJoints",  "omni.isaac.ros2_bridge.ROS2SubscribeJointState"),
                ("ArtController",    "omni.isaac.core_nodes.IsaacArticulationController"),
            ],
            keys.SET_VALUES: [
                ("PublishJoints.inputs:topicName",     "joint_states"),
                ("PublishJoints.inputs:targetPrim",    [ROBOT_PRIM]),
                ("SubscribeJoints.inputs:topicName",   "joint_command"),
                ("ArtController.inputs:usePath",       True),
                ("ArtController.inputs:robotPath",     ROBOT_PRIM),
                ("ArtController.inputs:jointNames",    []),  # empty = all joints
            ],
            keys.CONNECT: [
                ("OnPhysicsStep.outputs:step",              "PublishJoints.inputs:execIn"),
                ("OnPhysicsStep.outputs:step",              "SubscribeJoints.inputs:execIn"),
                ("OnPhysicsStep.outputs:step",              "SimTime.inputs:execIn"),
                ("SimTime.outputs:simulationTime",          "PublishJoints.inputs:timeStamp"),
                ("Context.outputs:context",                 "PublishJoints.inputs:context"),
                ("Context.outputs:context",                 "SubscribeJoints.inputs:context"),
                ("SubscribeJoints.outputs:execOut",         "ArtController.inputs:execIn"),
                ("SubscribeJoints.outputs:positionCommand", "ArtController.inputs:positionCommand"),
            ],
        },
    )
    print("[setup] ROS_JointStates graph")


def create_graph_clock(stage):
    keys = og.Controller.Keys
    og.Controller.edit(
        {
            "graph_path": "/World/ROS_Clock",
            "evaluator_name": "execution",
            "pipeline_stage": _on_demand_stage(),
        },
        {
            keys.CREATE_NODES: [
                ("OnPhysicsStep", "omni.isaac.core_nodes.IsaacOnPhysicsStep"),
                ("SimTime",       "omni.isaac.core_nodes.IsaacReadSimulationTime"),
                ("Context",       "omni.isaac.ros2_bridge.ROS2Context"),
                ("PublishClock",  "omni.isaac.ros2_bridge.ROS2PublishClock"),
            ],
            keys.CONNECT: [
                ("OnPhysicsStep.outputs:step",     "SimTime.inputs:execIn"),
                ("SimTime.outputs:execOut",        "PublishClock.inputs:execIn"),
                ("SimTime.outputs:simulationTime", "PublishClock.inputs:timeStamp"),
                ("Context.outputs:context",        "PublishClock.inputs:context"),
            ],
        },
    )
    print("[setup] ROS_Clock graph")


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    ctx = omni.usd.get_context()
    stage = ctx.get_stage()

    if stage is None:
        print("[setup] No open stage — open a scene first (File → Open).")
        return

    # Load robot USD if prim is missing (e.g. empty stage)
    robot_prim = stage.GetPrimAtPath(ROBOT_PRIM)
    if not robot_prim.IsValid():
        print(f"[setup] Robot prim missing — adding reference from:\n  {ROBOT_USD}")
        robot_xform = stage.DefinePrim(ROBOT_PRIM, "Xform")
        robot_xform.GetReferences().AddReference(ROBOT_USD)
        import omni.kit.app
        for _ in range(5):          # let stage settle
            omni.kit.app.get_app().update()
        print("[setup] Robot prim added.")

    setup_physics_scene(stage)
    setup_joint_drives(stage)
    create_graph_imu(stage)
    create_graph_joint_states(stage)
    create_graph_clock(stage)

    print()
    print("[setup] ✓ Scene configured. Press Play in Isaac Sim.")
    print("[setup]   Then: ./docker_mina/run.sh ros-policy configs/policy_humanoid.yaml")
    print("[setup]   And:  ./docker_mina/run.sh ros-gamepad")


main()
