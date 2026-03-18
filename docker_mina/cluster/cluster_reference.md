# Cluster & Apptainer — Technical Reference

How the Docker-to-cluster pipeline works, why each piece exists, and what happens when you run each command.

---

## Why Apptainer?

HPC clusters can't run Docker — it requires root. Apptainer (formerly Singularity) runs containers as your user. The workflow converts Docker images to Apptainer sandboxes.

---

## Architecture overview

### Image hierarchy

```
nvidia/isaac-lab:2.3.2          (NVIDIA base, ~26 GB)
  └── mina-isaaclab-base        (+ system tools, isaaclab wrapper)
        └── mina-bhl-training   (+ source packages baked in, headless defaults)
              ↓
         [apptainer build]
              ↓
         mina-bhl-training.sif  (sandbox directory, ~18 GB)
              ↓
         [tar + upload]
              ↓
         mina-bhl-training.tar  (on cluster /home/huou/singularity/)
```

The **training** image is used for Apptainer — not base — because it has all Python packages pre-installed. The base image requires manual `pip install` on every start, which doesn't work in non-interactive SLURM jobs.

### End-to-end flow

```
YOUR LAPTOP                          CLUSTER (kuma.hpc.epfl.ch)
───────────                          ──────────────────────────

docker build
  Dockerfile.training
       │
       ▼
docker save ──► tar ──► SSH ──────►  /home/huou/singularity/
                                          │
                                     apptainer build --sandbox
                                          │
                                     tar -cf .tar .sif
                                          │
                                     ┌────┘
                                     ▼
rsync Mina/ ──► SSH ──────────────►  /home/huou/isaaclab_YYYYMMDD_HHMMSS/
                                          │
                                     sbatch → SLURM queue
                                          │
                                     ┌────┘  (compute node allocated)
                                     ▼
                                     /scratch/$USER/$JOBID/
                                       ├── copy .tar, untar .sif
                                       ├── copy code snapshot
                                       ├── copy Isaac Sim cache
                                       │
                                       ▼
                                     apptainer exec --nv --writable
                                       ├── bind: source/, scripts/, configs/ from snapshot
                                       ├── bind: logs/ → /home/huou/isaaclab_*/logs/
                                       └── /isaac-sim/python.sh train.py
                                              │
                                              ▼
                                     logs/ ──► /home/huou/isaaclab_*/logs/
                                     cache ──► rsync back to /home/huou/
```

### Local Apptainer testing (what `run.sh apptainer-*` does)

```
YOUR LAPTOP
───────────

docker save mina-bhl-training:latest
       │
       ▼
/tmp/mina-bhl-training-docker.tar
       │
apptainer build --sandbox
       │
       ▼
/tmp/mina-bhl-training.sif/          (sandbox directory tree)
       │
apptainer exec --nv --writable --containall
  -B ~/docker/isaac-sim/cache/* ──► /isaac-sim/kit/cache, /root/.cache/...
  -B Mina/logs/               ──► /workspace/logs
  -B Mina/checkpoints/        ──► /workspace/checkpoints
       │
       ▼
  cd /workspace && /workspace/isaaclab/isaaclab.sh -p scripts/rsl_rl/train.py ...
```

This mirrors the cluster path: same container format, same bind mount strategy (overlay Mina dirs, leave Isaac Lab intact), same entry point. If it works here, it works there.

---

## What each script does

### `cluster_interface.sh push <profile>`

1. Checks Docker and Apptainer version compatibility
2. `docker save` → gzip → streams over SSH to `$CLUSTER_SIF_PATH/<profile>-docker.tar.gz`
3. On cluster: `apptainer build --sandbox --fakeroot` from the docker archive
4. Tars the sandbox into `<profile>.tar`, deletes intermediate files

The `--fakeroot` flag lets you build without root on the cluster. The sandbox is tarred because it's a directory tree with thousands of files — `tar` is much faster to transfer/extract than copying individually.

### `cluster_interface.sh job <profile> [args...]`

1. Creates a timestamped code snapshot: `rsync Mina/ → /home/huou/isaaclab_YYYYMMDD_HHMMSS/`
2. SSHs into login node and calls `submit_job_slurm.sh`

Timestamped snapshots mean concurrent jobs run from independent code copies — no race conditions.

### `submit_job_slurm.sh`

Generates a SLURM batch script and submits it. Key settings:

```
1 task, 4 CPUs, 1 L40S GPU, 12h time limit
TMPDIR=/scratch/$USER/$SLURM_JOB_ID
```

All heavy I/O goes to `/scratch` (node-local NVMe), not `/home` (network-mounted, has quotas).

### `run_singularity.sh` (runs on compute node)

