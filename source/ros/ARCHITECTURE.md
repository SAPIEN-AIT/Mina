# Isaac Sim ROS2 Policy Deployment

NVIDIA reference: https://docs.isaacsim.omniverse.nvidia.com/5.0.0/ros2_tutorials/tutorial_ros2_rl_controller.html

## Architecture

```
┌─────────────────────────────────┐     ┌──────────────────────────────────┐
│  play_isaacsim.py (external)    │     │  Isaac Sim                       │
│                                 │     │                                  │
│  /joint_states  ◄───────────────│─────│  OmniGraph (IsaacOnPhysicsStep): │
│  /imu           ◄───────────────│─────│    ROS_JointStates graph         │
│  /cmd_vel       ◄── gamepad     │     │    ROS_Imu graph                 │
│                                 │     │    ROS_Clock graph               │
│  ONNX policy (25 Hz)            │     │                                  │
│  RlController.update()          │     │  PhysX CPU @ 200 Hz:             │
│                                 │     │    Joint drives (PD internal)    │
│  /joint_command ────────────────│────►│    Independent of OmniGraph      │
└─────────────────────────────────┘     └──────────────────────────────────┘
```

**Key design points:**
- OmniGraph graphs trigger on `IsaacOnPhysicsStep` (not render tick) → decoupled from streaming
- CPU physics at 200 Hz → OmniGraph ticks ~20 Hz (enough for 25 Hz policy)
- PhysX joint drives run PD at every physics step regardless of ROS2 rate
- `play_isaacsim.py` is a pure ROS2 node — no `world.step()`, no render coupling

## How to run

### Step 1 — Start Isaac Sim

```bash
./docker_mina/run.sh stream          # WebRTC streaming (connect native client)
```

### Step 2 — Configure the scene

In the Isaac Sim Script Editor (Window → Script Editor):

```python
exec(open("/workspace/scripts/sim2sim/setup_isaacsim_ros2.py").read())
```

This configures physics (200 Hz, CPU, MBP), creates the OmniGraph action graphs,
and applies gain-scaled joint drives.

### Step 3 — Press Play in Isaac Sim

### Step 4 — Launch the policy node

```bash
./docker_mina/run.sh ros-policy configs/policy_humanoid.yaml
```

### Step 5 — Launch the gamepad

```bash
./docker_mina/run.sh ros-gamepad
```

Or run both at once (requires ROS2 in the host environment):

```bash
bash source/ros/launch_policy.sh configs/policy_humanoid.yaml
```

## Physics parameters

| Parameter       | Training | Deployment |
|-----------------|----------|------------|
| Physics rate    | 2000 Hz  | 200 Hz     |
| Physics backend | GPU      | CPU        |
| KP scale        | 1.0      | 0.3        |
| KD scale        | 1.0      | 0.7        |
| OmniGraph rate  | —        | ~20 Hz     |
| Policy rate     | 25 Hz    | 25 Hz      |

## Joint drive parameters (after gain scaling)

| Group       | Joints                              | kp (scaled) | kd (scaled) | armature |
|-------------|-------------------------------------|-------------|-------------|----------|
| Arms        | shoulder ×3, elbow ×2 (×2 sides)  | 3.0         | 1.4         | 0.002    |
| Hips+Knees  | hip ×3, knee (×2 sides)            | 6.0         | 1.4         | 0.007    |
| Ankles      | ankle_pitch, ankle_roll (×2 sides) | 6.0         | 1.4         | 0.002    |

## Topics

| Topic           | Direction          | Type                        |
|-----------------|--------------------|-----------------------------|
| `/joint_states` | Isaac Sim → policy | `sensor_msgs/JointState`    |
| `/imu`          | Isaac Sim → policy | `sensor_msgs/Imu`           |
| `/joint_command`| policy → Isaac Sim | `sensor_msgs/JointState`    |
| `/cmd_vel`      | gamepad → policy   | `geometry_msgs/Twist`       |
| `/clock`        | Isaac Sim → all    | `rosgraph_msgs/Clock`       |
