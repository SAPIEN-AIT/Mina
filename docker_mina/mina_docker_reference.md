# Mina Docker — Technical Reference

> How everything is wired up under the hood: image layers, volumes, env vars, compose mechanics.

---

## Image hierarchy

Every image builds on the one above it. You never modify NVIDIA's images.

```
nvcr.io/nvidia/isaac-sim:4.5.0          ← NVIDIA (never touch)
         │
         ├─── nvcr.io/nvidia/isaac-lab:2.3.2    ← NVIDIA (never touch)
         │              │
         │              └─── mina-isaaclab-base  ← Dockerfile.base
         │                           │             adds: git, wget, vim
         │                           │             adds: isaaclab CLI wrapper in PATH
         │                           │
         │                           └─── mina-bhl-training   ← Dockerfile.training
         │                                        │             installs: berkeley_humanoid_lite_assets
         │                                        │             installs: berkeley_humanoid_lite
         │                                        │             bakes: source/, scripts/, configs/
         │                                        │
         │                                        └─── mina-bhl-streaming  ← Dockerfile.streaming
         │                                                      adds: port EXPOSEs
         │                                                      overrides: ENV LIVESTREAM=2
         │
         └─── mina-isaacsim-synthdata    ← Dockerfile.synthdata
                                           skips Lab entirely
                                           adds: ENABLE_CAMERAS=1

pytorch/pytorch:2.3.0-cuda12.1          ← separate base, no Isaac Sim
         │
         └─── mina-bhl-deploy            ← Dockerfile.deploy
                                           adds: deploy/ scripts + model.pt
```

**Why `mina-bhl-streaming` builds FROM `mina-bhl-training`** and not from base: the streaming image needs your package already installed to run inference. Building from training means you don't duplicate the `pip install` step.

**Why `mina-isaacsim-synthdata` builds FROM `isaac-sim:4.5.0` directly** and not from the Lab image: Isaac Lab loads the full RL stack (managers, environments, gym registration) at import time. For data generation you only need Isaac Sim's rendering and USD APIs — skipping Lab saves ~2 GB of image size and several seconds of startup.

---

## Dockerfiles explained

### `Dockerfile.base`

```dockerfile
FROM nvcr.io/nvidia/isaac-lab:2.3.2
ENV ACCEPT_EULA=Y
ENV PRIVACY_CONSENT=Y
RUN apt-get update && apt-get install -y git wget vim ...

# Make isaaclab CLI available on PATH
ENV ISAACLAB_PATH=/workspace/isaaclab
RUN printf '#!/usr/bin/env bash\nexec /workspace/isaaclab/isaaclab.sh "$@"\n' \
    > /usr/local/bin/isaaclab && chmod +x /usr/local/bin/isaaclab

WORKDIR /workspace
CMD ["bash"]
```

Adds system tools and the EULA env vars so every container that inherits from this doesn't need to set them manually. The `WORKDIR /workspace` sets the default directory you land in when you `exec` or `run` the container.

**The `isaaclab` wrapper** is critical. Isaac Lab's `isaaclab.sh` resolves `ISAACLAB_PATH` from its own directory via `dirname $BASH_SOURCE`. A symlink would resolve to `/usr/local/bin/` (wrong). The wrapper script instead uses `exec` to call the real script, which then correctly resolves to `/workspace/isaaclab/`. Without this, you get `command not found` or Python path errors inside containers.

---

### `Dockerfile.training`

```dockerfile
FROM mina-isaaclab-base:latest
COPY source/ /workspace/source/
RUN cd /workspace/source/berkeley_humanoid_lite_assets && pip install -e .
RUN cd /workspace/source/berkeley_humanoid_lite && pip install -e .
COPY scripts/ /workspace/scripts/
COPY configs/ /workspace/configs/
ENV HEADLESS=1
CMD ["bash", "-c", "isaaclab -p scripts/rsl_rl/train.py ..."]
```

**Both `berkeley_humanoid_lite_assets` and `berkeley_humanoid_lite` must be installed.** The assets package provides robot USD configs (`HUMANOID_LITE_BIPED_CFG`) that the task code imports. Installing only the main package causes `ModuleNotFoundError: No module named 'berkeley_humanoid_lite_assets'` at runtime. Install assets first since the main package depends on it.