1. Sets up Isaac Sim cache directories
2. Copies cache + code snapshot + container tar to `/scratch`
3. Untars the sandbox
4. Creates bind-mount target directories inside the sandbox
5. Runs `apptainer exec` with all bind mounts
6. After training: rsyncs cache back to `/home` for next job's warm start

```
Bind mount map (inside container ← outside):

Isaac Sim caches (on scratch for speed):
/isaac-sim/kit/cache          ← $TMPDIR/docker-isaac-sim/cache/kit
/root/.cache/ov               ← $TMPDIR/docker-isaac-sim/cache/ov
/root/.cache/pip              ← $TMPDIR/docker-isaac-sim/cache/pip
/root/.cache/nvidia/GLCache   ← $TMPDIR/docker-isaac-sim/cache/glcache
/root/.nv/ComputeCache        ← $TMPDIR/docker-isaac-sim/cache/computecache
/root/.nvidia-omniverse/logs  ← $TMPDIR/docker-isaac-sim/logs
/root/.local/share/ov/data    ← $TMPDIR/docker-isaac-sim/data
/root/Documents               ← $TMPDIR/docker-isaac-sim/documents

Mina code (from snapshot, overlays baked-in code):
/workspace/source             ← $TMPDIR/<code_snapshot>/source
/workspace/scripts            ← $TMPDIR/<code_snapshot>/scripts
/workspace/configs            ← $TMPDIR/<code_snapshot>/configs

Logs (persistent, on /home — not scratch):
/workspace/logs               ← /home/huou/isaaclab_*/logs
```

**Important:** `/workspace/isaaclab` (Isaac Lab) is NOT bind-mounted — it stays as-is from the container image. Only the Mina-specific directories (`source/`, `scripts/`, `configs/`) are overlaid from the code snapshot. This keeps Isaac Lab's editable installs intact while still picking up code changes without rebuilding the `.sif`.

Logs bind-mount directly to `/home` (persistent storage) — not scratch. Training curves survive node reboots and job crashes.

---

## Storage layout on cluster

```
/home/huou/
├── singularity/
│   └── mina-bhl-training.tar       ← container image (~18 GB)
├── docker-isaac-sim/
│   └── cache/                       ← warm cache (shaders, pip, etc.)
├── isaaclab_20260318_140000/        ← code snapshot for job 1
│   └── logs/                        ← persistent training logs
├── isaaclab_20260318_150000/        ← code snapshot for job 2
│   └── logs/
└── ...

/scratch/$USER/$SLURM_JOB_ID/       ← fast local storage (auto-cleaned by SLURM)
├── mina-bhl-training.sif/           ← untarred container
├── isaaclab_20260318_140000/        ← code copy
└── docker-isaac-sim/                ← cache copy
```

---

## `.env.cluster` variables

| Variable | What it controls |
|---|---|
| `CLUSTER_LOGIN` | SSH target: `huou@kuma.hpc.epfl.ch` |
| `CLUSTER_ISAACLAB_DIR` | Base path for code snapshots (gets `_YYYYMMDD_HHMMSS` appended) |
| `CLUSTER_SIF_PATH` | Where the `.tar` container image lives |
| `CLUSTER_ISAAC_SIM_CACHE_DIR` | Warm cache location (must end in `docker-isaac-sim`) |
| `CLUSTER_PYTHON_EXECUTABLE` | Script to run: `scripts/rsl_rl/train.py` |
| `CLUSTER_JOB_SCHEDULER` | `SLURM` (also supports `PBS`) |
| `REMOVE_CODE_COPY_AFTER_JOB` | Delete timestamped snapshot after job finishes |

---

## Train & play arguments — in depth

### How arguments flow

When you run:

```bash
bash docker_mina/cluster/cluster_interface.sh job mina-bhl-training --task Foo --num_envs 4096 --headless
```

The argument chain is:

```
cluster_interface.sh
  └── rsync code → /home/huou/isaaclab_YYYYMMDD_HHMMSS/
  └── ssh → submit_job_slurm.sh "$CLUSTER_ISAACLAB_DIR" "mina-bhl-training" --task Foo --num_envs 4096 --headless
                └── sbatch → run_singularity.sh "$CLUSTER_ISAACLAB_DIR" "mina-bhl-training" --task Foo --num_envs 4096 --headless
                                  └── apptainer exec (bind source/, scripts/, configs/, logs/)
                                        └── cd /workspace && /isaac-sim/python.sh scripts/rsl_rl/train.py --task Foo --num_envs 4096 --headless
```

Everything after the image name passes through untouched to the Python script defined by `CLUSTER_PYTHON_EXECUTABLE` in `.env.cluster` (default: `scripts/rsl_rl/train.py`). The code snapshot's `source/`, `scripts/`, and `configs/` directories are bind-mounted over the baked-in copies, while `/workspace/isaaclab` (Isaac Lab itself) remains untouched.

### Switching between train and play

