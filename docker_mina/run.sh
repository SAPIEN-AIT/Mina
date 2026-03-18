#!/usr/bin/env bash
# ============================================================
# run.sh — Mina container command centre
# Usage: ./docker/run.sh <command> [options]
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Load .env
if [ -f "$SCRIPT_DIR/.env" ]; then
    set -a; source "$SCRIPT_DIR/.env"; set +a
fi

# ── Defaults ────────────────────────────────────────────────
MINA_ROOT="${MINA_ROOT:-$PROJECT_ROOT}"
ISAACLAB_ROOT="${ISAACLAB_ROOT:-/home/dev/IsaacLab}"
TASK="${TASK:-Velocity-Berkeley-Humanoid-Lite-Biped-v0}"
NUM_ENVS="${NUM_ENVS:-4096}"
PUBLIC_IP="${PUBLIC_IP:-127.0.0.1}"

# ── Shared cache volume args ─────────────────────────────────
CACHE_VOLS=(
    "-v" "${HOME}/docker/isaac-sim/cache/kit:/isaac-sim/kit/cache:rw"
    "-v" "${HOME}/docker/isaac-sim/cache/ov:/root/.cache/ov:rw"
    "-v" "${HOME}/docker/isaac-sim/cache/pip:/root/.cache/pip:rw"
    "-v" "${HOME}/docker/isaac-sim/cache/glcache:/root/.cache/nvidia/GLCache:rw"
    "-v" "${HOME}/docker/isaac-sim/cache/computecache:/root/.nv/ComputeCache:rw"
    "-v" "${HOME}/docker/isaac-sim/logs:/root/.nvidia-omniverse/logs:rw"
    "-v" "${HOME}/docker/isaac-sim/data:/root/.local/share/ov/data:rw"
)

# ── Create cache dirs on host if missing ─────────────────────
mkdir -p \
    "${HOME}/docker/isaac-sim/cache/kit" \
    "${HOME}/docker/isaac-sim/cache/ov" \
    "${HOME}/docker/isaac-sim/cache/pip" \
    "${HOME}/docker/isaac-sim/cache/glcache" \
    "${HOME}/docker/isaac-sim/cache/computecache" \
    "${HOME}/docker/isaac-sim/logs" \
    "${HOME}/docker/isaac-sim/data" \
    "${MINA_ROOT}/logs" \
    "${MINA_ROOT}/checkpoints" \
    "${MINA_ROOT}/outputs"

# ── NGC login check ──────────────────────────────────────────
ngc_login() {
    echo "==> Logging into nvcr.io..."
    echo "    Username: \$oauthtoken"
    echo "    Password: <your NGC API key from https://ngc.nvidia.com>"
    docker login nvcr.io --username '$oauthtoken' --password "${NGC_API_KEY}"
}

print_help() {
    cat <<EOF
Mina container command centre

SETUP
  ./docker/run.sh ngc-login          Authenticate with NGC (required once)
  ./docker/run.sh pull-base          Pull base images from NGC

BUILD (run once, then after code changes)
  ./docker/run.sh build-base         Build mina-isaaclab-base
  ./docker/run.sh build-training     Build mina-bhl-training
  ./docker/run.sh build-streaming    Build mina-bhl-streaming
  ./docker/run.sh build-synthdata    Build mina-isaacsim-synthdata
  ./docker/run.sh build-deploy       Build mina-bhl-deploy
  ./docker/run.sh build-all          Build all of the above

RUN
  ./docker/run.sh dev                Enter dev container (live code sync)
  ./docker/run.sh train [task] [n]   Headless training (task, num_envs)
  ./docker/run.sh stream [ckpt]      Livestream policy (checkpoint path)
  ./docker/run.sh synthdata [n]      Generate synthetic data (num scenes)
  ./docker/run.sh eval [task] [ckpt] Headless evaluation with video

APPTAINER (local Singularity testing)
  ./docker/run.sh apptainer-build [profile]   Convert Docker image to Apptainer sandbox
  ./docker/run.sh apptainer-shell [profile]   Interactive shell in Apptainer sandbox
  ./docker/run.sh apptainer-train [task] [n]  Headless training via Apptainer
  ./docker/run.sh apptainer-clean [profile]   Remove local Apptainer sandbox

UTILS
  ./docker/run.sh list               List all mina containers + images
  ./docker/run.sh stop               Stop all running mina containers
  ./docker/run.sh clean              Remove all mina containers + images

EOF
}