**`COPY` at build time** means the source code is frozen into the image layer. This is intentional for training — you want a reproducible snapshot, not a live-synced version that could change mid-run. The `pip install -e .` installs the package in editable mode inside the image so Python can find it as a module.

**`CMD`** is the default command if you run the container without specifying one. You can always override it: `docker run mina-bhl-training:latest bash`.

---

### `Dockerfile.streaming`

```dockerfile
FROM mina-bhl-training:latest
EXPOSE 47995-48012/udp
EXPOSE 49000-49007/udp
EXPOSE 49100/tcp
EXPOSE 8211/tcp
ENV HEADLESS=0
ENV LIVESTREAM=2
ENV ENABLE_CAMERAS=1
```

`EXPOSE` is documentation — it tells Docker which ports the container intends to use. It doesn't actually open them; that happens at `docker run -p` time. The env var overrides here flip the training image from headless to streaming mode without duplicating any other layer.

---

### `Dockerfile.synthdata`

```dockerfile
FROM nvcr.io/nvidia/isaac-sim:4.5.0
ENV HEADLESS=1
ENV ENABLE_CAMERAS=1
COPY data_gen/ /workspace/data_gen/
CMD ["/isaac-sim/python.sh", "data_gen/generate.py", ...]
```

Note the CMD uses `/isaac-sim/python.sh` not `python` — Isaac Sim ships its own Python interpreter with all Omniverse extensions pre-configured. Using the system Python would miss all the `omni.*` modules.

---

### `Dockerfile.deploy`

```dockerfile
FROM pytorch/pytorch:2.3.0-cuda12.1-cudnn8-runtime
COPY deploy/ /workspace/deploy/
COPY checkpoints/model_final.pt /workspace/model.pt
CMD ["python", "deploy/run_policy.py", "--checkpoint", "/workspace/model.pt"]
```

This is the only image with no Isaac Sim dependency. The `pytorch:...-runtime` base is ~4 GB vs ~20 GB for Isaac Sim images. It can run on any CUDA-capable machine — Jetson Orin, cloud inference instance, workstation — without an NGC account or EULA.

---

## Volumes — every mount explained

### The bind mount vs named volume distinction

In this project **everything is a bind mount** — an explicit host path mapped into the container. There are no Docker named volumes (which would be managed by Docker under `/var/lib/docker/volumes/`). The distinction matters because:

- Bind mounts: you control the path, you can browse the files with `ls`, back them up, version them with git
- Named volumes: Docker controls the path, you can only access them via `docker volume inspect` or by exec-ing into a container

All mounts use the same syntax regardless:
```
- /absolute/host/path:/container/path:mode
- ${ENV_VAR}/relative:/container/path:mode
```

---

### Isaac Sim generated cache mounts

These are directories that **Isaac Sim writes to on first run** and reads from on every subsequent run. You mount them to the host so they survive container restarts. You never put anything here yourself.

```
~/docker/isaac-sim/cache/kit  →  /isaac-sim/kit/cache
```
The Omniverse Kit framework cache. Contains compiled shader programs (GLSL/HLSL → GPU bytecode), extension dependency graphs, and asset manifests. Generated once on first launch, can be 2–4 GB. Without this mount, every container launch triggers full shader recompilation — typically 10–20 minutes.

```
~/docker/isaac-sim/cache/ov  →  /root/.cache/ov
```
The Omniverse asset cache. When Isaac Sim loads a USD file (robot URDF converted to USD, terrain meshes, etc.) it processes and cooks the geometry for PhysX collision detection. These cooked meshes are cached here. Without this, every run re-processes all assets — adds several minutes per unique asset.

```
~/docker/isaac-sim/cache/pip  →  /root/.cache/pip
```
Standard pip wheel cache. Speeds up `pip install` steps during image rebuilds by avoiding re-downloading packages already seen.

```
~/docker/isaac-sim/cache/glcache  →  /root/.cache/nvidia/GLCache
```
NVIDIA OpenGL driver shader cache. GPU programs compiled by the display driver for rendering. Separate from the Kit cache above.

```
~/docker/isaac-sim/cache/computecache  →  /root/.nv/ComputeCache
```
CUDA kernel cache. PhysX GPU, warp kernels, and other CUDA compute programs are JIT-compiled on first use and stored here. Persisting this saves ~2–5 minutes of CUDA compilation on every cold start.

