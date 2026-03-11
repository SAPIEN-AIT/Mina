# Mina / Berkeley Humanoid Lite — Project Summary

This document is a living reference. Update it every time a new script, function, or pipeline is successfully run.

---

## Successfully Run Pipelines

### 1. MuJoCo Sim2Sim (`scripts/sim2sim/play_mujoco.py`)

**Command:**
```bash
uv run ./scripts/sim2sim/play_mujoco.py --config ./configs/policy_humanoid.yaml
```

**What it does:**
Runs a trained RL policy inside MuJoCo — the full sim2sim loop. The robot is simulated in MuJoCo physics, controlled by a pre-trained ONNX/PyTorch policy, and steered with a gamepad in real time.

**Control flow:**
```
Cfg.from_arguments()          # Load YAML config (policy path, joint params, frequencies)
  └─ MujocoSimulator(cfg)     # Load MJCF, init physics, launch viewer, start gamepad thread
       └─ robot.reset()       # Set default joint positions, return initial obs tensor
  └─ RlController(cfg)        # Init obs/action buffers, set default joint targets
       └─ controller.load_policy()  # Load .pt (TorchPolicy) or .onnx (OnnxPolicy)

loop:
  controller.update(obs)      # Build obs buffer, run policy forward pass, scale actions
  robot.step(actions)         # PD control × N substeps, sync viewer, sleep to real-time
```

**Setup fixes required:**
- `pyproject.toml` had a duplicate `flatdict` key (lines 152–153) → merged into one
- `flatdict==4.0.1` build failed (`pkg_resources` not on PyPI) → added `setuptools>=40.8.0` + `no-build-isolation-package = ["flatdict"]` in `pyproject.toml`
- `berkeley_humanoid_lite.xml` and `berkeley_humanoid_lite_biped.xml` were deleted from the assets submodule working tree → restored with `git checkout` inside `source/berkeley_humanoid_lite_assets/`
- MJCF expected meshes at `mjcf/assets/merged/*.stl` but actual meshes are at `meshes/*.stl` → created symlink: `mjcf/assets/merged/ → ../../meshes/`
- Gamepad (`/dev/input/event4`) not accessible → added user to `input` group: `sudo usermod -aG input $USER`, then `newgrp input`

**Config used:** `configs/policy_humanoid.yaml` (22-joint full humanoid)

**Gamepad mappings (Logitech F710):**

| Input | Command |
|---|---|
| Left stick Y | velocity_x (forward/back) |
| Right stick X | velocity_y (lateral) |
| Left stick X | velocity_yaw (turn) |
| A + Right Bumper | Mode 3: RL control |
| A + Left Bumper | Mode 2: Init |
| X / Left or Right Thumbstick | Mode 1: Idle |

---

## Key Classes & Functions

### `Cfg` — [source/berkeley_humanoid_lite_lowlevel/.../policy/config.py](source/berkeley_humanoid_lite_lowlevel/berkeley_humanoid_lite_lowlevel/policy/config.py)

Configuration container loaded from a YAML file.

| Method | Description |
|---|---|
| `Cfg.from_arguments()` | Parses `--config` CLI arg, loads YAML via OmegaConf, returns `DictConfig` |

Key fields: `policy_checkpoint_path`, `num_joints`, `num_actions`, `action_indices`, `action_scale`, `joint_kp/kd`, `effort_limits`, `policy_dt`, `physics_dt`, `history_length`, `num_observations`.

---

### `MujocoEnv` — [source/berkeley_humanoid_lite/.../environments/mujoco.py](source/berkeley_humanoid_lite/berkeley_humanoid_lite/environments/mujoco.py)

Base class. Loads the MJCF model (`bhl_scene.xml` for 22-joint or `bhl_biped_scene.xml` for 12-joint), creates `MjData`, sets physics timestep, and launches the passive MuJoCo viewer.

---

### `MujocoSimulator(MujocoEnv)` — [source/berkeley_humanoid_lite/.../environments/mujoco.py](source/berkeley_humanoid_lite/berkeley_humanoid_lite/environments/mujoco.py)

The main sim2sim environment. Runs the full physics + control loop.