# ── Commands ─────────────────────────────────────────────────

cmd_ngc_login() {
    ngc_login
}

cmd_pull_base() {
    echo "==> Pulling nvcr.io/nvidia/isaac-lab:2.3.2 ..."
    docker pull nvcr.io/nvidia/isaac-lab:2.3.2
    echo "==> Pulling nvcr.io/nvidia/isaac-sim:4.5.0 ..."
    docker pull nvcr.io/nvidia/isaac-sim:4.5.0
}

cmd_build_base() {
    echo "==> Building mina-isaaclab-base ..."
    docker build \
        -f "$SCRIPT_DIR/Dockerfile.base" \
        -t mina-isaaclab-base:latest \
        "$PROJECT_ROOT"
}

cmd_build_training() {
    echo "==> Building mina-bhl-training ..."
    docker build \
        -f "$SCRIPT_DIR/Dockerfile.training" \
        -t mina-bhl-training:latest \
        "$PROJECT_ROOT"
}

cmd_build_streaming() {
    echo "==> Building mina-bhl-streaming ..."
    docker build \
        -f "$SCRIPT_DIR/Dockerfile.streaming" \
        -t mina-bhl-streaming:latest \
        "$PROJECT_ROOT"
}

cmd_build_synthdata() {
    echo "==> Building mina-isaacsim-synthdata ..."
    docker build \
        -f "$SCRIPT_DIR/Dockerfile.synthdata" \
        -t mina-isaacsim-synthdata:latest \
        "$PROJECT_ROOT"
}

cmd_build_deploy() {
    echo "==> Building mina-bhl-deploy ..."
    docker build \
        -f "$SCRIPT_DIR/Dockerfile.deploy" \
        -t mina-bhl-deploy:latest \
        "$PROJECT_ROOT"
}

cmd_build_all() {
    cmd_build_base
    cmd_build_training
    cmd_build_streaming
    cmd_build_synthdata
    cmd_build_deploy
    echo "==> All images built."
}

# ── mina-isaaclab-base — interactive dev ─────────────────────
cmd_dev() {
    echo "==> Starting mina-isaaclab-base (dev, live code sync) ..."
    docker run \
        --entrypoint "" \
        --name mina-isaaclab-base \
        --rm -it \
        --gpus all \
        --network host \
        --ipc host \
        -e ACCEPT_EULA=Y \
        -e PRIVACY_CONSENT=Y \
        -e HEADLESS=0 \
        -v "${MINA_ROOT}/source:/workspace/source:rw" \
        -v "${MINA_ROOT}/scripts:/workspace/scripts:rw" \
        -v "${MINA_ROOT}/configs:/workspace/configs:rw" \
        -v "${MINA_ROOT}/logs:/workspace/logs:rw" \
        -v "${MINA_ROOT}/checkpoints:/workspace/checkpoints:rw" \
        -v "${ISAACLAB_ROOT}/source:/isaac-lab/source:rw" \
        "${CACHE_VOLS[@]}" \
        mina-isaaclab-base:latest \
        /bin/bash
}

# ── mina-bhl-training — headless RL training ─────────────────
cmd_train() {
    local task="${1:-$TASK}"
    local num_envs="${2:-$NUM_ENVS}"
    echo "==> Starting mina-bhl-training: task=$task num_envs=$num_envs ..."
    docker run \
        --name mina-bhl-training \
        --rm -it \
        --gpus all \
        --network host \
        --ipc host \
        -e ACCEPT_EULA=Y \
        -e PRIVACY_CONSENT=Y \
        -e HEADLESS=1 \
        -e ENABLE_CAMERAS=0 \
        -e TASK="$task" \
        -e NUM_ENVS="$num_envs" \
        -v "${MINA_ROOT}/logs:/workspace/logs:rw" \
        -v "${MINA_ROOT}/checkpoints:/workspace/checkpoints:rw" \
        "${CACHE_VOLS[@]}" \
        mina-bhl-training:latest
}