```
~/docker/isaac-sim/logs  →  /root/.nvidia-omniverse/logs
```
Omniverse application logs. If a container crashes silently, the crash log survives here on the host. Essential for debugging.

```
~/docker/isaac-sim/data  →  /root/.local/share/ov/data
```
Omniverse persistent application data — extension settings, user preferences, nucleus server configurations. Mostly set-and-forget.

---

### Project bind mounts (dev container only)

These are your live files. They only appear on `mina-isaaclab-base` because baking them in (as in the training image) is intentional for reproducibility — the dev container is the exception.

```
${MINA_ROOT}/source  →  /workspace/source  (rw)
```
Your `berkeley_humanoid_lite` and `berkeley_humanoid_lite_assets` packages. Edit on host in VS Code, changes visible inside container immediately. **Note:** in the dev container, these packages are NOT pre-installed. You must run `pip install -e .` for each after starting the container (see Usage Guide).

```
${MINA_ROOT}/scripts  →  /workspace/scripts  (rw)
```
Training, play, and utility scripts. Live-synced for the same reason.

```
${MINA_ROOT}/configs  →  /workspace/configs  (rw)
```
Hydra configs, agent configs, task-specific YAML files. Also where `play.py` writes `policy_latest.yaml` deploy config.

```
${MINA_ROOT}/logs  →  /workspace/logs  (rw)
```
Written by training scripts inside the container, read by TensorBoard/WandB on the host. The `rw` on both sides means you can also drop files in from the host (e.g. copying a log from a cluster run).

```
${MINA_ROOT}/checkpoints  →  /workspace/checkpoints  (rw)
```
Model `.pt` files. Written by training, read by play/streaming. `rw` in dev, `ro` in streaming (a streaming container should never be able to overwrite a checkpoint).

```
${ISAACLAB_ROOT}/source  →  /isaac-lab/source  (rw)
```
The Isaac Lab framework source. Normally you don't need this — Isaac Lab is installed as a package inside the image. This mount is for when you need to patch or debug Isaac Lab itself (e.g. stepping through `InteractiveScene.clone_environments` to understand a physics bug). Changes here override the installed package because Isaac Lab is also installed in editable mode inside the image.

---

### Output-only mounts (training, streaming, synthdata)

```
${MINA_ROOT}/logs  →  /workspace/logs  (rw)          # training
${MINA_ROOT}/checkpoints  →  /workspace/checkpoints (rw)  # training
${MINA_ROOT}/checkpoints  →  /workspace/checkpoints (ro)  # streaming
${MINA_ROOT}/outputs  →  /output  (rw)               # synthdata
```

The `ro` (read-only) on the streaming checkpoint mount is a deliberate safety measure — a streaming container runs inference and should have no ability to modify your saved models.

---

## Environment variables

### Isaac Sim required vars (always set)

```
ACCEPT_EULA=Y         Required by NVIDIA — container refuses to start without it
PRIVACY_CONSENT=Y     Required by NVIDIA — same
```

### App-level vars (control how Isaac Sim starts)

For this repo's IsaacLab version, browser WebRTC demo streaming is currently unreliable. Use the native Isaac Sim Streaming Client when `LIVESTREAM` is enabled.

```
HEADLESS=0|1          0 = open a window, 1 = no window
LIVESTREAM=0|1|2      0 = off, 1 = livestream public endpoint, 2 = livestream local/private
ENABLE_CAMERAS=0|1    1 = activate offscreen render pipeline
                      Required for any camera sensor even in headless mode
                      MUST be 1 for --video recording to work
PUBLIC_IP=x.x.x.x    Streaming client endpoint — set to your machine's public IP
                      when streaming from a cloud GPU
```

**The interaction between `HEADLESS` and `ENABLE_CAMERAS`:**

| `HEADLESS` | `ENABLE_CAMERAS` | Effect |
|---|---|---|
| 0 | 0 | GUI window, no camera tensor data |
| 0 | 1 | GUI window + camera tensors (local dev with vision) |
| 1 | 0 | No window, no cameras — fastest, for state-based training |
| 1 | 1 | No window, cameras active — for vision training, video recording, or synthdata |

`LIVESTREAM=2` forces `HEADLESS=1` internally regardless of what you set.

