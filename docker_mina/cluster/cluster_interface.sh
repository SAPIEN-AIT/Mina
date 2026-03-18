#!/usr/bin/env bash

#==
# Configurations
#==

# Exits if error occurs
set -e

# Set tab-spaces
tabs 4

# get script directory
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"

#==
# Functions
#==
# Function to display warnings in red
display_warning() {
    echo -e "\033[31mWARNING: $1\033[0m"
}

# Helper function to compare version numbers
version_gte() {
    # Returns 0 if the first version is greater than or equal to the second, otherwise 1
    [ "$(printf '%s\n' "$1" "$2" | sort -V | head -n 1)" == "$2" ]
}

# Function to check docker versions
check_docker_version() {
    # check if docker is installed
    if ! command -v docker &> /dev/null; then
        echo "[Error] Docker is not installed! Please check the 'Docker Guide' for instruction." >&2;
        exit 1
    fi
    # Retrieve Docker version
    docker_version=$(docker --version | awk '{ print $3 }')
    apptainer_version=$(apptainer --version | awk '{ print $3 }')

    # Check if Docker version is exactly 24.0.7 or Apptainer version is exactly 1.2.5
    if [ "$docker_version" = "24.0.7" ] && [ "$apptainer_version" = "1.2.5" ]; then
        echo "[INFO]: Docker version ${docker_version} and Apptainer version ${apptainer_version} are tested and compatible."

    # Check if Docker version is >= 27.0.0 and Apptainer version is >= 1.3.4
    elif version_gte "$docker_version" "27.0.0" && version_gte "$apptainer_version" "1.3.4"; then
        echo "[INFO]: Docker version ${docker_version} and Apptainer version ${apptainer_version} are tested and compatible."

    # Else, display a warning for non-tested versions
    else
        display_warning "Docker version ${docker_version} and Apptainer version ${apptainer_version} are non-tested versions. There could be issues, please try to update them. More info: https://isaac-sim.github.io/IsaacLab/source/deployment/cluster.html"
    fi
}

# Checks if a docker image exists, otherwise prints warning and exists
check_image_exists() {
    image_name="$1"
    if ! docker image inspect $image_name &> /dev/null; then
        echo "[Error] The '$image_name' image does not exist!" >&2;
        echo "[Error] You might be able to build it with /IsaacLab/docker/container.py." >&2;
        exit 1
    fi
}

# Check if the singularity image exists on the remote host, otherwise print warning and exit
check_singularity_image_exists() {
    image_name="$1"
    if ! ssh "$CLUSTER_LOGIN" "[ -f $CLUSTER_SIF_PATH/$image_name.tar ]"; then
        echo "[Error] The '$image_name' image does not exist on the remote host $CLUSTER_LOGIN!" >&2;
        exit 1
    fi
}

submit_job() {

    echo "[INFO] Arguments passed to job script ${@}"

    case $CLUSTER_JOB_SCHEDULER in
        "SLURM")
            job_script_file=submit_job_slurm.sh
            ;;
        "PBS")
            job_script_file=submit_job_pbs.sh
            ;;
        *)
            echo "[ERROR] Unsupported job scheduler specified: '$CLUSTER_JOB_SCHEDULER'. Supported options are: ['SLURM', 'PBS']"
            exit 1
            ;;
    esac

    ssh $CLUSTER_LOGIN "cd $CLUSTER_ISAACLAB_DIR && bash $CLUSTER_ISAACLAB_DIR/docker_mina/cluster/$job_script_file \"$CLUSTER_ISAACLAB_DIR\" \"$profile\" ${@}"
}

#==
# Main
#==

#!/bin/bash

help() {
    echo -e "\nusage: $(basename "$0") [-h] <command> [<profile>] [<job_args>...] -- Utility for interfacing between IsaacLab and compute clusters."
    echo -e "\noptions:"
    echo -e "  -h              Display this help message."
    echo -e "\ncommands:"
    echo -e "  push [<profile>]              Push the docker image to the cluster."
    echo -e "  job [<profile>] [<job_args>]  Submit a job to the cluster."
    echo -e "\nwhere:"
    echo -e "  <profile>  is the optional container image name. Defaults to 'isaac-lab-base'."
    echo -e "  <job_args> are optional arguments specific to the job command."
    echo -e "\n" >&2
}