# ── mina-bhl-streaming — WebRTC visualization ────────────────
cmd_stream() {
    local checkpoint="${1:-/workspace/checkpoints/model.pt}"
    echo "==> Starting mina-bhl-streaming: checkpoint=$checkpoint ..."
    echo "    Connect via: http://${PUBLIC_IP}:8211/streaming/webrtc-demo/?server=${PUBLIC_IP}"
    docker run \
        --name mina-bhl-streaming \
        --rm -it \
        --gpus all \
        --network host \
        --ipc host \
        -e ACCEPT_EULA=Y \
        -e PRIVACY_CONSENT=Y \
        -e HEADLESS=0 \
        -e LIVESTREAM=2 \
        -e ENABLE_CAMERAS=1 \
        -e PUBLIC_IP="$PUBLIC_IP" \
        -e TASK="$TASK" \
        -e NUM_ENVS=16 \
        -e CHECKPOINT="$checkpoint" \
        -p 47995-48012:47995-48012/udp \
        -p 49000-49007:49000-49007/udp \
        -p 49100:49100/tcp \
        -p 8211:8211/tcp \
        -v "${MINA_ROOT}/checkpoints:/workspace/checkpoints:ro" \
        "${CACHE_VOLS[@]}" \
        mina-bhl-streaming:latest
}

# ── mina-isaacsim-synthdata — dataset generation ─────────────
cmd_synthdata() {
    local num_scenes="${1:-1000}"
    echo "==> Starting mina-isaacsim-synthdata: scenes=$num_scenes ..."
    docker run \
        --name mina-isaacsim-synthdata \
        --rm -it \
        --gpus all \
        --network host \
        --ipc host \
        -e ACCEPT_EULA=Y \
        -e PRIVACY_CONSENT=Y \
        -e HEADLESS=1 \
        -e ENABLE_CAMERAS=1 \
        -e NUM_SCENES="$num_scenes" \
        -v "${MINA_ROOT}/data_gen:/workspace/data_gen:rw" \
        -v "${MINA_ROOT}/outputs:/output:rw" \
        "${CACHE_VOLS[@]}" \
        mina-isaacsim-synthdata:latest
}

# ── headless eval with video ──────────────────────────────────
cmd_eval() {
    local task="${1:-$TASK}"
    local checkpoint="${2:-/workspace/checkpoints/model.pt}"
    echo "==> Starting headless eval: task=$task checkpoint=$checkpoint ..."
    docker run \
        --name mina-bhl-eval \
        --rm -it \
        --gpus all \
        --network host \
        --ipc host \
        -e ACCEPT_EULA=Y \
        -e PRIVACY_CONSENT=Y \
        -e HEADLESS=1 \
        -e ENABLE_CAMERAS=1 \
        -v "${MINA_ROOT}/checkpoints:/workspace/checkpoints:ro" \
        -v "${MINA_ROOT}/outputs:/workspace/outputs:rw" \
        "${CACHE_VOLS[@]}" \
        mina-bhl-training:latest \
        isaaclab -p scripts/rsl_rl/play.py \
            --task "$task" \
            --num_envs 64 \
            --checkpoint "$checkpoint" \
            --video --video_length 500 \
            --video_interval 1
}

# ── Apptainer (local Singularity testing) ─────────────────────
APPTAINER_DIR="/tmp"

apptainer_bind_args() {
    local cache_base="${HOME}/docker/isaac-sim"
    mkdir -p \
        "$cache_base/cache/kit" "$cache_base/cache/ov" "$cache_base/cache/pip" \
        "$cache_base/cache/glcache" "$cache_base/cache/computecache" \
        "$cache_base/logs" "$cache_base/data"
    echo "-B $cache_base/cache/kit:/isaac-sim/kit/cache:rw \
          -B $cache_base/cache/ov:/root/.cache/ov:rw \
          -B $cache_base/cache/pip:/root/.cache/pip:rw \
          -B $cache_base/cache/glcache:/root/.cache/nvidia/GLCache:rw \
          -B $cache_base/cache/computecache:/root/.nv/ComputeCache:rw \
          -B $cache_base/logs:/root/.nvidia-omniverse/logs:rw \
          -B $cache_base/data:/root/.local/share/ov/data:rw"
}

cmd_apptainer_build() {
    local profile="${1:-mina-bhl-training}"
    local sif_path="$APPTAINER_DIR/${profile}.sif"
    local tar_path="$APPTAINER_DIR/${profile}-docker.tar"

    if [ -d "$sif_path" ]; then
        echo "[INFO] Sandbox already exists at $sif_path — delete it first with: $0 apptainer-clean $profile"
        return 0
    fi

    echo "==> Converting Docker image '$profile:latest' to Apptainer sandbox ..."
    docker save "$profile:latest" -o "$tar_path"
    apptainer build --sandbox "$sif_path" "docker-archive://$tar_path"
    rm -f "$tar_path"
    # Create bind-mount targets that may not exist in the image
    mkdir -p "$sif_path/workspace/logs" "$sif_path/workspace/checkpoints"
    echo "==> Sandbox ready at $sif_path ($(du -sh "$sif_path" | cut -f1))"
}

