# Docker Finetuning: Making Mina Container Runtime Stable

## Mina Docker Stack — From Failing Startup to Usable Isaac Lab Runtime

---

## Executive Summary

This document captures the exact Docker-level changes that made the Mina container setup run reliably for the Isaac Lab + Berkeley Humanoid workflow.

The final fix was not one single flag, but a set of practical hardening changes across `docker/Dockerfile` and `docker/docker-compose.yaml`:

- install `uv` in-container to match project commands (`uv run ...`)
- install `uv` to a global path so it works for non-root users
- add robust environment defaults for NVIDIA runtime, EULA/privacy, display, and terminal behavior
- set `ipc: host` to prevent shared-memory bottlenecks/crashes
- run container as host-mapped user to avoid root-owned files in bind mounts
- isolate container virtualenv/cache from host workspace
- make compose build args and bind paths resilient with fallback defaults
- bind `/etc/localtime` for host/container time consistency

These changes turned the container from "starts inconsistently or fails at runtime" into a reproducible baseline for training/play/sim workflows.

---

## Starting Point

### Symptoms

The Docker workflow was close, but still fragile. Typical issues in this stage are:

- runtime instability under simulator load
- missing tooling expected by project commands (`uv`)
- environment-dependent behavior (GPU vars/display/EULA)
- brittle compose variables when `.env` is incomplete

### Goal

Get a container setup that:

- starts consistently across machines
- can run Mina commands as documented
- avoids shared-memory failures under Isaac Sim load
- keeps defaults sane while still allowing overrides

---

## Phase 1: Align Container Tooling with Project Workflow

### Change

File: `docker/Dockerfile`

Added:

- install packages: `curl`, `ca-certificates`, `zenity`
- install `uv` via `https://astral.sh/uv/install.sh` with `UV_INSTALL_DIR=/usr/local/bin`

### Why this mattered

Project commands and docs use `uv run ...`. Without `uv` in the image, runtime behavior depends on ad-hoc manual setup inside each container.

Installing `uv` during image build makes command execution deterministic and avoids "works on one shell/container, fails on another" drift.

### Result

The container can run the same command style used in Mina development (`uv run ...`) immediately after build.

---

## Phase 1.5: Non-Root Runtime and Host Permission Safety

### Change

Files: `docker/docker-compose.yaml`, `docker/.env.base`

Added:

- `user: "${LOCAL_UID:-1000}:${LOCAL_GID:-1000}"`
- `.env` defaults:
- `LOCAL_UID=1000`
- `LOCAL_GID=1000`
- `OMNI_KIT_ALLOW_ROOT` default changed to `0`

### Why this mattered

Running as root in a bind-mounted repo tends to create root-owned files on the host and causes cleanup/permission friction later.

Mapping container user to host UID/GID keeps ownership sane and avoids the recurring "root problems" from previous runs.

### Result

Container writes into the workspace with normal host ownership, while still allowing explicit overrides when needed.

---

## Phase 2: Harden Compose Environment Defaults

### Change

File: `docker/docker-compose.yaml`

Environment block moved from hardcoded/sparse values to override-friendly defaults:

- `OMNI_KIT_ALLOW_ROOT=${OMNI_KIT_ALLOW_ROOT:-0}`
- `NVIDIA_VISIBLE_DEVICES=${NVIDIA_VISIBLE_DEVICES:-all}`
- `NVIDIA_DRIVER_CAPABILITIES=${NVIDIA_DRIVER_CAPABILITIES:-all}`
- `ACCEPT_EULA=${ACCEPT_EULA:-Y}`
- `PRIVACY_CONSENT=${PRIVACY_CONSENT:-Y}`
- `DISPLAY=${DISPLAY:-}`
- `TERM=${TERM:-xterm-256color}`
- `QT_X11_NO_MITSHM=${QT_X11_NO_MITSHM:-1}`
- `UV_PROJECT_ENVIRONMENT=${UV_PROJECT_ENVIRONMENT:-/tmp/mina-uv-env}`
- `UV_CACHE_DIR=${UV_CACHE_DIR:-/tmp/mina-uv-cache}`

### Why this mattered

Isaac/Omniverse stacks are sensitive to runtime env setup. Missing or partial env values can cause subtle failures.

Using `${VAR:-default}` gives:

- sane defaults for first-time bring-up
- easy overrides per user/machine
- fewer hard failures from incomplete `.env` files

### Result

Compose behavior became much more portable and predictable across local setups.

---

## Phase 2.5: Virtualenv Isolation from Host Workspace

### Change

Files: `docker/docker-compose.yaml`, `docker/.env.base`

Added:

