# Docker → Apptainer/HPC Hardening: Status & Remaining Issues

## Goal

Build a Docker image that:
1. Bakes all Python dependencies into `/.venv` at build time
2. Works for **any UID/GID** (Apptainer runs as calling user, not root)
3. Is fully self-contained — no internet, no Docker volumes, no compose at runtime
4. Can run Isaac Sim training headlessly on HPC

---

## What Was Done

### 1. Baked Python Environment (`/.venv`)

- `uv sync --frozen --no-dev` installs all deps into `/.venv` during build
- The project requires Python `>=3.10,<3.11` but the base image (`isaac-lab-base`) only ships Python 3.11
- **uv automatically downloads a managed CPython 3.10.20** and installs it under `/root/.local/share/uv/python/cpython-3.10.20-linux-x86_64-gnu/`

### 2. Python Relocation (Symlink Cycle Fix)

**Problem**: The original Dockerfile used `command -v python3.10` to find the interpreter, but `PATH` includes `/.venv/bin`, creating a self-referential symlink cycle (`/.venv/bin/python3.10 → /.venv/bin/python3.10`).

**Fix**: Read `pyvenv.cfg` (written by uv) to discover the real interpreter path, then:
- Copy the entire uv-managed Python from `/root/.local/share/uv/python/...` to `/opt/python/`
- Relink `/.venv/bin/python{,3,3.10}` → `/opt/python/bin/python3.10`
- Update `pyvenv.cfg` home to point to `/opt/python/bin`

**Result**: `readlink -f /.venv/bin/python3` → `/opt/python/bin/python3.10` ✓

### 3. Multi-User Permissions

```dockerfile
RUN chmod -R a+rX /.venv /opt/python && \
    find /.venv/bin /opt/python/bin -type f -executable -exec chmod a+rx {} + && \
    chmod a+rx /.venv/bin/* /opt/python/bin/* && \
    chmod a+w /.venv/lib/python3.10/site-packages/omni/EULA_ACCEPTED 2>/dev/null || true
```

Verified working as UID 65534 (nobody) — Python runs, `uv run` works.

### 4. Runtime Cache Directories

Pre-created under `/root/` with 777 permissions, matching the official Isaac Lab
cluster bind-mount targets:
- `/root/.cache/{ov,pip,nvidia/GLCache}`
- `/root/.nv/ComputeCache`
- `/root/.nvidia-omniverse/logs`
- `/root/.local/share/ov/data`
- `/root/Documents`

### 5. Build Self-Test

A `RUN` step at the end validates Python, symlinks, imports, and site-packages. Build fails if anything is broken.

### 6. Environment Variables Baked In

```dockerfile
ENV UV_NO_SYNC=1      # Prevent uv from trying to write to the read-only /.venv
ENV ACCEPT_EULA=Y     # Auto-accept NVIDIA EULA
```

### 7. EULA Acceptance

```dockerfile
ENV OMNI_KIT_ACCEPT_EULA=Y
```
Discovered in `kit_app.py:19`: bypasses both the file check and the runtime write.

---

## Current Tests Passing

```bash
# Default user (1000:1000)
$ docker compose ... run --rm isaac-lab-template -lc \
    '/.venv/bin/python3 --version && readlink -f /.venv/bin/python3 && uv run python -c "print(\"OK\")"'
Python 3.10.20
/opt/python/bin/python3.10
OK

# Arbitrary user (65534:65534, simulating Apptainer)
$ docker compose ... run --rm --user 65534:65534 isaac-lab-template -lc \
    '/.venv/bin/python3 --version && uv run python -c "print(\"OK\")"'
Python 3.10.20
OK
```

---

## Current Blocker: ~~Omniverse Runtime Write Paths~~ RESOLVED

Isaac Sim / Omniverse Kit writes to many directories under `$HOME` at runtime.
On Apptainer (read-only root filesystem, non-root user), these fail.

### Resolution: Bind Mounts (Official NVIDIA Approach)

