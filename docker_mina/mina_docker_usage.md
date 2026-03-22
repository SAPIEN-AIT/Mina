# Mina Docker — Usage Guide

> What to run, when to run it, and what each container is for.
>
> **All commands assume you run from the Mina project root:** `/home/alex/dev/Mina`

---

## The two containers

| Container name | Base image | What it's for |
|---|---|---|
| `mina-isaaclab-base` | `isaac-lab:2.3.2` | Daily development — edit code on host, run inside container |
| `mina-bhl-training` | `mina-isaaclab-base` | Headless RL training, streaming, eval — code baked in, logs/checkpoints come out |

Streaming visualization reuses `mina-bhl-training` with runtime flag overrides (`HEADLESS=0`, `LIVESTREAM=2`) — no separate image needed.

ROS2 policy inference and teleop use the external `mina_desktop:jazzy` image (see ROS2 section below).

---

## First-time setup (once only)

```bash
cd /home/alex/dev/Mina

# 1. Fill in your NGC API key and confirm paths
nano docker_mina/.env

# 2. Log into NGC  (username is literally "$oauthtoken", password is your API key)
#    Get your key at: https://ngc.nvidia.com → top-right menu → Setup → Generate API Key
./docker_mina/run.sh ngc-login

# 3. Pull the official NVIDIA base image (~15 GB, takes a while)
./docker_mina/run.sh pull-base

# 4. Build both images (base + training)
./docker_mina/run.sh build-all
```

After this you should not need `ngc-login` or `pull-base` again unless you upgrade versions.

---

## Available gym environment IDs

These are the registered task names you pass to `--task`:

| Task ID | Description |
|---|---|
| `Velocity-Berkeley-Humanoid-Lite-Biped-v0` | Biped (legs only) velocity tracking |
| `Velocity-Berkeley-Humanoid-Lite-v0` | Full humanoid velocity tracking |

The default task in `.env` is `Velocity-Berkeley-Humanoid-Lite-Biped-v0`.

---

## Everyday workflows

### Active development

Use this whenever you are writing or debugging task code, configs, or scripts.
Your files are live-synced — no rebuild needed when you edit.

**Note:** The dev container does not have `berkeley_humanoid_lite` pre-installed. You must install it on every fresh container start:

```bash
cd /home/alex/dev/Mina
./docker_mina/run.sh dev
# You are now inside the container at /workspace

# Install project packages (required on every fresh start)
/isaac-sim/python.sh -m pip install -e /workspace/source/berkeley_humanoid_lite_assets -q
/isaac-sim/python.sh -m pip install -e /workspace/source/berkeley_humanoid_lite -q

# Now run anything:
isaaclab -p scripts/rsl_rl/train.py --task Velocity-Berkeley-Humanoid-Lite-Biped-v0 --num_envs 64 --headless
isaaclab -p scripts/rsl_rl/play.py --task Velocity-Berkeley-Humanoid-Lite-Biped-v0 --num_envs 4 --headless
```

Exit with `Ctrl+D` or `exit`. The container is removed on exit (`--rm`), but your files on the host are untouched.

---

### Headless training

Use this for serious training runs. Code is baked into the image (not live-synced) so the run is fully reproducible. Logs and checkpoints come out to your host.

```bash
cd /home/alex/dev/Mina

# Default task and num_envs from .env
./docker_mina/run.sh train

# Override task and/or num_envs
./docker_mina/run.sh train Velocity-Berkeley-Humanoid-Lite-Biped-v0 8192

# Multi-GPU (add --distributed inside the container CMD, or exec in)
docker exec -it mina-bhl-training bash
```

#### Where outputs go

```
/home/alex/dev/Mina/logs/rsl_rl/<experiment>/
├── <timestamp>/
│   ├── model_0.pt, model_100.pt, ...    ← checkpoints
│   ├── params/
│   │   ├── env.yaml, agent.yaml          ← config snapshots
│   │   └── env.pkl, agent.pkl
│   ├── events.out.tfevents.*              ← TensorBoard logs
│   └── isaaclab/                          ← Isaac Lab internal logs
└── ...
```

Point TensorBoard at the logs dir: `tensorboard --logdir /home/alex/dev/Mina/logs/rsl_rl`

> **Rebuild before training** if you changed task code since the last build:
> ```bash
> ./docker_mina/run.sh build-training
> ./docker_mina/run.sh train
> ```

---

