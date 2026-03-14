#!/bin/bash
set -e

# 1. CONFIGURATION
# ==========================================
ISAAC_LAB_VERSION="2.3.2"
CONTAINER_NAME="isaac-lab"
PROJECT_DIR="/workspace/isaaclab/source/standalone/mina_project"

# REVERSED ORDER: Install sub-modules first, then the main workspace
PACKAGES_TO_INSTALL=(
    "./source/berkeley_humanoid_lite"
    "./source/berkeley_humanoid_lite_assets"
    "./source/berkeley_humanoid_lite_lowlevel"
    "."
)

cd "$(dirname "$0")"
echo "🚀 Starting Isaac Lab ${ISAAC_LAB_VERSION} Deployment..."

# ==========================================
# 2. PRIVILEGE & USER CHECK
# ==========================================
if [ "$EUID" -ne 0 ]; then
  echo "❌ Error: Please run this script with sudo!"
  exit 1
fi

if [ -z "$SUDO_USER" ]; then
    echo "❌ Error: Could not detect the original user."
    exit 1
fi

ACTUAL_UID=$(id -u "$SUDO_USER")
ACTUAL_GID=$(id -g "$SUDO_USER")
ACTUAL_HOME=$(getent passwd "$SUDO_USER" | cut -d: -f6)

# ==========================================
# 3. NVIDIA TOOLKIT INSTALLATION (If Missing)
# ==========================================
if ! command -v nvidia-ctk &> /dev/null; then
    echo "🔧 Installing NVIDIA Container Toolkit..."
    curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg --yes
    curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | \
      sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | \
      sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list > /dev/null
    sudo apt-get update && sudo apt-get install -y nvidia-container-toolkit
    sudo nvidia-ctk runtime configure --runtime=docker
    sudo systemctl restart docker
fi

# ==========================================
# 4. DIRECTORY & ENV SETUP
# ==========================================
echo "📁 Configuring host cache directories..."
CACHE_BASE="${ACTUAL_HOME}/docker/isaac-sim"
mkdir -p "${CACHE_BASE}"/{cache/kit,cache/ov,cache/pip,cache/glcache,cache/computecache,logs,data,documents}
chown -R "${ACTUAL_UID}:${ACTUAL_GID}" "${CACHE_BASE}"

cat <<EOF > .env.base
ISAAC_LAB_VERSION=${ISAAC_LAB_VERSION}
ACCEPT_EULA=Y
PRIVACY_CONSENT=Y
UID=${ACTUAL_UID}
GID=${ACTUAL_GID}
CACHE_DIR=${CACHE_BASE}
EOF
chown "${ACTUAL_UID}:${ACTUAL_GID}" .env.base

# ==========================================
# 5. LAUNCH CONTAINER
# ==========================================
echo "🔥 Launching container: ${CONTAINER_NAME}..."
docker compose --env-file .env.base up -d

# ==========================================
# 6. POST-DEPLOY PACKAGE REGISTRATION
# ==========================================
echo "📦 Registering Python packages inside the container..."
sleep 5 # Brief pause to let the container filesystem initialize

for pkg in "${PACKAGES_TO_INSTALL[@]}"; do
    echo "   -> Installing: ${pkg}"
    docker exec -u root "${CONTAINER_NAME}" bash -c "cd ${PROJECT_DIR} && /workspace/isaaclab/isaaclab.sh -p -m pip install -e ${pkg} --ignore-requires-python --no-cache-dir --no-deps"
done

echo "------------------------------------------------------"
echo "✅ SUCCESS! Environment is fully deployed and configured."
echo "👉 Start training with:"
echo "   docker exec -u root -it ${CONTAINER_NAME} bash -c 'cd ${PROJECT_DIR} && /workspace/isaaclab/isaaclab.sh -p scripts/rsl_rl/train.py --task Velocity-Berkeley-Humanoid-Lite-v0'"