Per the [Isaac Lab Cluster Guide](https://isaac-sim.github.io/IsaacLab/main/source/deployment/cluster.html),
the official solution is **Apptainer bind mounts** — not Carbonite setting overrides.

The Dockerfile pre-creates the target directories (under `/root/`) with 777 permissions.
At runtime, `docker/run_apptainer.sh` mounts writable host directories over them:

| Host (auto-created under `$MINA_CACHE_DIR`) | Container target | Purpose |
|-----|-----|-----|
| `cache/ov` | `/root/.cache/ov` | Omniverse cache |
| `cache/pip` | `/root/.cache/pip` | pip cache |
| `cache/glcache` | `/root/.cache/nvidia/GLCache` | GL shader cache |
| `cache/computecache` | `/root/.nv/ComputeCache` | CUDA compute cache |
| `logs` | `/root/.nvidia-omniverse/logs` | Omniverse logs |
| `data` | `/root/.local/share/ov/data` | Omniverse data/config |
| `documents` | `/root/Documents` | Documents |
| `$MINA_WORK_DIR` | `/workspace` | Hydra outputs, logs |

### EULA Acceptance

Replaced the file-touch approach with `ENV OMNI_KIT_ACCEPT_EULA=Y` (discovered in
`kit_app.py:19`: `os.environ.get("OMNI_KIT_ACCEPT_EULA", default="N")`).

### The `outputs/` Directory Issue

Hydra creates `outputs/YYYY-MM-DD/...` in CWD. The Apptainer wrapper binds
a writable workspace dir to `/workspace`, which handles this.

---

## File Inventory

| File | Status | Notes |
|------|--------|-------|
| `docker/Dockerfile` | Modified | All fixes above applied |
| `docker/docker-compose.yaml` | Modified earlier | Non-root user, cache volumes, ipc:host |
| `docker/.env.base` | Modified earlier | UID/GID defaults, UV/cache paths |
| `.dockerignore` | Modified earlier | Excludes .venv, ros_workspaces, build artifacts |

---

## Dockerfile (current state, for reference)

Key layers in order:
1. `FROM isaac-lab-base` (Python 3.11, Isaac Sim, NVIDIA runtime)
2. Install `uv` to `/usr/local/bin`
3. `COPY` dependency metadata + workspace source packages
4. `uv sync --frozen --no-dev` → bakes `/.venv` with Python 3.10.20
5. Relocate uv-managed Python from `/root/...` to `/opt/python/`
6. Set `ENV UV_NO_SYNC=1`, `ACCEPT_EULA=Y`
7. Touch EULA marker file
8. `chmod -R a+rX /.venv /opt/python` (+ EULA file writable)
9. `COPY` rest of repo
10. Create writable runtime dirs (`/var/cache/isaac`, `/tmp/mina-uv-cache`, etc.)
11. Self-test (Python version, symlinks, imports)

---

## Next Steps

1. **Research Omniverse env vars** to redirect `cache/`, `data/`, `logs/` out of the venv (Option C above)
2. **Test with writable overlay** — `docker run` with `--tmpfs /.venv/lib/python3.10/site-packages/omni/cache` as quick validation
3. **Ensure `outputs/` dir is writable** — either pre-create in image or document bind-mount requirement
4. **Apptainer conversion test** — `apptainer build mina.sif docker-daemon://isaac-lab-template:latest`
5. **HPC launch script** — Document all required `--bind` mounts for Apptainer

---

## Quick Debug Commands

```bash
# Inspect Omniverse package for env var usage
docker compose -f docker/docker-compose.yaml --env-file docker/.env.base run --rm --user root \
    isaac-lab-template -lc 'grep -rn "os.environ\|getenv\|OMNI_DATA\|OMNI_CACHE\|CARB_" /.venv/lib/python3.10/site-packages/omni/*.py /.venv/lib/python3.10/site-packages/isaacsim/__init__.py 2>/dev/null | head -40'

# Test with writable omni dirs (quick validation)
docker compose -f docker/docker-compose.yaml --env-file docker/.env.base run --rm \
    --tmpfs /.venv/lib/python3.10/site-packages/omni/cache \
    --tmpfs /.venv/lib/python3.10/site-packages/omni/data \
    isaac-lab-template -lc 'cd /workspace/isaaclab_extension_template && uv run ./scripts/rsl_rl/train.py --task Velocity-Berkeley-Humanoid-Lite-v0 --headless'

# Check what OMNI env vars the runtime reads
docker compose -f docker/docker-compose.yaml --env-file docker/.env.base run --rm --user root \
    isaac-lab-template -lc 'strings /.venv/lib/python3.10/site-packages/omni/*.so 2>/dev/null | grep -i "OMNI_\|CARB_\|cache\|data_path" | sort -u | head -30'
```