### Training with video recording

Add `--video` to record rollout videos during training. Also requires `ENABLE_CAMERAS=1`.

```bash
cd /home/alex/dev/Mina

# Via run.sh — override CMD to add --video flag
docker run --name mina-bhl-training --rm --gpus all --network host --ipc host \
  -e ACCEPT_EULA=Y -e PRIVACY_CONSENT=Y -e HEADLESS=1 -e ENABLE_CAMERAS=1 \
  -v "$PWD/logs:/workspace/logs:rw" \
  -v "$PWD/checkpoints:/workspace/checkpoints:rw" \
  -v "${HOME}/docker/isaac-sim/cache/kit:/isaac-sim/kit/cache:rw" \
  -v "${HOME}/docker/isaac-sim/cache/ov:/root/.cache/ov:rw" \
  -v "${HOME}/docker/isaac-sim/cache/pip:/root/.cache/pip:rw" \
  -v "${HOME}/docker/isaac-sim/cache/glcache:/root/.cache/nvidia/GLCache:rw" \
  -v "${HOME}/docker/isaac-sim/cache/computecache:/root/.nv/ComputeCache:rw" \
  -v "${HOME}/docker/isaac-sim/logs:/root/.nvidia-omniverse/logs:rw" \
  -v "${HOME}/docker/isaac-sim/data:/root/.local/share/ov/data:rw" \
  mina-bhl-training:latest \
  bash -c "isaaclab -p scripts/rsl_rl/train.py \
    --task Velocity-Berkeley-Humanoid-Lite-Biped-v0 \
    --num_envs 16 --headless --max_iterations 100 \
    --video --video_length 200 --video_interval 50"
```

**Important:** `ENABLE_CAMERAS=1` must be set for video recording — it activates the offscreen render pipeline.

#### Where video outputs go

```
/home/alex/dev/Mina/logs/rsl_rl/<experiment>/<timestamp>/videos/
├── train/
│   ├── rl-video-step-0.mp4
│   ├── rl-video-step-50.mp4
│   └── ...
```

---

### Playing / evaluating a checkpoint

The play script auto-loads the latest checkpoint from the latest training run.

```bash
cd /home/alex/dev/Mina

# Headless play — runs inference loop indefinitely (Ctrl+C to stop)
docker run --name mina-bhl-play --rm --gpus all --network host --ipc host \
  -e ACCEPT_EULA=Y -e PRIVACY_CONSENT=Y -e HEADLESS=1 -e ENABLE_CAMERAS=0 \
  -v "$PWD/logs:/workspace/logs:rw" \
  -v "$PWD/checkpoints:/workspace/checkpoints:rw" \
  -v "$PWD/configs:/workspace/configs:rw" \
  -v "${HOME}/docker/isaac-sim/cache/kit:/isaac-sim/kit/cache:rw" \
  -v "${HOME}/docker/isaac-sim/cache/ov:/root/.cache/ov:rw" \
  -v "${HOME}/docker/isaac-sim/cache/pip:/root/.cache/pip:rw" \
  -v "${HOME}/docker/isaac-sim/cache/glcache:/root/.cache/nvidia/GLCache:rw" \
  -v "${HOME}/docker/isaac-sim/cache/computecache:/root/.nv/ComputeCache:rw" \
  -v "${HOME}/docker/isaac-sim/logs:/root/.nvidia-omniverse/logs:rw" \
  -v "${HOME}/docker/isaac-sim/data:/root/.local/share/ov/data:rw" \
  mina-bhl-training:latest \
  bash -c "isaaclab -p scripts/rsl_rl/play.py \
    --task Velocity-Berkeley-Humanoid-Lite-Biped-v0 \
    --num_envs 4 --headless"

# Play with video recording (exits automatically after video_length steps)
# Same command as above but change ENABLE_CAMERAS=0 to ENABLE_CAMERAS=1
# and add --video --video_length 200 to the isaaclab command.
# See "Testing all containers" section below for full copy-paste commands.
```

#### Where play outputs go

```
/home/alex/dev/Mina/logs/rsl_rl/<experiment>/<timestamp>/
├── videos/play/
│   └── rl-video-step-0.mp4              ← recorded play video
├── exported/
│   ├── policy.pt                         ← JIT-exported policy
│   └── policy.onnx                       ← ONNX-exported policy
```

The play script also writes a deploy config to `/workspace/configs/policy_latest.yaml` (mount `configs/` to persist it to host).