### Project vars (read by `run.sh` and compose)

```
MINA_ROOT             Absolute path to /home/alex/dev/Mina
ISAACLAB_ROOT         Absolute path to /home/alex/dev/IsaacLab
TASK                  Default gym env ID for train/stream/eval
                      (e.g. Velocity-Berkeley-Humanoid-Lite-Biped-v0)
NUM_ENVS              Default number of parallel environments
NGC_API_KEY           Your personal NGC key — used only by ngc-login
```

---

## Docker Compose mechanics

### YAML anchors (`&` and `*`)

```yaml
x-isaac-common: &isaac-common    # & defines the anchor named "isaac-common"
  runtime: nvidia
  network_mode: host
  ipc: host

services:
  mina-bhl-training:
    <<: *isaac-common            # << merges the anchor's content here
```

`<<: *isaac-common` pastes `runtime: nvidia`, `network_mode: host`, and `ipc: host` into the service definition. If you need to change `network_mode` across all containers, you change it in one place.

**Warning about duplicate YAML keys:** YAML silently takes the last value when a key appears twice. For example, having `volumes:` defined twice on a service means the first block is silently discarded. Always use a single `volumes:` key with all entries listed together.

### Profiles

```yaml
profiles: [training]
```

Without profiles, `docker compose up` would start every service. Profiles require you to opt in:

```bash
docker compose --profile training up mina-bhl-training
docker compose --profile dev up mina-isaaclab-base
```

This prevents accidentally starting all four containers simultaneously and exhausting GPU memory.

### `${VAR:-default}` syntax

```yaml
- ${CACHE_KIT:-~/docker/isaac-sim/cache/kit}:/isaac-sim/kit/cache:rw
```

If `CACHE_KIT` is set in `.env`, use that value. If not, fall back to `~/docker/isaac-sim/cache/kit`. Every volume has a fallback so the file works even with a partially filled `.env`.

---

## `network_mode: host` — why it matters

Isaac Sim doesn't play well with Docker's default bridge networking. In bridge mode, Docker assigns the container its own virtual network interface with a different IP. Isaac Sim's Omniverse services (Nucleus client, streaming, physics synchronization) open many ports dynamically and expect to bind directly to the machine's network. In bridge mode you'd need to manually map every port it uses.

`network_mode: host` collapses the container's network into the host's — the container sees the same interfaces, same IP, same ports as the host machine. The container can't be isolated from the host network, but for a local GPU workstation or a dedicated cloud training VM this is the right trade-off.

Consequence: two containers with `network_mode: host` that both try to bind the same port will conflict. Don't run `mina-bhl-training` and `mina-bhl-streaming` simultaneously.

---

## `ipc: host` — why it matters

IPC (Inter-Process Communication) shared memory is how Isaac Sim's GPU pipeline coordinates between processes. The PhysX GPU dispatcher, the rendering pipeline, and the Python process all communicate via shared memory segments. In Docker's default IPC mode, each container gets its own isolated shared memory namespace with a small default size limit (64 MB). Isaac Sim needs much more and needs it on the host's namespace. Without `ipc: host` you get crashes like:

```
[Error] Failed to create shared memory segment
[Error] PhysX GPU dispatcher initialization failed
```

---

## Build context and COPY paths

In every Dockerfile, the `COPY` source paths are relative to the **build context**, not to the Dockerfile's location. The build context is set in `run.sh` as `$PROJECT_ROOT` (i.e. `/home/alex/dev/Mina`):

```bash
docker build \
    -f docker_mina/Dockerfile.training \   # Dockerfile location
    -t mina-bhl-training:latest \
    /home/alex/dev/Mina                     # build context ← COPY paths are relative to this
```

So `COPY source/ /workspace/source/` in `Dockerfile.training` copies `/home/alex/dev/Mina/source/` — not a `source/` folder next to the Dockerfile.

Similarly, `docker-compose.yaml` uses `context: ..` and `dockerfile: docker_mina/Dockerfile.training` — the `..` makes the build context the Mina project root, and the dockerfile path is relative to that context.

---

## Complete file map