The cluster pipeline always runs whatever `CLUSTER_PYTHON_EXECUTABLE` points to. To run play instead of train:

1. Edit `.env.cluster`:
   ```
   CLUSTER_PYTHON_EXECUTABLE=scripts/rsl_rl/play.py
   ```
2. Submit the job with play-specific arguments:
   ```bash
   bash docker_mina/cluster/cluster_interface.sh job mina-bhl-training \
       --task Velocity-Berkeley-Humanoid-Lite-Biped-v0 \
       --load_run 2026-03-18_14-00-00 --checkpoint model_6000.pt \
       --headless --video
   ```
3. Remember to set it back to `scripts/rsl_rl/train.py` after.

### train.py arguments — full reference

Arguments come from three sources: `train.py` itself, `cli_args.py` (RSL-RL group), and `AppLauncher` (simulator group).

#### Core training arguments (train.py)

| Argument | Type | Default | Description |
|---|---|---|---|
| `--task` | str | None | Registered task ID. Determines env config, reward structure, observation space, and action space. Required. |
| `--num_envs` | int | None (→ 2048) | Number of parallel simulation environments. More envs = more samples per iteration but more GPU memory. 4096 is typical for cluster GPUs with ≥24 GB VRAM. |
| `--max_iterations` | int | None (→ 6000) | Total PPO training iterations. Each iteration collects `num_steps_per_env × num_envs` transitions. At 4096 envs × 24 steps, that's ~98k transitions/iter. |
| `--seed` | int | None | Random seed for reproducibility. Affects env randomization and network initialization. |
| `--video` | flag | False | Record training videos. Automatically enables `--enable_cameras`. Videos saved to `logs/rsl_rl/<experiment>/<run>/videos/train/`. |
| `--video_length` | int | 200 | Number of environment steps per video clip. At 25 Hz policy rate, 200 steps ≈ 8 seconds of sim time. |
| `--video_interval` | int | 2000 | Record a video every N environment steps. Lower values = more videos but slower training. |

#### RSL-RL agent arguments (cli_args.py)

| Argument | Type | Default | Description |
|---|---|---|---|
| `--experiment_name` | str | from task cfg | Name of the experiment subfolder under `logs/rsl_rl/`. Default is `"biped"` or `"humanoid"` depending on the task config. All runs for the same experiment are grouped here. |
| `--run_name` | str | None | Optional suffix appended to the auto-generated timestamped directory name. E.g., `--run_name test1` → `2026-03-18_14-00-00_test1/`. Useful for labeling experiments. |
| `--resume` | bool | False | Resume training from a checkpoint. Used together with `--load_run` and/or `--checkpoint`. The optimizer state and iteration count are restored. |
| `--load_run` | str | None | Name of a previous run directory to load from. If omitted when resuming, picks the latest run in the experiment folder. Can be a full timestamp like `2026-03-18_14-00-00` or a partial match. |
| `--checkpoint` | str | None | Specific checkpoint file to load (e.g., `model_3000.pt`). If omitted, loads the latest checkpoint in the run directory. |
| `--logger` | str | None (→ tensorboard) | Logging backend. Options: `tensorboard` (default, logs to `events.out.tfevents.*`), `wandb` (requires wandb login), `neptune`. |
| `--log_project_name` | str | None | Project name for wandb or neptune. Only relevant when `--logger wandb` or `--logger neptune`. |

#### AppLauncher / simulator arguments

| Argument | Type | Default | Description |
|---|---|---|---|
| `--headless` | flag | False | Run without GUI. **Required on cluster** (no display available). Selects a lighter Isaac Sim experience file. |
| `--device` | str | None (→ cuda:0) | Simulation device. `cuda:0` for GPU (default), `cpu` for CPU-only (very slow, debugging only). On multi-GPU nodes, use `cuda:N` to select a specific GPU. |
| `--enable_cameras` | flag | False | Enable camera sensor rendering. Required for video recording and any camera-based observations. Auto-enabled by `--video`. Adds overhead even in headless mode. |
| `--livestream` | int | 0 | Enable WebRTC livestreaming. `0` = off, `1` = public network, `2` = local/private network. Not useful on cluster (no client to connect). |
| `--verbose` | flag | False | Verbose-level logging from Isaac Sim. Useful for debugging Omniverse/PhysX issues. |
| `--info` | flag | False | Info-level logging from Isaac Sim. |
| `--experience` | str | "" (auto) | Override the Isaac Sim `.kit` experience file. Auto-selected based on headless/cameras flags. |
| `--rendering_mode` | str | None | Rendering quality preset: `performance`, `balanced`, or `quality`. |
| `--kit_args` | str | "" | Pass-through arguments to Omniverse Kit. |

### play.py arguments — full reference

#### Core play arguments (play.py)