#### Headless eval with video (via run.sh)

```bash
cd /home/alex/dev/Mina

# Auto-detects latest checkpoint
./docker_mina/run.sh eval Velocity-Berkeley-Humanoid-Lite-Biped-v0

# With a specific checkpoint
./docker_mina/run.sh eval Velocity-Berkeley-Humanoid-Lite-Biped-v0 model_3000.pt

# Videos saved to logs/rsl_rl/<experiment>/<timestamp>/videos/play/
```

---

### Visualising a policy (Streaming Client)

Use this to watch a trained checkpoint live with the native Isaac Sim Streaming Client.
Streaming reuses the `mina-bhl-training` image with runtime flag overrides — no separate image needed.

**Compatibility note:** for the IsaacLab version used by this repo, the browser WebRTC demo page does not work reliably. Use the native client instead.

```bash
cd /home/alex/dev/Mina

# Local streaming (auto-picks latest checkpoint)
./docker_mina/run.sh stream

# With a specific checkpoint
./docker_mina/run.sh stream model_3000.pt

# Then connect with the Isaac Sim Streaming Client to:
#   127.0.0.1
```

For remote cloud GPUs, set `PUBLIC_IP` in `.env` to your machine's public IP, then:

```bash
./docker_mina/run.sh stream
# Then connect with the Isaac Sim Streaming Client to:
#   <PUBLIC_IP>
```

The stream command automatically picks up the latest checkpoint from the latest training run if no checkpoint is specified.

---

### ROS2 policy inference and teleop

These commands use the `mina_desktop:jazzy` image (ROS2 Jazzy + Python 3.12). The image auto-installs the `berkeley_humanoid_lite_lowlevel` package on startup.

```bash
cd /home/alex/dev/Mina

# Run ROS2 policy node (default config: configs/policy_latest.yaml)
./docker_mina/run.sh ros-policy

# Run with custom config
./docker_mina/run.sh ros-policy /home/mina/Mina/configs/my_config.yaml

# Run gamepad → /cmd_vel bridge
./docker_mina/run.sh ros-gamepad

# Interactive shell in ROS2 container
./docker_mina/run.sh ros-shell
```

---

## Useful maintenance commands

```bash
cd /home/alex/dev/Mina

# See all mina images and running containers
./docker_mina/run.sh list

# Stop all running mina containers
./docker_mina/run.sh stop

# Remove all mina containers and images (keeps cache dirs on host)
./docker_mina/run.sh clean

# Rebuild a single image after code changes
./docker_mina/run.sh build-base
./docker_mina/run.sh build-training
```

---

## When to rebuild

| You changed... | Rebuild needed |
|---|---|
| A file under `source/` (task code) | Only if using training — not needed in dev |
| A file under `scripts/` | Same as above |
| `docker_mina/.env` paths or versions | Yes, `build-all` |
| A `Dockerfile.*` | Yes, that specific image |
| Nothing — just re-running a training | No |

**Rebuild order matters**: base → training (training depends on base).

---

## Quick reference card

```
FIRST TIME        ngc-login → pull-base → build-all

DAILY DEV         dev
TRAIN             train [task] [num_envs]
WATCH             stream [checkpoint]   (use Streaming Client, not browser demo)
EVAL + VIDEO      eval [task] [checkpoint]
ROS2 INFERENCE    ros-policy [config]
ROS2 TELEOP       ros-gamepad
ROS2 DEBUG        ros-shell

MAINTENANCE       list | stop | clean | build-<name>

All from:         cd /home/alex/dev/Mina && ./docker_mina/run.sh <command>
```

---

## Testing all containers (copy-paste commands)

Run these in order from `/home/alex/dev/Mina` to verify everything works.
Each command is self-contained — just paste and run.

### 1. Dev container — train with video

