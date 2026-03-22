#!/usr/bin/env bash
# ============================================================
# run.sh — Mina container command centre
# Usage: ./docker_mina/run.sh <command> [options]
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
    "${MINA_ROOT}/checkpoints"

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
  ./docker_mina/run.sh ngc-login          Authenticate with NGC (required once)
  ./docker_mina/run.sh pull-base          Pull base images from NGC

BUILD (run once, then after code changes)
  ./docker_mina/run.sh build-base         Build mina-isaaclab-base
  ./docker_mina/run.sh build-training     Build mina-bhl-training
  ./docker_mina/run.sh build-all          Build base + training

RUN
  ./docker_mina/run.sh dev                Enter dev container (live code sync)
  ./docker_mina/run.sh train [task] [n]   Headless training (task, num_envs)
  ./docker_mina/run.sh stream [ckpt]      Livestream policy (checkpoint path)
  ./docker_mina/run.sh eval [task] [ckpt] Headless evaluation with video
  ./docker_mina/run.sh sim2sim [config]   Standalone Isaac Sim sim2sim (gamepad enabled)

ROS2 (Jazzy — policy inference + teleop, uses mina_desktop:jazzy)
  ./docker_mina/run.sh ros-policy [config]         Run ROS2 policy node (default: configs/policy_latest.yaml)
  ./docker_mina/run.sh ros-gamepad [--verbose]      Run gamepad → /cmd_vel bridge
  ./docker_mina/run.sh ros-shell                   Interactive shell in ROS2 container

APPTAINER (local Singularity testing)
  ./docker_mina/run.sh apptainer-build [profile]   Convert Docker image to Apptainer sandbox
  ./docker_mina/run.sh apptainer-shell [profile]   Interactive shell in Apptainer sandbox
  ./docker_mina/run.sh apptainer-train [task] [n]  Headless training via Apptainer
  ./docker_mina/run.sh apptainer-clean [profile]   Remove local Apptainer sandbox

UTILS
  ./docker_mina/run.sh list               List all mina containers + images
  ./docker_mina/run.sh stop               Stop all running mina containers
  ./docker_mina/run.sh clean              Remove all mina containers + images

EOF
}

# ── Commands ─────────────────────────────────────────────────

cmd_ngc_login() {
    ngc_login
}

