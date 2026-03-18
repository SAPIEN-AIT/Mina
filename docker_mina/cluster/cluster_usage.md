# Cluster & Apptainer — Usage

All commands from `/home/alex/dev/Mina`.

---

## One-time setup

```bash
# 1. Set your cluster credentials
nano docker_mina/cluster/.env.cluster    # CLUSTER_LOGIN, paths
nano docker_mina/cluster/submit_job_slurm.sh  # --mail-user

# 2. Build the training Docker image (if not already done)
./docker_mina/run.sh build-training
```

---

## Local Apptainer testing

Test the full Singularity workflow on your machine before touching the cluster.

```bash
# Build sandbox from Docker image (~10 min, ~18 GB in /tmp)
./docker_mina/run.sh apptainer-build

# Run training locally via Apptainer
./docker_mina/run.sh apptainer-train                                          # defaults
./docker_mina/run.sh apptainer-train Velocity-Berkeley-Humanoid-Lite-v0 64    # custom

# Interactive shell (debug)
./docker_mina/run.sh apptainer-shell

# Clean up sandbox
./docker_mina/run.sh apptainer-clean
```

Logs go to `logs/rsl_rl/` on host, same as Docker training.

The sandbox lives at `/tmp/mina-bhl-training.sif` — does not survive reboot.

---

## Push image to cluster

```bash
bash docker_mina/cluster/cluster_interface.sh push mina-bhl-training
```

This streams the Docker image over SSH, builds a Singularity sandbox on the cluster, and tars it. Takes 10-30 min first time. Only redo after rebuilding the Docker image.

---

## Submit a training job

```bash
bash docker_mina/cluster/cluster_interface.sh job mina-bhl-training \
    --task Velocity-Berkeley-Humanoid-Lite-Biped-v0 \
    --num_envs 4096 \
    --headless
```

Everything after the image name is forwarded to `train.py`.

### Train arguments

| Argument | Type | Default | Description |
|---|---|---|---|
| `--task` | str | from `.env` | Task ID (see table below) |
| `--num_envs` | int | 2048 | Number of parallel environments |
| `--max_iterations` | int | 6000 | Training iterations |
| `--seed` | int | None | Random seed |
| `--headless` | flag | off | No GUI (required on cluster) |
| `--experiment_name` | str | from task cfg | Log folder name under `logs/rsl_rl/` |
| `--run_name` | str | None | Suffix appended to timestamped run dir |
| `--resume` | bool | False | Resume from checkpoint |
| `--load_run` | str | None | Run folder to resume from |
| `--checkpoint` | str | None | Specific checkpoint file to load |
| `--logger` | str | tensorboard | `tensorboard`, `wandb`, or `neptune` |
| `--log_project_name` | str | None | Project name for wandb/neptune |
| `--video` | flag | off | Record training videos |
| `--video_length` | int | 200 | Video length in steps |
| `--video_interval` | int | 2000 | Steps between recordings |
| `--device` | str | cuda:0 | `cpu`, `cuda`, `cuda:N` |
| `--enable_cameras` | flag | off | Enable camera sensors (auto-set with `--video`) |

### Play arguments

To run play on the cluster, change `CLUSTER_PYTHON_EXECUTABLE` in `.env.cluster` to `scripts/rsl_rl/play.py`.

| Argument | Type | Default | Description |
|---|---|---|---|
| `--task` | str | from `.env` | Task ID |
| `--num_envs` | int | from env cfg | Number of environments |
| `--load_run` | str | latest | Run folder containing the checkpoint |
| `--checkpoint` | str | latest | Checkpoint file to load |
| `--video` | flag | off | Record a play video |
| `--video_length` | int | 200 | Video length in steps |
| `--disable_fabric` | flag | off | Use USD I/O instead of Fabric |
| `--headless` | flag | off | No GUI |
| `--device` | str | cuda:0 | `cpu`, `cuda`, `cuda:N` |

### Available tasks

| Task ID | Robot | Action Dim |
|---|---|---|
| `Velocity-Berkeley-Humanoid-Lite-v0` | Full humanoid (arms+legs, 21 DOF) | 21 |
| `Velocity-Berkeley-Humanoid-Lite-Biped-v0` | Biped (legs only, 12 DOF) | 12 |

### Examples

```bash
# Resume training from a specific checkpoint
bash docker_mina/cluster/cluster_interface.sh job mina-bhl-training \
    --task Velocity-Berkeley-Humanoid-Lite-Biped-v0 \
    --num_envs 4096 --headless \
    --resume True --load_run 2026-03-18_14-00-00 --checkpoint model_3000.pt

# Train full humanoid with wandb logging
bash docker_mina/cluster/cluster_interface.sh job mina-bhl-training \
    --task Velocity-Berkeley-Humanoid-Lite-v0 \
    --num_envs 4096 --headless \
    --max_iterations 10000 --logger wandb --log_project_name mina-humanoid

# Train with video recording and custom seed
bash docker_mina/cluster/cluster_interface.sh job mina-bhl-training \
    --task Velocity-Berkeley-Humanoid-Lite-Biped-v0 \
    --num_envs 2048 --headless \
    --video --video_interval 1000 --seed 42
```

See [cluster_reference.md](cluster_reference.md) for full details on how arguments flow through the pipeline and the configurable training hyperparameters.

---

## Monitor jobs

```bash
ssh huou@kuma.hpc.epfl.ch

squeue -u $USER                    # queue status
scontrol show job <JOBID>          # job details
scancel <JOBID>                    # cancel
tail -f /home/huou/isaaclab_*/logs/**/*.txt   # live logs
tail -50 /home/huou/isaaclab_*/slurm-<JOBID>.out  # last 50 lines of job output
```

---

## Quick reference

```
LOCAL TEST        apptainer-build → apptainer-train
PUSH TO CLUSTER   cluster_interface.sh push mina-bhl-training
RUN ON CLUSTER    cluster_interface.sh job mina-bhl-training --task ... --num_envs ...
MONITOR           ssh → squeue / tail logs
CLEANUP           apptainer-clean (local) / scancel (cluster)
```

---

## Gotchas

- **Root-owned log dirs** from Docker runs block Apptainer (runs as your user). Fix:
  `docker run --rm -v $PWD/logs:/data alpine chown -R $(id -u):$(id -g) /data`
- **Disk space**: sandbox is ~18 GB. Need ~26 GB free to build (docker tar + sandbox).
  Free space with `docker builder prune --all -f`.
- **Stale cache locks** (`omni.datastore` errors): harmless, just warnings.
- **Sandbox in /tmp**: lost on reboot. Change `APPTAINER_DIR` in `run.sh` for persistence.

---

## Files

```
docker_mina/cluster/
├── .env.cluster           ← your cluster credentials and paths
├── cluster_interface.sh   ← push images / submit jobs (the one script you call)
├── submit_job_slurm.sh    ← SBATCH template (edit GPU partition, time limit, email)
├── run_singularity.sh     ← runs on compute node (don't edit for normal use)
├── cluster_usage.md       ← this file
└── cluster_reference.md   ← how everything works under the hood
```