```bash
docker run --name mina-dev-train --rm --gpus all --network host --ipc host --entrypoint "" \
  -e ACCEPT_EULA=Y -e PRIVACY_CONSENT=Y -e HEADLESS=1 -e ENABLE_CAMERAS=1 \
  -v "$PWD/source:/workspace/source:rw" \
  -v "$PWD/scripts:/workspace/scripts:rw" \
  -v "$PWD/configs:/workspace/configs:rw" \
  -v "$PWD/logs:/workspace/logs:rw" \
  -v "$PWD/checkpoints:/workspace/checkpoints:rw" \
  -v "/home/alex/dev/IsaacLab/source:/isaac-lab/source:rw" \
  -v "${HOME}/docker/isaac-sim/cache/kit:/isaac-sim/kit/cache:rw" \
  -v "${HOME}/docker/isaac-sim/cache/ov:/root/.cache/ov:rw" \
  -v "${HOME}/docker/isaac-sim/cache/pip:/root/.cache/pip:rw" \
  -v "${HOME}/docker/isaac-sim/cache/glcache:/root/.cache/nvidia/GLCache:rw" \
  -v "${HOME}/docker/isaac-sim/cache/computecache:/root/.nv/ComputeCache:rw" \
  -v "${HOME}/docker/isaac-sim/logs:/root/.nvidia-omniverse/logs:rw" \
  -v "${HOME}/docker/isaac-sim/data:/root/.local/share/ov/data:rw" \
  mina-isaaclab-base:latest \
  bash -c "/isaac-sim/python.sh -m pip install -e /workspace/source/berkeley_humanoid_lite_assets --no-cache-dir -q && \
    /isaac-sim/python.sh -m pip install -e /workspace/source/berkeley_humanoid_lite --no-cache-dir -q && \
    isaaclab -p scripts/rsl_rl/train.py --task Velocity-Berkeley-Humanoid-Lite-Biped-v0 --num_envs 16 --headless --max_iterations 3 --video --video_length 10 --video_interval 1"
```

**Check:** `.mp4` files in `logs/rsl_rl/biped/<latest-timestamp>/videos/train/`

### 2. Dev container — play with video

```bash
docker run --name mina-dev-play --rm --gpus all --network host --ipc host --entrypoint "" \
  -e ACCEPT_EULA=Y -e PRIVACY_CONSENT=Y -e HEADLESS=1 -e ENABLE_CAMERAS=1 \
  -v "$PWD/source:/workspace/source:rw" \
  -v "$PWD/scripts:/workspace/scripts:rw" \
  -v "$PWD/configs:/workspace/configs:rw" \
  -v "$PWD/logs:/workspace/logs:rw" \
  -v "$PWD/checkpoints:/workspace/checkpoints:rw" \
  -v "/home/alex/dev/IsaacLab/source:/isaac-lab/source:rw" \
  -v "${HOME}/docker/isaac-sim/cache/kit:/isaac-sim/kit/cache:rw" \
  -v "${HOME}/docker/isaac-sim/cache/ov:/root/.cache/ov:rw" \
  -v "${HOME}/docker/isaac-sim/cache/pip:/root/.cache/pip:rw" \
  -v "${HOME}/docker/isaac-sim/cache/glcache:/root/.cache/nvidia/GLCache:rw" \
  -v "${HOME}/docker/isaac-sim/cache/computecache:/root/.nv/ComputeCache:rw" \
  -v "${HOME}/docker/isaac-sim/logs:/root/.nvidia-omniverse/logs:rw" \
  -v "${HOME}/docker/isaac-sim/data:/root/.local/share/ov/data:rw" \
  mina-isaaclab-base:latest \
  bash -c "/isaac-sim/python.sh -m pip install -e /workspace/source/berkeley_humanoid_lite_assets --no-cache-dir -q && \
    /isaac-sim/python.sh -m pip install -e /workspace/source/berkeley_humanoid_lite --no-cache-dir -q && \
    isaaclab -p scripts/rsl_rl/play.py --task Velocity-Berkeley-Humanoid-Lite-Biped-v0 --num_envs 4 --headless --video --video_length 20"
```

**Check:** `logs/rsl_rl/biped/<latest-timestamp>/videos/play/rl-video-step-0.mp4` and `exported/policy.{pt,onnx}`

### 3. Training container — train with video