- `UV_PROJECT_ENVIRONMENT=/tmp/mina-uv-env`
- `UV_CACHE_DIR=/tmp/mina-uv-cache`
- Docker-managed volume mounted on repo `.venv` inside the container:
- `mina-project-venv -> /workspace/isaaclab_extension_template/.venv`

### Why this mattered

With a bind mount, accidental virtualenv operations can mutate host `.venv`, changing local development state unexpectedly.

This setup provides two layers of protection:

- uv writes to `/tmp` paths in-container by default
- the project `.venv` path is masked by a named Docker volume

Together, container Python environment changes stay inside Docker and do not leak into host setup.

### Result

Changing/rebuilding container environments no longer trashes or reconfigures the host virtualenv.

---

## Phase 3: Shared Memory Fix (`ipc: host`)

### Change

File: `docker/docker-compose.yaml`

Added:

- `ipc: host`

### Why this mattered

This was the highest-impact runtime stabilization change.

Simulator workloads can stress `/dev/shm` heavily. With default container IPC limits, apps may fail, hang, or degrade unpredictably under load.

`ipc: host` allows the container to use host shared-memory capacity, avoiding the default container shm ceiling.

### Result

The container runtime became stable enough for the full Mina simulation flow. This aligns with the observation that Docker "finally runs" after these updates.

---

## Phase 4: Make Compose Paths and Build Args Resilient

### Change

File: `docker/docker-compose.yaml`

Build args now include fallback values:

- `ISAACLAB_BASE_IMAGE_ARG=${ISAACLAB_BASE_IMAGE:-isaac-lab-base}`
- `DOCKER_ISAACLAB_EXTENSION_TEMPLATE_PATH_ARG=${DOCKER_ISAACLAB_EXTENSION_TEMPLATE_PATH:-/workspace/isaaclab_extension_template}`

Volume target also uses fallback:

- `target: ${DOCKER_ISAACLAB_EXTENSION_TEMPLATE_PATH:-/workspace/isaaclab_extension_template}`

### Why this mattered

Earlier behavior required all variables to be perfectly defined up front. Missing one value could break build or mount behavior.

Fallbacks preserve flexibility while removing brittle startup assumptions.

### Result

Fewer config-related startup failures and a better out-of-the-box compose experience.

---

## Phase 5: Host Time Sync for Better Cross-Tool Consistency

### Change

File: `docker/docker-compose.yaml`

Added bind mount:

- `source: /etc/localtime`
- `target: /etc/localtime`
- `read_only: true`

### Why this mattered

Consistent host/container time reduces confusion in logs, timestamps, and debugging between host tools and containerized processes.

### Result

Cleaner diagnostics and easier correlation when troubleshooting multi-process workflows.

---

## Final Diff Summary

### `docker/Dockerfile`

- Installed `curl`, `ca-certificates`, `zenity`
- Installed `uv` globally (`/usr/local/bin`) during image build

### `docker/docker-compose.yaml`

- Added environment defaults for NVIDIA + Omniverse + display/runtime
- Defaulted to non-root execution (`OMNI_KIT_ALLOW_ROOT=0`)
- Added host UID/GID user mapping for file ownership safety
- Added uv env/cache isolation (`/tmp/mina-uv-env`, `/tmp/mina-uv-cache`)
- Added named volume over project `.venv` for host isolation
- Added `ipc: host` (key stability fix)
- Added defaulted build args and volume target path
- Added `/etc/localtime` read-only bind mount

### `docker/.env.base`

- Added `LOCAL_UID` and `LOCAL_GID` defaults
- Added `UV_PROJECT_ENVIRONMENT` and `UV_CACHE_DIR` defaults

---

## Practical Verification Checklist

After rebuilding, verify:

1. `docker compose -f docker/docker-compose.yaml build` completes.
2. `docker compose -f docker/docker-compose.yaml run --rm isaac-lab-template uv --version` works.
3. Container starts without shared-memory errors under Isaac Sim workload.
4. Mina commands using `uv run ...` execute inside container.
5. Files created from container are not root-owned on host.
6. Container-side Python env changes do not alter host `.venv`.
7. Logs/timestamps are coherent between host and container.

---

## Lessons Learned

1. Docker reliability for simulator stacks is mostly runtime ergonomics, not only image correctness.
2. Shared memory limits can look like random simulator instability unless explicitly addressed.
3. Matching toolchain assumptions (`uv`) inside the image removes a major class of "works locally but not in container" problems.
4. Defaulted compose variables are worth the small verbosity cost; they prevent fragile bring-up.
5. A small set of targeted infrastructure tweaks can unlock the entire robotics workflow.
6. Non-root execution and venv isolation should be treated as first-class defaults in bind-mounted robotics repos.

---

## Files Reference

- `docker/Dockerfile`
- `docker/docker-compose.yaml`
- `learnings/docker_finetuning.md`