cmd_pull_base() {
    echo "==> Pulling nvcr.io/nvidia/isaac-lab:2.3.2 ..."
    docker pull nvcr.io/nvidia/isaac-lab:2.3.2
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

cmd_build_all() {
    cmd_build_base
    cmd_build_training
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
        ${WANDB_API_KEY:+-e WANDB_API_KEY="${WANDB_API_KEY}"} \
        ${WANDB_USERNAME:+-e WANDB_USERNAME="${WANDB_USERNAME}"} \
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
        ${WANDB_API_KEY:+-e WANDB_API_KEY="${WANDB_API_KEY}"} \
        ${WANDB_USERNAME:+-e WANDB_USERNAME="${WANDB_USERNAME}"} \
        "${CACHE_VOLS[@]}" \
        mina-bhl-training:latest
}

# ── mina-bhl-streaming — WebRTC visualization ────────────────
cmd_stream() {
    local extra_args=""
    if [ -n "${1:-}" ]; then
        extra_args="--load_run ${1} --checkpoint ${2:-model_*.pt}"
    fi
    echo "==> Starting streaming (mina-bhl-training + play.py) ..."
    echo "    Connect via Isaac Sim Streaming Client → ${PUBLIC_IP}"
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
        -e FASTRTPS_DEFAULT_PROFILES_FILE=/workspace/source/ros/fastdds.xml \
        -v "${MINA_ROOT}/source/ros/fastdds.xml:/workspace/source/ros/fastdds.xml:ro" \
        -v "${MINA_ROOT}/logs:/workspace/logs:rw" \
        -v "${MINA_ROOT}/checkpoints:/workspace/checkpoints:ro" \
        "${CACHE_VOLS[@]}" \
        mina-bhl-training:latest \
        bash -c "isaaclab -p scripts/rsl_rl/play.py \
            --task ${TASK} \
            --num_envs 16 \
            ${extra_args}"
}

# ── headless eval with video ──────────────────────────────────
cmd_eval() {
    local task="${1:-$TASK}"
    local extra_args=""
    if [ -n "${2:-}" ]; then
        extra_args="--load_run ${2} --checkpoint ${3:-model_*.pt}"
    fi
    echo "==> Starting headless eval: task=$task ..."
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
        -v "${MINA_ROOT}/logs:/workspace/logs:rw" \
        -v "${MINA_ROOT}/checkpoints:/workspace/checkpoints:ro" \
        ${WANDB_API_KEY:+-e WANDB_API_KEY="${WANDB_API_KEY}"} \
        ${WANDB_USERNAME:+-e WANDB_USERNAME="${WANDB_USERNAME}"} \
        "${CACHE_VOLS[@]}" \
        mina-bhl-training:latest \
        bash -c "isaaclab -p scripts/rsl_rl/play.py \
            --task $task \
            --num_envs 64 \
            --video --video_length 500 \
            ${extra_args}"
}

# ── Standalone Isaac Sim sim2sim ─────────────────────────────
cmd_sim2sim() {
    local config="${1:-configs/policy_latest.yaml}"
    echo "==> Starting sim2sim (mina-bhl-training + gamepad) ..."
    echo "    Config: $config"
    echo "    Connect via Isaac Sim Streaming Client → ${PUBLIC_IP}"
    docker run \
        --name mina-sim2sim \
        --rm -it \
        --gpus all \
        --network host \
        --ipc host \
        --privileged \
        -e ACCEPT_EULA=Y \
        -e PRIVACY_CONSENT=Y \
        -e LIVESTREAM=2 \
        -e ENABLE_CAMERAS=1 \
        -e ISAACLAB_PATH=/workspace/isaaclab \
        -e PYTHONPATH="/workspace/source/berkeley_humanoid_lite_lowlevel:/workspace/source/berkeley_humanoid_lite:/workspace/source/berkeley_humanoid_lite_assets" \
        -v "${MINA_ROOT}/source:/workspace/source:rw" \
        -v "${MINA_ROOT}/scripts:/workspace/scripts:rw" \
        -v "${MINA_ROOT}/configs:/workspace/configs:ro" \
        -v "${MINA_ROOT}/checkpoints:/workspace/checkpoints:ro" \
        -v /dev/input:/dev/input:ro \
        "${CACHE_VOLS[@]}" \
        mina-bhl-training:latest \
        bash -c "isaaclab -p -m pip install -q onnxruntime omegaconf inputs && \
                 isaaclab -p scripts/sim2sim/play_isaacsim.py --config $config"
}

# ── ROS2 (Jazzy — policy inference + teleop) ──────────────────
ROS_IMAGE="mina_desktop:jazzy"
FASTDDS_XML="/home/mina/Mina/source/ros/fastdds.xml"
ROS_DDS_ENV=(
    "-e" "RMW_IMPLEMENTATION=rmw_fastrtps_cpp"
    "-e" "FASTRTPS_DEFAULT_PROFILES_FILE=${FASTDDS_XML}"
)

cmd_ros_policy() {
    local config="${1:-/home/mina/Mina/configs/policy_latest.yaml}"
    local host_config="${MINA_ROOT}/${config#/home/mina/Mina/}"
    mkdir -p "${MINA_ROOT}/debug"

    # Check that the ONNX referenced in the config exists
    if [ -f "$host_config" ]; then
        local onnx_path
        onnx_path="$(grep '^policy_checkpoint_path:' "$host_config" | awk '{print $2}')"
        # Convert container path to host path (handles /workspace/ and /home/mina/Mina/ prefixes)
        local rel_path="${onnx_path#/workspace/}"
        rel_path="${rel_path#/home/mina/Mina/}"
        local host_onnx="${MINA_ROOT}/${rel_path}"
        if [ -n "$onnx_path" ] && [ ! -f "$host_onnx" ]; then
            echo "[Error] ONNX not found: $onnx_path (checked $host_onnx)" >&2
            echo "        Re-run play.py to export a fresh policy and regenerate the config:" >&2
            echo "        ./docker_mina/run.sh eval $TASK" >&2
            exit 1
        fi
    fi

    echo "==> Starting ROS2 policy node: config=$config ..."
    docker run \
        --name mina-ros-policy \
        --rm -it \
        --network host \
        "${ROS_DDS_ENV[@]}" \
        -v "${MINA_ROOT}:/home/mina/Mina:rw" \
        -v "${MINA_ROOT}/logs:/workspace/logs:ro" \
        -v "${MINA_ROOT}/checkpoints:/workspace/checkpoints:ro" \
        -w /home/mina/Mina \
        "$ROS_IMAGE" \
        python3 /home/mina/Mina/source/ros/play_isaacsim.py --config "$config"
}

cmd_ros_gamepad() {
    echo "==> Starting ROS2 gamepad teleop ..."
    docker run \
        --name mina-ros-gamepad \
        --rm -it \
        --network host \
        --privileged \
        "${ROS_DDS_ENV[@]}" \
        -v /dev/input:/dev/input:ro \
        -v /run/udev:/run/udev:ro \
        -v "${MINA_ROOT}:/home/mina/Mina:rw" \
        "$ROS_IMAGE" \
        python3 /home/mina/Mina/source/ros/gamepad_teleop.py "$@"
}

cmd_ros_shell() {
    echo "==> Opening ROS2 Jazzy shell ..."
    docker run \
        --name mina-ros-shell \
        --rm -it \
        --network host \
        "${ROS_DDS_ENV[@]}" \
        -v "${MINA_ROOT}:/home/mina/Mina:rw" \
        "$ROS_IMAGE" \
        bash
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
    build-all)      cmd_build_all ;;
    dev)            cmd_dev ;;
    train)          shift; cmd_train "$@" ;;
    stream)         shift; cmd_stream "$@" ;;
    eval)           shift; cmd_eval "$@" ;;
    sim2sim)        shift; cmd_sim2sim "$@" ;;
    ros-policy)     shift; cmd_ros_policy "$@" ;;
    ros-gamepad)    shift; cmd_ros_gamepad "$@" ;;
    ros-shell)      cmd_ros_shell ;;
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