```bash
docker run --name mina-training-train --rm --gpus all --network host --ipc host \
  -e ACCEPT_EULA=Y -e PRIVACY_CONSENT=Y -e HEADLESS=1 -e ENABLE_CAMERAS=1 \
  -v "$PWD/logs:/workspace/logs:rw" \
  -v "$PWD/checkpoints:/workspace/checkpoints:rw" \
  -v "${HOME}/docker/isaac-sim/cache/kit:/isaac-sim/kit/cache:rw" \
  -v "${HOME}/docker/isaac-sim/cache/ov:/root/.cache/ov:rw" \
  -v "${HOME}/docker/isaac-sim/cache/pip:/root/.cache/pip:rw" \
  -v "${HOME}/docker/isaac-sim/cache/glcache:/root/.cache/nvidia/GLCache:rw" \
  -v "${HOME}/docker/isaac-sim/cache/computecache:/root/.nv/ComputeCache:rw" \
  -v "${HOME}/docker/isaac-sim/logs:/root/.nvidia-omniverse/logs:rw" \
  -v "${HOME}/docker/isaac-sim/data:/root/.local/share/ov/data:rw" \
  mina-bhl-training:latest \
  bash -c "isaaclab -p scripts/rsl_rl/train.py --task Velocity-Berkeley-Humanoid-Lite-Biped-v0 --num_envs 16 --headless --max_iterations 3 --video --video_length 10 --video_interval 1"
```

**Check:** new timestamped dir in `logs/rsl_rl/biped/` with `model_*.pt` and `videos/train/*.mp4`

### 4. Training container — play with video

```bash
docker run --name mina-training-play --rm --gpus all --network host --ipc host \
  -e ACCEPT_EULA=Y -e PRIVACY_CONSENT=Y -e HEADLESS=1 -e ENABLE_CAMERAS=1 \
  -v "$PWD/logs:/workspace/logs:rw" \
  -v "$PWD/checkpoints:/workspace/checkpoints:rw" \
  -v "$PWD/configs:/workspace/configs:rw" \
  -v "${HOME}/docker/isaac-sim/cache/kit:/isaac-sim/kit/cache:rw" \
  -v "${HOME}/docker/isaac-sim/cache/ov:/root/.cache/ov:rw" \
  -v "${HOME}/docker/isaac-sim/cache/pip:/root/.cache/pip:rw" \
  -v "${HOME}/docker/isaac-sim/cache/glcache:/root/.cache/nvidia/GLCache:rw" \
  -v "${HOME}/docker/isaac-sim/cache/computecache:/root/.nv/ComputeCache:rw" \
  -v "${HOME}/docker/isaac-sim/logs:/root/.nvidia-omniverse/logs:rw" \
  -v "${HOME}/docker/isaac-sim/data:/root/.local/share/ov/data:rw" \
  mina-bhl-training:latest \
  bash -c "isaaclab -p scripts/rsl_rl/play.py --task Velocity-Berkeley-Humanoid-Lite-Biped-v0 --num_envs 4 --headless --video --video_length 20"
```

**Check:** `videos/play/rl-video-step-0.mp4` and `exported/policy.{pt,onnx}` in the latest run dir

### 5. Streaming via training image — play with livestream client

```bash
./docker_mina/run.sh stream
```

**Check:** open the Isaac Sim Streaming Client and connect to `127.0.0.1`. You should see the humanoid running. `Ctrl+C` to stop.

### 6. Final verification

```bash
echo "=== Train videos ===" && find logs/rsl_rl/biped -name "*.mp4" -path "*train*" | wc -l
echo "=== Play videos ===" && find logs/rsl_rl/biped -name "*.mp4" -path "*play*" | wc -l
echo "=== Exported policies ===" && find logs/rsl_rl/biped -name "policy.*" -path "*exported*"
echo "=== Checkpoints ===" && find logs/rsl_rl/biped -name "model_*.pt" | wc -l
```

**Expected:** train videos > 0, play videos > 0, exported `policy.pt` + `policy.onnx`, multiple `model_*.pt` checkpoints.

---

## Output locations summary

| What | Host path |
|---|---|
| Training checkpoints | `/home/alex/dev/Mina/logs/rsl_rl/<experiment>/<timestamp>/model_*.pt` |
| TensorBoard logs | `/home/alex/dev/Mina/logs/rsl_rl/<experiment>/<timestamp>/events.out.tfevents.*` |
| Training videos | `/home/alex/dev/Mina/logs/rsl_rl/<experiment>/<timestamp>/videos/train/` |
| Play/eval videos | `/home/alex/dev/Mina/logs/rsl_rl/<experiment>/<timestamp>/videos/play/` |
| Exported policies | `/home/alex/dev/Mina/logs/rsl_rl/<experiment>/<timestamp>/exported/policy.{pt,onnx}` |
| Deploy config | `/home/alex/dev/Mina/configs/policy_latest.yaml` |
| Isaac Sim cache | `~/docker/isaac-sim/cache/` |
| Omniverse logs | `~/docker/isaac-sim/logs/` |
