# 1st Hurdle: Isaac Sim ↔ ROS2 Sim-to-Sim Bridge

## Berkeley Humanoid Lite — From Training to Isaac Sim Standalone

---

## Executive Summary

This document chronicles the process of deploying a trained RL locomotion policy for the Berkeley Humanoid Lite (22-DOF humanoid) from Isaac Lab training into Isaac Sim standalone via a ROS2 bridge. The goal was to create a sim-to-sim pipeline that mirrors the real robot deployment architecture: an external ROS2 node runs the policy and publishes joint commands, while Isaac Sim provides physics simulation — identical to how the Jetson + CAN bus stack will work on the real robot.

The journey involved solving a cascade of interconnected problems: URDF import issues, OmniGraph Action Graph setup, physics parameter mismatches, a critical OmniGraph timing bottleneck, gain scaling, and observation pipeline debugging. The final working architecture uses PhysX internal joint drives for PD control at physics rate, with the external policy node publishing position targets at 25 Hz via ROS2.

---

## Starting Point

**What we had:**

- A trained RL policy checkpoint (`policy_humanoid.onnx`) produced by Isaac Lab
- Training used `ImplicitActuatorCfg` — PhysX joint drives with stiffness/damping, running PD internally at 2000 Hz physics rate
- A working MuJoCo sim-to-sim pipeline (`play_mujoco.py`) that loaded the same ONNX and ran perfectly with an Xbox gamepad
- Robot description files: URDF, MJCF (with armature values), and USD sensor configuration
- The deployment config (`policy_humanoid.yaml`) with all gains, joint ordering, and timing parameters

**What we wanted:**

An Isaac Sim standalone scene with the robot, a ROS2 bridge publishing sensor data and receiving joint commands, and an external Python node (`play_isaacsim.py`) running the ONNX policy — architecturally identical to real hardware deployment.

---

## Phase 1: Scene Setup and URDF Import

### Importing the Robot

The Berkeley Humanoid Lite robot was imported into Isaac Sim from its URDF file. The URDF import process creates an articulation with all links, joints, and collision meshes. However, several critical issues emerged immediately.

**Problem: `root_joint` FixedJoint.** The URDF importer creates a `PhysicsFixedJoint` called `root_joint` that anchors the robot's base to the world origin. This makes the robot float in place, completely immune to gravity. The fix was to delete this joint from the stage so the robot becomes a free-floating articulation subject to gravity.

**Problem: No armature in URDF.** The MJCF file includes `armature=0.005` on all joints, which adds rotational inertia to each joint and prevents high-frequency oscillation under PD control. The URDF format has no equivalent field. Without armature, the joints are "too light" and PD torques cause violent oscillation. This required manually adding `PhysxJointAPI.CreateArmatureAttr()` to every joint.

**Problem: No initial joint positions.** The URDF import starts all joints at 0 radians, but the robot's default standing pose has bent knees (hip pitch: -0.2, knee pitch: 0.4, ankle pitch: -0.3). Without setting initial positions via `JointStateAPI`, the robot spawns in a straight-legged pose and immediately collapses before any controller can act.

### Building the Scene

The complete Isaac Sim scene required:

- The robot articulation (from URDF or sensor USD)
- A ground plane with collision enabled
- A `PhysicsScene` with configurable timestep
- An environment with lighting
- The robot positioned slightly above the ground (z=0.05) to prevent initial ground penetration

### Setting Up the Action Graph

An OmniGraph Action Graph was created to bridge Isaac Sim's physics with ROS2. The graph consists of:

- **OnPlaybackTick** — fires every simulation tick (gated by render/physics rate)
- **ROS2 Publish Joint State** → publishes joint positions and velocities to `/joint_states` at the OmniGraph tick rate
- **Isaac Read IMU → ROS2 Publish Imu** → reads the IMU sensor prim and publishes orientation and angular velocity to `/imu`
- **ROS2 Subscribe Joint State** → receives commands from `/joint_command`
- **Isaac Articulation Controller** → applies received position/effort commands to the robot articulation

The IMU sensor prim path needed to be discovered dynamically (searching for `IsaacImuSensor` type in the stage) rather than hardcoded, since the prim path varies depending on how the USD is loaded.