| Method | Description |
|---|---|
| `__init__(cfg)` | Inits PD gains, effort limits, substep count, starts `Se2Gamepad` thread |
| `reset()` | Sets default qpos/qvel, returns initial obs tensor |
| `step(actions)` | Runs N physics substeps with PD control, syncs viewer, sleeps to real-time, returns obs |
| `_apply_actions(actions)` | Computes PD torques, clips to effort limits, writes to `mj_data.ctrl` |
| `_get_observations()` | Reads gamepad commands + sensor data, returns concatenated obs tensor: `[quat(4), ang_vel(3), joint_pos(N), joint_vel(N), mode+cmd_vel(4)]` |
| `_get_base_quat()` | Reads IMU quaternion `[w,x,y,z]` from `sensordata` |
| `_get_base_ang_vel()` | Reads angular velocity `[wx,wy,wz]` from `sensordata` |
| `_get_projected_gravity()` | Rotates `[0,0,-1]` into robot frame via inverse quaternion rotation |
| `_get_joint_pos()` | Reads joint positions from `sensordata[0:N]` |
| `_get_joint_vel()` | Reads joint velocities from `sensordata[N:2N]` |

---

### `MujocoVisualizer(MujocoEnv)` — [source/berkeley_humanoid_lite/.../environments/mujoco.py](source/berkeley_humanoid_lite/berkeley_humanoid_lite/environments/mujoco.py)

Lighter variant — replays real robot observations into MuJoCo for visualization (no policy, no PD control). Takes raw robot obs over UDP and drives the sim state directly.

---

### `RlController` — [source/berkeley_humanoid_lite_lowlevel/.../policy/rl_controller.py](source/berkeley_humanoid_lite_lowlevel/berkeley_humanoid_lite_lowlevel/policy/rl_controller.py)

Runs the trained policy. Works for both sim2sim and real deployment.

| Method | Description |
|---|---|
| `__init__(cfg)` | Inits obs/action buffers (with history), default joint positions |
| `load_policy()` | Loads `.pt` → `TorchPolicy` or `.onnx` → `OnnxPolicy` |
| `update(obs)` | Parses obs, builds sliding window obs buffer, runs forward pass, clips & scales actions |
| `quat_rotate_inverse(q, v)` | Static method: rotates vector `v` by inverse of quaternion `q` (numpy) |

**Observation buffer layout (per timestep, 75-dim for humanoid):**
```
command_velocity  [3]   (vx, vy, vyaw)
base_ang_vel      [3]
projected_gravity [3]
joint_pos         [N]   (relative to default)
joint_vel         [N]
prev_actions      [N]
```
Full buffer = `(history_length + 1) × num_observations` (sliding window).

---

### `TorchPolicy` / `OnnxPolicy` — [source/berkeley_humanoid_lite_lowlevel/.../policy/rl_controller.py](source/berkeley_humanoid_lite_lowlevel/berkeley_humanoid_lite_lowlevel/policy/rl_controller.py)

| Class | Backend | `forward(obs)` |
|---|---|---|
| `TorchPolicy` | `torch.load(.pt)` | `model(obs_tensor).numpy()` |
| `OnnxPolicy` | `onnxruntime.InferenceSession(.onnx)` | `session.run(None, {key: obs})` — auto-detects input key |

---

### `Se2Gamepad` — [source/berkeley_humanoid_lite_lowlevel/.../policy/gamepad.py](source/berkeley_humanoid_lite_lowlevel/berkeley_humanoid_lite_lowlevel/policy/gamepad.py)

Runs a background thread that continuously reads from the gamepad and populates `commands`.

| Method | Description |
|---|---|
| `__init__()` | Sets up `commands` dict, dead zone, sensitivity |
| `run()` | Starts background thread calling `run_forever()` |
| `advance()` | Reads one batch of gamepad events, updates `_states`, calls `_update_command_buffer()` |
| `_update_command_buffer()` | Maps raw axis values (±32768) to normalized velocity commands; sets `mode_switch` from button combos |
| `stop()` | Signals the thread to exit |

`commands` dict keys: `velocity_x`, `velocity_y`, `velocity_yaw`, `mode_switch`.

---

### `quat_rotate_inverse(q, v)` — [source/berkeley_humanoid_lite/.../environments/mujoco.py](source/berkeley_humanoid_lite/berkeley_humanoid_lite/environments/mujoco.py)

Utility function (torch version, also has numpy version in `RlController`). Rotates vector `v` into the frame defined by quaternion `q` (i.e., world → body frame). Used to project gravity into the robot's base frame.