# Parse options
while getopts ":h" opt; do
    case ${opt} in
        h )
            help
            exit 0
            ;;
        \? )
            echo "Invalid option: -$OPTARG" >&2
            help
            exit 1
            ;;
    esac
done
shift $((OPTIND -1))

# Check for command
if [ $# -lt 1 ]; then
    echo "Error: Command is required." >&2
    help
    exit 1
fi

command=$1
shift
profile="isaac-lab-base"

case $command in
    push)
        if [ $# -gt 1 ]; then
            echo "Error: Too many arguments for push command." >&2
            help
            exit 1
        fi
        [ $# -eq 1 ] && profile=$1
        echo "Executing push command"
        [ -n "$profile" ] && echo "Using profile: $profile"
        # Check if Docker image exists
        check_image_exists $profile:latest
        # source env file to get cluster login and path information
        source $SCRIPT_DIR/.env.cluster
        # make sure target directory exists on cluster
        ssh $CLUSTER_LOGIN "mkdir -p $CLUSTER_SIF_PATH"
        # skip upload if the docker tar.gz already exists on the cluster
        if ssh $CLUSTER_LOGIN "[ -f $CLUSTER_SIF_PATH/$profile-docker.tar.gz ]"; then
            echo "[INFO] Docker archive already exists on cluster, skipping upload."
        else
            # stream docker image directly to cluster (no local disk usage)
            echo "[INFO] Streaming Docker image to cluster..."
            docker save $profile:latest | gzip | ssh $CLUSTER_LOGIN "cat > $CLUSTER_SIF_PATH/$profile-docker.tar.gz"
        fi
        # build singularity sandbox and tar it on the cluster, then clean up
        echo "[INFO] Building Singularity image on cluster (this may take a while)..."
        ssh $CLUSTER_LOGIN "cd $CLUSTER_SIF_PATH \
            && rm -rf $profile.sif $profile.tar \
            && gunzip $profile-docker.tar.gz \
            && APPTAINER_NOHTTPS=1 apptainer build --sandbox --fakeroot $profile.sif docker-archive://$CLUSTER_SIF_PATH/$profile-docker.tar \
            && tar -cf $profile.tar $profile.sif \
            && rm -rf $profile.sif $profile-docker.tar"
        echo "[INFO] Done. Image available at $CLUSTER_SIF_PATH/$profile.tar"
        ;;
    job)
        if [ $# -ge 1 ]; then
            profile=$1
            shift
        fi
        job_args="$@"
        echo "[INFO] Executing job command"
        [ -n "$profile" ] && echo -e "\tUsing profile: $profile"
        [ -n "$job_args" ] && echo -e "\tJob arguments: $job_args"
        source $SCRIPT_DIR/.env.cluster
        # Get current date and time
        current_datetime=$(date +"%Y%m%d_%H%M%S")
        # Append current date and time to CLUSTER_ISAACLAB_DIR
        CLUSTER_ISAACLAB_DIR="${CLUSTER_ISAACLAB_DIR}_${current_datetime}"
        # Check if singularity image exists on the remote host
        check_singularity_image_exists $profile
        # make sure target directory exists
        ssh $CLUSTER_LOGIN "mkdir -p $CLUSTER_ISAACLAB_DIR"
        # Sync Isaac Lab code
        echo "[INFO] Syncing Isaac Lab code..."
        rsync -rh  --exclude="*.git*" --filter=':- .dockerignore'  /$SCRIPT_DIR/../.. $CLUSTER_LOGIN:$CLUSTER_ISAACLAB_DIR
        # execute job script
        echo "[INFO] Executing job script..."
        # check whether the second argument is a profile or a job argument
        submit_job $job_args
        ;;
    *)
        echo "Error: Invalid command: $command" >&2
        help
        exit 1
        ;;
esac
