#!/usr/bin/env bash

echo "(run_singularity.sh): Called on compute node from current isaaclab directory $1 with container profile $2 and arguments ${@:3}"

#==
# Helper functions
#==

setup_directories() {
    # Check and create directories
    for dir in \
        "${CLUSTER_ISAAC_SIM_CACHE_DIR}/cache/kit" \
        "${CLUSTER_ISAAC_SIM_CACHE_DIR}/cache/ov" \
        "${CLUSTER_ISAAC_SIM_CACHE_DIR}/cache/pip" \
        "${CLUSTER_ISAAC_SIM_CACHE_DIR}/cache/glcache" \
        "${CLUSTER_ISAAC_SIM_CACHE_DIR}/cache/computecache" \
        "${CLUSTER_ISAAC_SIM_CACHE_DIR}/logs" \
        "${CLUSTER_ISAAC_SIM_CACHE_DIR}/data" \
        "${CLUSTER_ISAAC_SIM_CACHE_DIR}/documents"; do
        if [ ! -d "$dir" ]; then
            mkdir -p "$dir"
            echo "Created directory: $dir"
        fi
    done
}


#==
# Main
#==


# get script directory
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"

# load variables to set the Isaac Lab path on the cluster
source $SCRIPT_DIR/.env.cluster

# Container-internal paths (match the nvidia/isaac-lab base image)
DOCKER_ISAACSIM_ROOT_PATH=/isaac-sim
DOCKER_USER_HOME=/root

# make sure that all directories exists in cache directory
setup_directories
# copy all cache files
cp -r $CLUSTER_ISAAC_SIM_CACHE_DIR $TMPDIR

# make sure logs directory exists (in the permanent isaaclab directory)
mkdir -p "$CLUSTER_ISAACLAB_DIR/logs"
touch "$CLUSTER_ISAACLAB_DIR/logs/.keep"

# copy the temporary isaaclab directory with the latest changes to the compute node
cp -r $1 $TMPDIR
# Get the directory name
dir_name=$(basename "$1")

# copy container to the compute node
tar -xf $CLUSTER_SIF_PATH/$2.tar  -C $TMPDIR

# create bind-mount destinations inside the writable sandbox (Singularity
# --writable mode does not auto-create them)
for dest in \
    "$TMPDIR/$2.sif${DOCKER_ISAACSIM_ROOT_PATH}/kit/cache" \
    "$TMPDIR/$2.sif${DOCKER_USER_HOME}/.cache/ov" \
    "$TMPDIR/$2.sif${DOCKER_USER_HOME}/.cache/pip" \
    "$TMPDIR/$2.sif${DOCKER_USER_HOME}/.cache/nvidia/GLCache" \
    "$TMPDIR/$2.sif${DOCKER_USER_HOME}/.nv/ComputeCache" \
    "$TMPDIR/$2.sif${DOCKER_USER_HOME}/.nvidia-omniverse/logs" \
    "$TMPDIR/$2.sif${DOCKER_USER_HOME}/.local/share/ov/data" \
    "$TMPDIR/$2.sif${DOCKER_USER_HOME}/Documents" \
    "$TMPDIR/$2.sif/workspace/source" \
    "$TMPDIR/$2.sif/workspace/scripts" \
    "$TMPDIR/$2.sif/workspace/configs" \
    "$TMPDIR/$2.sif/workspace/logs"; do
    mkdir -p "$dest"
done

# execute command in singularity container
# NOTE: We mount individual Mina directories (source, scripts, configs) so
# that code changes are picked up from the snapshot, while leaving
# /workspace/isaaclab intact (Isaac Lab is installed as editable there).
singularity exec \
    -B $TMPDIR/docker-isaac-sim/cache/kit:${DOCKER_ISAACSIM_ROOT_PATH}/kit/cache:rw \
    -B $TMPDIR/docker-isaac-sim/cache/ov:${DOCKER_USER_HOME}/.cache/ov:rw \
    -B $TMPDIR/docker-isaac-sim/cache/pip:${DOCKER_USER_HOME}/.cache/pip:rw \
    -B $TMPDIR/docker-isaac-sim/cache/glcache:${DOCKER_USER_HOME}/.cache/nvidia/GLCache:rw \
    -B $TMPDIR/docker-isaac-sim/cache/computecache:${DOCKER_USER_HOME}/.nv/ComputeCache:rw \
    -B $TMPDIR/docker-isaac-sim/logs:${DOCKER_USER_HOME}/.nvidia-omniverse/logs:rw \
    -B $TMPDIR/docker-isaac-sim/data:${DOCKER_USER_HOME}/.local/share/ov/data:rw \
    -B $TMPDIR/docker-isaac-sim/documents:${DOCKER_USER_HOME}/Documents:rw \
    -B $TMPDIR/$dir_name/source:/workspace/source:rw \
    -B $TMPDIR/$dir_name/scripts:/workspace/scripts:rw \
    -B $TMPDIR/$dir_name/configs:/workspace/configs:rw \
    -B $CLUSTER_ISAACLAB_DIR/logs:/workspace/logs:rw \
    --nv --writable --containall $TMPDIR/$2.sif \
    bash -c "export ISAACLAB_PATH=/workspace/isaaclab && cd /workspace && /isaac-sim/python.sh ${CLUSTER_PYTHON_EXECUTABLE} ${@:3}"

# copy resulting cache files back to host
rsync -azPv $TMPDIR/docker-isaac-sim $CLUSTER_ISAAC_SIM_CACHE_DIR/..

# if defined, remove the temporary isaaclab directory pushed when the job was submitted
if $REMOVE_CODE_COPY_AFTER_JOB; then
    rm -rf $1
fi

echo "(run_singularity.sh): Return"