A setup script (`setup_isaacsim_ros2.py`) was written to programmatically create this entire scene from the sensor USD. A key lesson: the script must wait for both the USD stage AND all extensions (OmniGraph, Physics commands) to fully initialize before attempting to create nodes. The stage object appears almost immediately, but extensions take many more frames to load. Attempting to create the Action Graph before the OmniGraph simulation pipeline is ready causes a `GRAPH_PIPELINE_STAGE_SIMULATION` error.

---

## Phase 2: The External PD Control Architecture (Failed Approach)

### Original Design

The initial architecture mirrored MuJoCo exactly:

1. `play_isaacsim.py` subscribes to `/joint_states` and `/imu`
2. ONNX policy runs at 25 Hz, outputs target joint positions
3. A PD control loop runs at 250 Hz, computing `torque = kp * (target - pos) + kd * (0 - vel)`
4. Torques are published to `/joint_command` in the `effort` field
5. In the USD, joint drives have `stiffness=0, damping=0` so the `effort` field is interpreted as raw torque
6. The ArticulationController in the Action Graph applies these torques to the joints

### The Problems

This architecture worked in MuJoCo because the control loop is synchronous — action goes in, physics steps, new state comes out, all in one call. In the Isaac Sim + ROS2 architecture, every step is asynchronous and gated by OmniGraph tick rate.

**Bugs fixed along the way:**

- `rclpy.spin_once()` deadlock: calling `spin_once()` inside a timer callback causes the node to freeze after the first tick. Fixed by using `rclpy.spin()` with separate timer callbacks.
- Joint order mismatch: Isaac Sim publishes joints in a different order than the YAML config. A remapping system was built that learns the robot's joint order from the first `/joint_states` message and applies bidirectional index mapping.
- Position commands vs torques: the original approach published position targets and relied on Isaac Sim's internal PD. But the internal PD gains were unknown/wrong. Switched to computing PD torques externally and publishing via the effort field.

### Why It Still Failed: The OmniGraph Bottleneck

Despite fixing all the above bugs, the robot would stand briefly then collapse. A diagnostic tool was built to measure the actual data rates in the system. The results were devastating:

| Physics Rate | OmniGraph Tick Rate | Sensor Update Rate |
|---|---|---|
| 2000 Hz | ~6 Hz | ~6 Hz |
| 500 Hz | ~18 Hz | ~18 Hz |
| 200 Hz | ~20 Hz | ~20 Hz |
| 60 Hz | ~43 Hz | ~43 Hz |

At the training physics rate of 2000 Hz, the OmniGraph only ticks at **6 Hz**. The laptop's GPU/CPU spends all its time computing physics substeps (~333 substeps per render frame at 2000 Hz / 6 fps), leaving almost no budget for OmniGraph evaluation. This means:

- Sensor data (`/joint_states`, `/imu`) only updates 6 times per second
- The `ROS2SubscribeJointState` node only reads incoming commands 6 times per second
- The ArticulationController only applies torques 6 times per second
- The external 250 Hz PD loop is publishing torques that are being **silently decimated** to 6 Hz application rate

A PD controller tuned for 2000 Hz that only fires at 6 Hz is catastrophically underdamped. The robot is essentially in open-loop freefall for ~170 ms between torque applications. No gain tuning can fix this — the architecture is fundamentally incompatible with the hardware constraints.

---

## Phase 3: PhysX Internal Drives (The Solution)

### The Insight

The key realization: PhysX joint drives with nonzero `stiffness` and `damping` compute PD forces **internally at every physics substep**, regardless of OmniGraph tick rate. If we set the drive properties to match the training gains and publish position targets instead of torques, PhysX does the PD at 2000 Hz while position targets only need to arrive at the policy rate (25 Hz).

This is architecturally identical to how MuJoCo works: `data.ctrl[:]` sets position targets, and the internal integrator applies PD forces at every `mj_step()`. The OmniGraph bottleneck becomes irrelevant — the drives are active every physics step, and the 6 Hz bridge rate only affects how quickly new targets arrive, not how quickly the PD runs.

### Confirming the Approach

A quick test: set `stiffness=kp` and `damping=kd` on all joint drives via the Script Editor, set target positions to the default pose, hit Play with no external node. The robot stood indefinitely — the internal PD worked. This was the proof of concept.

### Finding the Exact Training Parameters

Investigation of the Isaac Lab training code revealed that the robot uses `ImplicitActuatorCfg`, which means the training stiffness/damping values are set directly on PhysX drives — they're not external PD gains. The exact per-joint-group parameters from the training configuration:

| Joint Group | Stiffness (kp) | Damping (kd) | Effort Limit | Armature |
|---|---|---|---|---|
| Arms (10 joints) | 10.0 Nm/rad | 2.0 Nm·s/rad | 4.0 Nm | 0.002 |
| Legs — hips & knees (8 joints) | 20.0 Nm/rad | 2.0 Nm·s/rad | 6.0 Nm | 0.007 |
| Ankles (4 joints) | 20.0 Nm/rad | 2.0 Nm·s/rad | 6.0 Nm | 0.002 |

Note that armature values differ per group — the MJCF's uniform `armature=0.005` was an approximation that contributed to instability.

### Gain Scaling for Lower Physics Rates

The exact training gains work perfectly at 2000 Hz physics but cause oscillation at lower physics rates. Since the laptop cannot sustain 2000 Hz without choking OmniGraph to ~6 Hz, a compromise was needed.

At 200 Hz physics (which gives ~20 Hz OmniGraph — enough for the 25 Hz policy), the gains needed scaling to account for the larger timestep. The impulse-matching formula:

```
KP_SCALE = target_physics_hz / training_physics_hz = 200 / 2000 = 0.3
KD_SCALE = sqrt(KP_SCALE) * damping_boost = 0.55 * 1.3 ≈ 0.7
```

The damping boost (1.3x) compensates for the inherent ROS2 feedback delay — the policy sees sensor state that is ~50 ms old, causing slight overshoot that extra damping suppresses.

### Baking the Gains into the USD

The final joint drive configuration is applied via a Script Editor script (or `setup_isaacsim_ros2.py`) before running the policy. For each `PhysicsRevoluteJoint` in the stage:

```python
drive.CreateTypeAttr("force")
drive.CreateStiffnessAttr(kp * KP_SCALE)    # e.g., 20.0 * 0.3 = 6.0 for legs
drive.CreateDampingAttr(kd * KD_SCALE)       # e.g., 2.0 * 0.7 = 1.4 for legs
drive.CreateMaxForceAttr(effort_limit)        # 4.0 or 6.0
drive.CreateTargetPositionAttr(default_deg)   # default pose in degrees
drive.CreateTargetVelocityAttr(0.0)

physx_joint = PhysxSchema.PhysxJointAPI.Apply(prim)
physx_joint.CreateArmatureAttr(armature)      # 0.002 or 0.007 per group
```

---

## Phase 4: Cleaning Up the Policy Node

### Problems with the Original `play_isaacsim.py`

Even after switching to PhysX internal drives, the policy initially caused progressive wobbling that worsened until the robot fell. Two features in the code were corrupting the policy's internal state:

**Static joint detection** — the code tracked joints that appeared stationary (position near default, velocity near zero) and zeroed their `prev_actions` in the RlController. But `prev_actions` is part of the 75-element observation vector fed to the ONNX model. Zeroing them told the policy "your last commands weren't executed," causing it to output increasingly large compensating actions that diverged exponentially.

**Raw action clipping** — `prev_actions` was clipped to ±5.0 before feeding back into the observation. This doesn't exist in MuJoCo training. Any time the policy outputs large values (which can happen transiently), the clipped feedback diverges from the true state and corrupts subsequent policy outputs.

### The Clean Version

The final `play_isaacsim.py` is minimal — it does exactly what `play_mujoco.py` does:

1. Read sensors (joint positions, velocities, IMU orientation, angular velocity)
2. Build 55-element observation vector
3. Pass to `RlController.update()` which transforms it into 75 elements (adds projected gravity, subtracts defaults, appends prev_actions) and runs ONNX inference
4. Publish resulting position targets to `/joint_command`

No static joint detection. No action clipping. No warmup hacks. No velocity validation. The policy works because it's receiving the same data pipeline it was trained with.

---

## Phase 5: Teleop Integration

### Keyboard Teleop

The standard ROS2 `teleop_twist_keyboard` package works out of the box. It publishes `geometry_msgs/Twist` to `/cmd_vel`, which `play_isaacsim.py` reads and incorporates into the observation vector. The policy responds to forward/backward, lateral, and yaw commands.

### Gamepad Teleop

The policy was trained with Xbox controller input via the `Se2Gamepad` class, which provides analog stick values in [-1, 1] range. A small ROS2 bridge node reads the gamepad and publishes `Twist` messages to `/cmd_vel`, preserving the exact value range the policy expects.