```
a = v * (2·qw² - 1)
b = cross(q_vec, v) * 2·qw
c = q_vec * dot(q_vec, v) * 2
result = a - b + c
```

---

## Asset File Structure

```
source/berkeley_humanoid_lite_assets/
└── data/robots/berkeley_humanoid/berkeley_humanoid_lite/
    ├── mjcf/
    │   ├── bhl_scene.xml              # Top-level MJCF scene (22-joint humanoid)
    │   ├── bhl_biped_scene.xml        # Top-level MJCF scene (12-joint biped)
    │   ├── berkeley_humanoid_lite.xml # Robot model (joints, actuators, sensors)
    │   ├── berkeley_humanoid_lite_biped.xml
    │   └── assets/
    │       └── merged -> ../../meshes/  # Symlink created to fix mesh paths
    ├── meshes/                          # All .stl mesh files (flat directory)
    ├── urdf/
    └── usd/
```

---

## Configuration Files

| File | Robot | Joints |
|---|---|---|
| `configs/policy_humanoid.yaml` | Full humanoid | 22 |
| `configs/policy_biped.yaml` | Biped (legs only) | 12 |

Each YAML contains: policy checkpoint path, joint names, kp/kd/effort limits, default positions, obs/action dimensions, history length, physics/policy dt, network IP/port settings.

---

---

## Successfully Run Pipelines (continued)

### 2. Isaac Sim ROS2 Controller (`scripts/sim2sim/play_isaacsim.py`)

**Command:**
```bash
uv run ./scripts/sim2sim/play_isaacsim.py --config ./configs/policy_humanoid.yaml \
  [--joint-states-topic joint_states] \
  [--imu-topic imu] \
  [--cmd-vel-topic cmd_vel] \
  [--joint-command-topic joint_command]
```

**What it does:**
Runs the same trained RL policy as the MuJoCo sim2sim pipeline, but inside Isaac Sim via ROS2. Robot state arrives from Isaac Sim via ROS2 topics, velocity commands come from `/cmd_vel` (e.g. `teleop_twist_keyboard`), and joint position targets are published back to Isaac Sim.

**Control flow:**
```
Cfg loaded from YAML (same file as MuJoCo)
  └─ IsaacSimController(rclpy.Node)
       ├─ Subscribes: /joint_states + /imu → TimeSynchronizer → _sensor_callback()
       ├─ Subscribes: /cmd_vel → _cmd_vel_callback()
       ├─ RlController(cfg).load_policy()   # reused as-is from MuJoCo pipeline
       └─ 25 Hz timer → _policy_callback()
            ├─ _build_observation()         # 55-element vector (same layout as MuJoCo)
            ├─ controller.update(obs)       # identical RlController forward pass
            └─ Publishes /joint_command     # absolute joint position targets
```

**Observation vector (55 elements — identical to MuJoCo pipeline input to RlController):**
```
[0:4]   base_quat (w,x,y,z)     ← imu.orientation
[4:7]   base_ang_vel             ← imu.angular_velocity
[7:29]  joint_pos (22)           ← joint_states.position, reordered by cfg.joints
[29:51] joint_vel (22)           ← joint_states.velocity, same reordering
[51]    mode = 3.0               ← hardcoded RL_RUNNING
[52:55] [vx, vy*0.5, vyaw]      ← cmd_vel.linear.x/y, cmd_vel.angular.z
```

**Topic names are CLI-configurable** — check Isaac Sim's actual topic names with:
```bash
ros2 topic list | grep -E "joint|imu"
```

**Verification:**
```bash
ros2 topic echo /joint_command          # confirm publishing
ros2 topic hz /joint_command            # should be ~25 Hz
```

**Key difference from MuJoCo:** No gamepad, no physics, no PD control — all handled by Isaac Sim. Policy (`RlController`) is 100% reused unchanged.

---

## Environment Setup Notes

- **Python version:** 3.10 (enforced by `requires-python = ">=3.10,<3.11"`)
- **Package manager:** `uv` (workspace with `pyproject.toml`)
- **Run all scripts from the project root** (`/home/alex/dev/Mina`) — MJCF paths are relative to it
- **Gamepad access:** user must be in `input` group (`sudo usermod -aG input $USER`, then `newgrp input`)
- **`flatdict` build:** requires `no-build-isolation-package = ["flatdict"]` in `[tool.uv]`