```
/home/alex/dev/Mina/
├── docker_mina/
│   ├── .env                    ← your credentials and paths (never commit to git)
│   ├── run.sh                  ← all commands live here
│   ├── docker-compose.yaml     ← service definitions
│   ├── Dockerfile.base         ← builds mina-isaaclab-base
│   ├── Dockerfile.training     ← builds mina-bhl-training
│   ├── Dockerfile.streaming    ← builds mina-bhl-streaming
│   ├── Dockerfile.synthdata    ← builds mina-isaacsim-synthdata
│   ├── Dockerfile.deploy       ← builds mina-bhl-deploy
│   ├── mina_docker_reference.md ← this file
│   └── mina_docker_usage.md    ← usage guide
│
├── source/
│   ├── berkeley_humanoid_lite/       ← main task package
│   └── berkeley_humanoid_lite_assets/ ← robot USD configs (required dependency)
├── scripts/
│   └── rsl_rl/
│       ├── train.py             ← RL training script
│       ├── play.py              ← inference / eval / export script
│       └── cli_args.py          ← shared CLI argument helpers
├── configs/                     ← Hydra configs + exported policy_latest.yaml
├── checkpoints/                 ← exported .onnx policies
├── logs/
│   └── rsl_rl/
│       ├── biped/               ← Velocity-Berkeley-Humanoid-Lite-Biped-v0 runs
│       │   └── <timestamp>/
│       │       ├── model_*.pt   ← training checkpoints
│       │       ├── videos/      ← recorded videos (train/ and play/ subdirs)
│       │       ├── exported/    ← JIT + ONNX exported policies
│       │       ├── params/      ← frozen config snapshots
│       │       └── isaaclab/    ← Isaac Lab internal logs
│       └── humanoid/            ← Velocity-Berkeley-Humanoid-Lite-v0 runs
├── outputs/                     ← eval videos, synthetic datasets
└── data_gen/                    ← synthdata generation scripts
```

```
~/docker/isaac-sim/             ← Isaac Sim generated cache (on host, never in git)
├── cache/
│   ├── kit/                    ← compiled shaders (~2-4 GB)
│   ├── ov/                     ← processed USD assets
│   ├── pip/                    ← pip wheels
│   ├── glcache/                ← OpenGL driver cache
│   └── computecache/           ← CUDA kernel cache
├── logs/                       ← Omniverse crash logs
└── data/                       ← Omniverse app data
```

---

## Known gotchas

### `isaaclab: command not found`
The base image creates a wrapper script at `/usr/local/bin/isaaclab` that delegates to `/workspace/isaaclab/isaaclab.sh`. A symlink does NOT work because `isaaclab.sh` resolves its own path via `dirname $BASH_SOURCE` — a symlink would resolve to `/usr/local/bin/` instead of `/workspace/isaaclab/`.

### `ModuleNotFoundError: No module named 'berkeley_humanoid_lite_assets'`
Both `berkeley_humanoid_lite_assets` AND `berkeley_humanoid_lite` must be pip-installed. The assets package must be installed first. The training Dockerfile handles this automatically. The dev container does NOT — you must install manually after every fresh start.

### `get_checkpoint_path` picks wrong directory
The `play.py` script uses `get_checkpoint_path()` which finds the newest subdirectory in the experiment log root. If anything creates a non-timestamped directory as a sibling of training runs (e.g. an `isaaclab/` log dir), it may be picked as the "latest run" and fail. The `train.py` script now creates `isaaclab/` logs inside the run directory to avoid this.

### Play script doesn't record videos
The `play.py` script must call `env.reset()` (not `env.get_observations()`) before the simulation loop for `gymnasium.wrappers.RecordVideo` to start recording. The `reset()` call triggers the video wrapper's recording start.

### `ENABLE_CAMERAS=1` required for video
Video recording uses the offscreen render pipeline. Without `ENABLE_CAMERAS=1`, no frames are captured and videos will be empty or not created. Set it as an env var: `-e ENABLE_CAMERAS=1`.

### `obs_normalizer` attribute error in play.py
Newer versions of `rsl_rl` removed `OnPolicyRunner.obs_normalizer`. The `play.py` script uses `getattr(ppo_runner, "obs_normalizer", None)` to handle both old and new versions gracefully.

### Root-owned files in logs/
Containers run as root by default. Files written by the container (checkpoints, videos, logs) are owned by `root` on the host. To delete them without sudo: `docker run --rm -v /path:/data alpine rm -rf /data/target`.
