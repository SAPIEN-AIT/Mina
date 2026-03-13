# 1. KILL THE CONTAINER
# Stop and remove the Isaac Sim/Lab container if it exists
docker stop isaac-sim || true
docker rm isaac-sim || true

# 2. BURN THE BLUEPRINTS
# Delete the auto-generated env file in your docker folder
rm -f ./docker/.env.base

# 3. WIPE THE HARD DRIVE DATA
# Remove all cached shaders, logs, and data stored in your home directory
sudo rm -rf ~/docker/isaac-sim

# 4. DISMANTLE THE PLUMBING (The NVIDIA Toolkit)
# Completely uninstall the toolkit and its configurations
sudo apt-get purge -y nvidia-container-toolkit
sudo apt-get autoremove -y

# 5. REVOKE THE WAREHOUSE KEYS
# Delete the NVIDIA repository list and the GPG security keys
sudo rm -f /etc/apt/sources.list.d/nvidia-container-toolkit.list
sudo rm -f /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg

# 6. REBOOT THE DOCKER SERVICE
# Restarts Docker so it forgets it ever had NVIDIA capabilities
sudo systemctl restart docker

echo "------------------------------------------------------"
echo "💀 EVERYTHING IS GONE. Your board is now blank."
echo "👉 To verify: 'nvidia-ctk --version' should return 'command not found'"