---

## Final Architecture

```
┌─────────────────────────────────┐     ┌──────────────────────────────┐
│  play_isaacsim.py (external)    │     │  Isaac Sim                   │
│                                 │     │                              │
│  Subscribe:                     │     │  OmniGraph (~20 Hz):         │
│    /joint_states → positions    │◄────│    ROS2PublishJointState      │
│    /imu → orientation, ang_vel  │◄────│    IsaacReadIMU → PublishImu  │
│    /cmd_vel → velocity commands │     │                              │
│                                 │     │  ROS2SubscribeJointState     │
│  ONNX Policy (25 Hz):          │     │    → ArticulationController  │
│    obs → RlController.update()  │     │    → updates drive targets   │
│    → position targets           │────►│                              │
│                                 │     │  PhysX (200 Hz):             │
│  Publish:                       │     │    Joint drives (kp/kd)      │
│    /joint_command (positions)   │     │    PD at EVERY physics step  │
│                                 │     │    Independent of OmniGraph  │
└─────────────────────────────────┘     └──────────────────────────────┘
```

---

## Key Lessons Learned

**1. OmniGraph ticks at render rate, not physics rate.** This is the single most important insight. Any control loop that depends on OmniGraph (including ROS2 subscribe/publish) is gated by the render frame budget. At high physics rates, this budget is consumed by substep computation, leaving OmniGraph starved. Do not build external high-frequency control loops over OmniGraph.

**2. Use PhysX internal drives for PD control.** If the training uses `ImplicitActuatorCfg`, the stiffness/damping values go directly onto the PhysX drives. Position targets arrive at whatever rate the bridge supports; PD runs at physics rate internally. This decouples control stability from bridge bandwidth.

**3. URDF imports are incomplete.** URDF lacks armature, initial joint positions, and correct drive properties. These must be added programmatically after import. The MJCF file is a better reference for physics parameters but can't be directly imported into Isaac Sim.

**4. Don't add compensations that weren't in training.** Static joint detection, action clipping, gain hacks, warmup sequences — if MuJoCo's working pipeline doesn't have it, the Isaac Sim pipeline shouldn't either. Every modification to the observation or action pipeline is a potential source of divergence from training.

**5. Gain scaling is necessary at non-training physics rates.** The impulse-matching formula `kp_scaled = kp * (target_hz / training_hz)` provides a reasonable starting point. Additional damping (~30% boost) compensates for the inherent ROS2 feedback delay.

**6. Per-joint-group parameters matter.** Armature, stiffness, damping, and effort limits can vary per joint group. Using uniform values (even from the MJCF) causes subtle dynamics mismatches that prevent walking even when standing works.

**7. Verify actual data rates empirically.** Build diagnostics that measure real callback rates and sensor update rates, not theoretical rates. The gap between "timer fires at 250 Hz" and "data changes at 6 Hz" was the root cause of the entire instability problem.

---

## Files Reference

```
scripts/sim2sim/play_isaacsim.py       # Main ROS2 policy node (clean version)
scripts/sim2sim/play_mujoco.py         # Working MuJoCo reference
scripts/sim2sim/setup_isaacsim_ros2.py # Programmatic scene setup from sensor USD
scripts/sim2sim/set_joint_drives.py    # Standalone drive configuration script
scripts/sim2sim/gamepad_teleop.py      # Xbox controller → /cmd_vel bridge
scripts/sim2sim/diagnostics_patch.py   # OmniGraph timing diagnostic tool
configs/policy_humanoid.yaml           # Deployment config (gains, joints, timing)
configs/policy_latest.yaml             # Training config
checkpoints/policy_humanoid.onnx       # Policy checkpoint
```

---

## Next Steps

1. **Real robot deployment** — the ROS2 architecture is already correct for hardware. `play_isaacsim.py` publishes to `/joint_command`; the real robot's CAN bus driver subscribes. The 1 kHz hardware PD is closer to the 2000 Hz training rate than the 200 Hz Isaac Sim compromise.

2. **Programmatic scene setup** — bake all fixes into `setup_isaacsim_ros2.py` so the scene can be reproduced from the sensor USD without manual patching.

3. **Higher physics rate** — with a more powerful GPU, 2000 Hz physics becomes feasible with acceptable OmniGraph rates, eliminating the need for gain scaling entirely.