cmd_apptainer_shell() {
    local profile="${1:-mina-bhl-training}"
    local sif_path="$APPTAINER_DIR/${profile}.sif"
    if [ ! -d "$sif_path" ]; then
        echo "[Error] No sandbox at $sif_path. Run: $0 apptainer-build $profile" >&2
        exit 1
    fi
    echo "==> Opening shell in $sif_path ..."
    apptainer shell \
        $(apptainer_bind_args) \
        --nv --writable --containall "$sif_path"
}

cmd_apptainer_train() {
    local profile="mina-bhl-training"
    local task="${1:-$TASK}"
    local num_envs="${2:-$NUM_ENVS}"
    local sif_path="$APPTAINER_DIR/${profile}.sif"

    if [ ! -d "$sif_path" ]; then
        echo "[Error] No sandbox at $sif_path. Run: $0 apptainer-build" >&2
        exit 1
    fi

    echo "==> Apptainer training: task=$task num_envs=$num_envs ..."
    apptainer exec \
        $(apptainer_bind_args) \
        -B "${MINA_ROOT}/logs:/workspace/logs:rw" \
        -B "${MINA_ROOT}/checkpoints:/workspace/checkpoints:rw" \
        --nv --writable --containall "$sif_path" \
        bash -c "export ISAACLAB_PATH=/workspace/isaaclab && \
                 export HEADLESS=1 && \
                 export ENABLE_CAMERAS=0 && \
                 cd /workspace && \
                 /workspace/isaaclab/isaaclab.sh -p scripts/rsl_rl/train.py \
                   --task $task \
                   --num_envs $num_envs \
                   --headless"
}

cmd_apptainer_clean() {
    local profile="${1:-mina-bhl-training}"
    local sif_path="$APPTAINER_DIR/${profile}.sif"
    if [ -d "$sif_path" ]; then
        echo "==> Removing $sif_path ..."
        rm -rf "$sif_path"
        echo "Done."
    else
        echo "[INFO] No sandbox at $sif_path"
    fi
}

# ── Utils ─────────────────────────────────────────────────────
cmd_list() {
    echo "==> Mina images:"
    docker images | grep "^mina-"
    echo ""
    echo "==> Running mina containers:"
    docker ps --filter "name=mina-" --format "table {{.Names}}\t{{.Status}}\t{{.Image}}"
}

cmd_stop() {
    echo "==> Stopping all mina containers ..."
    docker ps -q --filter "name=mina-" | xargs -r docker stop
}

cmd_clean() {
    echo "==> Removing all mina containers and images ..."
    docker ps -q --filter "name=mina-" | xargs -r docker rm -f
    docker images -q "mina-*" | xargs -r docker rmi -f
    echo "Done."
}

# ── Dispatch ─────────────────────────────────────────────────
case "${1:-help}" in
    ngc-login)      cmd_ngc_login ;;
    pull-base)      cmd_pull_base ;;
    build-base)     cmd_build_base ;;
    build-training) cmd_build_training ;;
    build-streaming)cmd_build_streaming ;;
    build-synthdata)cmd_build_synthdata ;;
    build-deploy)   cmd_build_deploy ;;
    build-all)      cmd_build_all ;;
    dev)            cmd_dev ;;
    train)          shift; cmd_train "$@" ;;
    stream)         shift; cmd_stream "$@" ;;
    synthdata)      shift; cmd_synthdata "$@" ;;
    eval)           shift; cmd_eval "$@" ;;
    apptainer-build)  shift; cmd_apptainer_build "$@" ;;
    apptainer-shell)  shift; cmd_apptainer_shell "$@" ;;
    apptainer-train)  shift; cmd_apptainer_train "$@" ;;
    apptainer-clean)  shift; cmd_apptainer_clean "$@" ;;
    list)           cmd_list ;;
    stop)           cmd_stop ;;
    clean)          cmd_clean ;;
    help|--help|-h) print_help ;;
    *)              echo "Unknown command: $1"; print_help; exit 1 ;;
esac