| Argument | Type | Default | Description |
|---|---|---|---|
| `--task` | str | None | Registered task ID. Must match the task the model was trained on. |
| `--num_envs` | int | None (→ from cfg) | Number of environments for inference. Typically much smaller than training (e.g., 1–64). |
| `--video` | flag | False | Record a play video. Exits after `--video_length` steps. Videos saved to `logs/rsl_rl/<experiment>/<run>/videos/play/`. |
| `--video_length` | int | 200 | Number of steps to record. Play exits after this many steps when `--video` is set. |
| `--disable_fabric` | flag | False | Disable Fabric (Isaac Sim's fast data layer) and fall back to USD I/O. Useful for debugging USD-level issues. Slower. |

#### RSL-RL agent arguments (same as train)

| Argument | Type | Default | Description |
|---|---|---|---|
| `--load_run` | str | None (→ latest) | Run directory containing the checkpoint. If omitted, picks the most recent run under the experiment folder. |
| `--checkpoint` | str | None (→ latest) | Checkpoint file to load. If omitted, picks the latest `model_*.pt` in the run. |
| `--experiment_name` | str | from task cfg | Experiment subfolder to look for runs in. |

Play also accepts all AppLauncher arguments (`--headless`, `--device`, etc.) — same as train.

#### What play.py produces

Beyond running inference, `play.py` automatically exports:

1. **JIT policy** → `logs/rsl_rl/<experiment>/<run>/exported/policy.pt`
2. **ONNX policy** → `logs/rsl_rl/<experiment>/<run>/exported/policy.onnx`
3. **Deploy config** → `configs/policy_latest.yaml` — contains joint kinematics, Kp/Kd gains, control frequencies, observation dimensions, and the path to the exported ONNX model.

### Training hyperparameters (not CLI-configurable)

These are set in the task's PPO config file and **cannot** be changed from the command line. To modify them, edit the config before building/pushing the image, or edit the code snapshot on the cluster before submitting.

Config files:
- Biped: `source/berkeley_humanoid_lite/…/config/biped/agents/rsl_rl_ppo_cfg.py`
- Humanoid: `source/berkeley_humanoid_lite/…/config/humanoid/agents/rsl_rl_ppo_cfg.py`

| Parameter | Value | What it controls |
|---|---|---|
| `num_steps_per_env` | 24 | Rollout length per environment per iteration |
| `save_interval` | 100 | Save a checkpoint every N iterations |
| `empirical_normalization` | False | Whether to normalize observations empirically |
| `actor_hidden_dims` | [256, 128, 128] | Policy network MLP layer sizes |
| `critic_hidden_dims` | [256, 128, 128] | Value network MLP layer sizes |
| `activation` | elu | Activation function |
| `init_noise_std` | 1.0 | Initial action noise standard deviation |
| `learning_rate` | 1e-3 | PPO learning rate |
| `schedule` | adaptive | LR schedule (adapts based on KL divergence) |
| `desired_kl` | 0.01 | Target KL divergence for adaptive schedule |
| `clip_param` | 0.2 | PPO clipping parameter |
| `entropy_coef` | 0.008 | Entropy bonus coefficient |
| `gamma` | 0.99 | Discount factor |
| `lam` | 0.95 | GAE lambda |
| `num_learning_epochs` | 5 | PPO epochs per iteration |
| `num_mini_batches` | 4 | Mini-batches per epoch |
| `max_grad_norm` | 1.0 | Gradient clipping norm |
| `value_loss_coef` | 1.0 | Value function loss weight |

---

## Key design decisions

**Why sandbox, not SIF image?**
Sandbox (directory) allows `--writable` mode, which Isaac Sim needs to write temporary files at runtime. A read-only `.sif` file would fail.

**Why copy everything to /scratch?**
`/home` is network-mounted NFS with strict quotas. Reading 18 GB of container files over NFS during training would be painfully slow. `/scratch` is node-local NVMe — orders of magnitude faster. SLURM auto-cleans `/scratch` after the job.

**Why tar the sandbox?**
A sandbox is a directory with 100k+ files. Transferring it over SSH file-by-file is slow. A single `.tar` transfers and extracts much faster.

**Why warm cache?**
Isaac Sim compiles shaders and caches pip packages on first run (5-20 min overhead). The cache is rsynced back to `/home` after each job so the next job skips this warmup.

**Why timestamped code copies?**
Each `job` command creates a fresh `isaaclab_YYYYMMDD_HHMMSS` directory. This means you can submit multiple jobs with different code versions without them interfering. Set `REMOVE_CODE_COPY_AFTER_JOB=true` to auto-clean.

**Why logs on /home, not /scratch?**
Scratch is wiped when the job ends (or if the node crashes). Logs bind-mount to `/home` for persistence — you can `tail -f` from the login node while the job runs.
