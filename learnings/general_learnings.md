# General Learnings

## Robotics Master Pipeline Diagram

```text
================================================================================
                          THE ROBOTICS MASTER PIPELINE
================================================================================

[ THE HOST HARDWARE ] (The Physical Building)
  ┌──────────────────────────────────────────────────────────────────┐
  │  [CPU] (Master Electrician) <────> [RAM] (Counter Space)         │
  │    │                                                             │
  │  (PCIe Bus / The High-Speed Tunnel)                              │
  │    │                                                             │
  │  [NVIDIA GPU] (The Massive Industrial Oven)                      │
  └────┼─────────────────────────────────────────────────────────────┘
       │
[ THE HOST OS & PLUMBING ] (The Building Manager)
  ┌────┼─────────────────────────────────────────────────────────────┐
  │  [Linux Kernel] (Allocates CPU/RAM to everything)                │
  │  [NVIDIA Driver] (The translator manual for the GPU)             │
  │    │                                                             │
  │  [NVIDIA Container Toolkit] (The specialized GPU passthrough)    │
  └────┼─────────────────────────────────────────────────────────────┘
       │
       ▼  (The Toolkit safely bridges the GPU through the Docker wall)
       │
[ THE DOCKER CONTAINER ] (The Prefabricated Ghost Kitchen)
  ╔══════════════════════════════════════════════════════════════════╗
  ║  [ Base Ubuntu OS ] (The completely isolated room)               ║
  ║                                                                  ║
  ║    ┌────────────────────────────────────────────────────────┐    ║
  ║    │ [ Isaac Sim ] (The Reality Simulator / Holodeck)       │    ║
  ║    │   ├─ PhysX: Calculates gravity, friction, joint torque │    ║
  ║    │   └─ Shaders: Bounces light using the GPU cache        │    ║
  ║    └─▲──────────────────────────────────────────────────────┘    ║
  ║      │                                                           ║
  ║    ┌─▼──────────────────────────────────────────────────────┐    ║
  ║    │ [ Isaac Lab ] (The Robot Training Academy)             │    ║
  ║    │   ├─ Cloner: Spawns 4,000 identical robots             │    ║
  ║    │   └─ Gym: Hands out +1 points for walking, -1 for falls│    ║
  ║    └─▲──────────────────────────────────────────────────────┘    ║
  ║      │                                                           ║
  ║    ┌─▼──────────────────────────────────────────────────────┐    ║
  ║    │ [ Virtual Environment (.venv via `uv`) ]               │    ║
  ║    │   └─ [ rsl_rl / train.py ]: The AI Brain looking at    │    ║
  ║    │      the scores and updating the robot's reflexes!     │    ║
  ║    └────────────────────────────────────────────────────────┘    ║
  ╚══════════════════════════════════════════════════════════════════╝
                                  │
                                  ▼ (After 10 hours of massive GPU math...)
                                  
[ THE DEPLOYMENT ] 
  ┌──────────────────────────────────────────────────────────────────┐
  │ Export the trained `.onnx` brain file and flash it onto the      │
  │ physical robot's onboard computer to walk in the real world!     │
  └──────────────────────────────────────────────────────────────────┘
```

## Isaac Lab Training Setup: Hurdles and Fixes

### 1. Bash History Expansion

- Symptom: `bash: !': event not found`
- Cause: `!` is treated specially by bash history expansion inside double quotes.
- Fix: use single quotes around inline commands that contain `!`.

### 2. Isaac Lab IO API Change (`dump_pickle`)

- Symptom: `ImportError` for `dump_pickle` from `isaaclab.utils.io`.
- Cause: newer Isaac Lab versions changed or removed helper APIs.
- Applied fix: `scripts/rsl_rl/train.py` now defines a local `dump_pickle` using `pickle`, while keeping `dump_yaml`.

### 3. Docker Permission Issues for Internal Logs

- Symptom: permission denied when simulator writes internal logs.
- Cause: default internal log targets can be problematic in containerized runs.
- Applied fix: redirect simulator internal logs with `env_cfg.sim.log_dir` into project logs under `logs/rsl_rl/.../isaaclab`.

### 4. Module Discovery and Python Version Friction

- Symptoms: `ModuleNotFoundError: berkeley_humanoid_lite`, Python requirement mismatch.
- Cause: extension not installed in the active env and environment versions differ.
- Applied/available fix:

```bash
pip install -e . --ignore-requires-python
```

### 5. Omniverse License Acceptance

- Symptom: training startup blocked by EULA checks.
- Applied fix: run with `OMNI_KIT_ACCEPT_EULA=Y` and `ACCEPT_EULA=Y`.

```bash
OMNI_KIT_ACCEPT_EULA=Y ACCEPT_EULA=Y UV_PROJECT_ENVIRONMENT=.venv \
uv run ./scripts/rsl_rl/train.py --task Velocity-Berkeley-Humanoid-Lite-v0 --headless --max_iterations 1
```

### Confirmed Task IDs

- `Velocity-Berkeley-Humanoid-Lite-v0`
- `Velocity-Berkeley-Humanoid-Lite-Biped-v0`
