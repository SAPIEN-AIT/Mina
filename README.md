# Berkeley Humanoid Lite

[![Python](https://img.shields.io/badge/python-3.11-blue.svg)](https://docs.python.org/3/whatsnew/3.11.html)
[![License](https://img.shields.io/badge/license-MIT-yellow.svg)](https://opensource.org/license/mit)
[![License](https://img.shields.io/badge/license-CC%20BY--SA%204.0-orange.svg)](https://creativecommons.org/licenses/by-sa/4.0/)

**[Website](http://lite.berkeley-humanoid.org/)** | **[arXiv](https://arxiv.org/abs/2504.17249)** | **[Paper](https://lite.berkeley-humanoid.org/static/paper/demonstrating-berkeley-humanoid-lite.pdf)** | **[Video](https://youtu.be/dIdJGkMDFl4?si=SRD7HhQQbhM3JCRA)** | **[Documentation](https://berkeley-humanoid-lite.gitbook.io/berkeley-humanoid-lite-docs)** | **[Releases](https://berkeley-humanoid-lite.gitbook.io/docs/releases)**


Berkeley Humanoid Lite is an open-source, sub-$5,000 humanoid robot featuring modular 3D-printed gearboxes and widely available components, designed to democratize and advance humanoid robotics research.

This project is built on the values of open-source, accessibility, and customization, and it's continuously evolving. We welcome your feedback, issues, and pull requests in GitHub or joining our Discord.

## Overview

This repository is the workspace for the Berkeley Humanoid Lite project that contains everything we need, including policy training, sim2sim validation, real-world deployment, motion capture, and teleoperated manipulation controls.

Functionalities are organized into several submodules. We arrange the directory structure following the Isaac Lab convention, where each submodule can be installed as an extension:

- `source/berkeley_humanoid_lite/` contains the IsaacLab environment and task definitions.

- `source/berkeley_humanoid_lite_assets/` contains robot descriptions (URDF, MJCF, and USD) and the script to export these description files from Onshape project.

- `source/berkeley_humanoid_lite_lowlevel/` contains the lowlevel code running on the real robot. Only contents inside this folder is required to deploy to the real robot.

Except a few edge cases, all the commands should be invoked from the root directory of this repository. The entry points of different flows are collected in the `scripts/` directory.


## Getting Started

Please refer to our [Documentation](https://berkeley-humanoid-lite.gitbook.io/docs) to get started with software and hardware setup.

The latest release of CAD model and 3D print files can be accessed from the [Release](https://berkeley-humanoid-lite.gitbook.io/docs/releases) page.

## How to Run / Quick Start

### Docker Deployment

The finalized Docker workflow is driven by `docker/deploy_all.sh`.
This script must be run with `sudo` on the host machine because it installs Docker/NVIDIA plumbing when needed, launches the `isaac-lab` container, and registers the local Berkeley packages into the container as the `root` user.

```bash
cd docker
chmod +x deploy_all.sh
sudo ./deploy_all.sh
```

### Critical Runtime Rules

1. The deployment script installs the local packages inside the container as `root`.
2. Because of that, all interactive training and playback commands must also run as `root` inside the container.
3. Do not use plain `python3` inside the container for Isaac Lab tasks.
4. Always use the Isaac Lab wrapper:

```bash
/workspace/isaaclab/isaaclab.sh -p
```

### Option A: Run Training Directly from the Host (Recommended)

```bash
docker exec -u root -it isaac-lab bash -c 'cd /workspace/isaaclab/source/standalone/mina_project && /workspace/isaaclab/isaaclab.sh -p scripts/rsl_rl/train.py --task Velocity-Berkeley-Humanoid-Lite-v0'
```

### Option B: Run from Inside the Container

Step 1. Enter the container as `root`:

```bash
docker exec -u root -it isaac-lab bash
```

Step 2. Navigate to the project and run training with the Isaac Lab wrapper:

```bash
cd /workspace/isaaclab/source/standalone/mina_project
/workspace/isaaclab/isaaclab.sh -p scripts/rsl_rl/train.py --task Velocity-Berkeley-Humanoid-Lite-v0
```

### Playback

```bash
docker exec -u root -it isaac-lab bash -c 'cd /workspace/isaaclab/source/standalone/mina_project && /workspace/isaaclab/isaaclab.sh -p scripts/rsl_rl/play.py --task Velocity-Berkeley-Humanoid-Lite-v0'
```

## Project Overview (Isaac Lab + RSL-RL)

This workspace is configured to train Berkeley Humanoid Lite policies using Isaac Lab (successor to Isaac Gym) and RSL-RL.
The repository is organized as an Isaac Lab extension, which means the custom robot, assets, and tasks are registered into Isaac Lab through the packages under `source/`.

## Integration Hurdles and Fixes

1. Bash history expansion (`!`) errors
- Symptom: `bash: !': event not found`
- Cause: `!` triggers history expansion inside double quotes in interactive bash.
- Fix: use single quotes around inline Python snippets that include `!`.

2. Isaac Lab API break (`dump_pickle` import)
- Symptom: `ImportError: cannot import name 'dump_pickle' from isaaclab.utils.io`
- Cause: helper APIs changed in newer Isaac Lab versions.
- Fix in `scripts/rsl_rl/train.py`: keep `dump_yaml` import, remove failing `dump_pickle` import, and provide a local `dump_pickle` implementation using Python stdlib `pickle`.

3. Docker log permission failures in `/tmp`
- Symptom: permission denied while simulator writes internal logs.
- Cause: internal Isaac Sim/Lab logs may target directories with restricted permissions in some container setups.
- Fix in `scripts/rsl_rl/train.py`: set `env_cfg.sim.log_dir` to `logs/rsl_rl/<experiment>/isaaclab`.

4. Module registration and Python-version mismatch
- Symptoms: `ModuleNotFoundError: berkeley_humanoid_lite`, plus Python requirement mismatch.
- Cause: package not installed into active environment and metadata constraints differ across image/runtime versions.
- Fix: install the workspace in editable mode when needed:

```bash
pip install -e . --ignore-requires-python
```

5. Omniverse EULA gating
- Symptom: run blocked at startup waiting for EULA acceptance.
- Fix: run training with:

```bash
OMNI_KIT_ACCEPT_EULA=Y ACCEPT_EULA=Y UV_PROJECT_ENVIRONMENT=.venv \
uv run ./scripts/rsl_rl/train.py --task Velocity-Berkeley-Humanoid-Lite-v0 --headless
```

## Valid Task IDs

- `Velocity-Berkeley-Humanoid-Lite-v0` (full humanoid)
- `Velocity-Berkeley-Humanoid-Lite-Biped-v0` (biped variant)


## Contributing

We wholeheartedly welcome contributions from the community to make this robot platform more mature and useful for everyone. We appreciate any kind of contributions, including bug reports, feature requests, or code contributions.

Also, please reach out to us to tell us about your projects and how you are using this robot platform. We would love to feature your work on our website and social media.

## License

The code in this repository is licensed under [MIT License](https://opensource.org/license/mit). See the [LICENSE](LICENSE) file for details.

Other assets are under [Creative Commons Attribution-ShareAlike 4.0 International <img style="height:22px!important;margin-left:3px;vertical-align:text-bottom;" src="https://mirrors.creativecommons.org/presskit/icons/cc.svg?ref=chooser-v1" alt=""><img style="height:22px!important;margin-left:3px;vertical-align:text-bottom;" src="https://mirrors.creativecommons.org/presskit/icons/by.svg?ref=chooser-v1" alt=""><img style="height:22px!important;margin-left:3px;vertical-align:text-bottom;" src="https://mirrors.creativecommons.org/presskit/icons/sa.svg?ref=chooser-v1" alt="">](https://creativecommons.org/licenses/by-sa/4.0).


## Citation

If you find this code useful, we would appreciate if you would cite our paper:

```
@article{chi2025demonstrating,
  title={Demonstrating Berkeley Humanoid Lite: An Open-source, Accessible, and Customizable 3D-printed Humanoid Robot},
  author={Yufeng Chi and Qiayuan Liao and Junfeng Long and Xiaoyu Huang and Sophia Shao and Borivoje Nikolic and Zhongyu Li and Koushil Sreenath},
  year={2025},
  eprint={2504.17249},
  archivePrefix={arXiv},
  primaryClass={cs.RO},
  url={https://arxiv.org/abs/2504.17249}, 
}
```
