# Bridge Porting: Clean ROS Architecture

## Mina ROS Bridge Ported into `mina-ros-env`

---

## Executive Summary

This document records the port of the ROS-side bridge code from `Mina/source/ros` into the standalone ROS2 workspace package `mina-ros-env/src/mina_simulation`. The objective was not just to copy scripts, but to make the bridge runnable as a clean ROS package with the same intended behavior: a teleop node publishing `/cmd_vel`, and a policy node subscribing to `/cmd_vel`, `/joint_states`, and `/imu` while publishing `/joint_command`.

The work uncovered three separate classes of problems:

- missing code dependencies that existed only inside the Mina source tree
- missing packaged runtime assets such as the default YAML config and ONNX checkpoint
- ROS workspace integration problems, including a broken install rule in `mina_description` and an unnecessary runtime dependency on `omegaconf`

The final result is a clean `mina_simulation` ROS package that builds successfully, installs the correct executables, resolves its packaged config/checkpoint correctly, and passes smoke tests through `ros2 run`.

What looks simple on paper turned out to require cleaning up assumptions that were invisible in the original Mina source-tree layout. The original scripts were not actually standalone ROS nodes. They relied on helper modules living elsewhere in the repository, on runtime assets that were not packaged, and on a workspace dependency package whose install rules were already broken. The port only became complete once the code, assets, and package boundaries were all made explicit.

---

## Starting Point

### What existed originally

Inside `Mina/source/ros` there were two ROS-facing scripts:

- `gamepad_teleop.py`
- `play_isaacsim.py`

These were written assuming they lived inside the Mina monorepo, where they could import helper modules from the Berkeley Humanoid Lite low-level policy package.

### What the target workspace looked like

Inside `mina-ros-env/src/mina_simulation` there was only a ROS package skeleton:

- `CMakeLists.txt`
- `package.xml`
- `launch/isaac_sim.launch.py`

It did not yet contain:

- the teleop node
- the policy node
- the helper modules those nodes import
- the default policy YAML
- the default ONNX checkpoint

### What “working exactly as intended” meant

For this port, “working” meant:

- the nodes must live fully inside the ROS workspace package
- they must be installable and runnable with `ros2 run`
- the policy node must be able to find its default config in the installed package
- the default config must be able to find its checkpoint in the installed package
- only the actual ROS nodes should appear as ROS executables

That last point mattered more than it seems. A ROS package can easily look functional while still being badly structured. If helper modules appear as runnable ROS programs, or if the package only works when launched from a particular directory, the package is still coupled to the original repository layout. The goal here was a package that behaves correctly when built, installed, and executed through the normal ROS tooling.

---

## Target Architecture

The goal was to convert an in-repo, source-tree-coupled layout into a clean package-local ROS architecture:

```text
mina-ros-env/src/mina_simulation/
├── CMakeLists.txt
├── package.xml
├── launch/
├── config/
│   ├── policy_humanoid.yaml
│   └── checkpoints/
│       └── policy_humanoid.onnx
└── scripts/
    ├── gamepad.py
    ├── rl_controller.py
    ├── gamepad_teleop.py
    └── play_isaacsim.py
```

The idea is simple:

- `gamepad_teleop.py` reads an Xbox controller and publishes `Twist` on `/cmd_vel`
- `play_isaacsim.py` runs the policy loop and publishes `JointState` targets on `/joint_command`
- `gamepad.py` and `rl_controller.py` are internal helper modules installed alongside the nodes
- the package carries its own config and checkpoint so installed runs do not depend on the Mina source tree

The intended runtime architecture is:

```text
┌───────────────────────────────────────┐
│ gamepad_teleop.py                     │
│ reads controller input                │
│ publishes /cmd_vel                    │
└───────────────────┬───────────────────┘
                    │
                    ▼
┌───────────────────────────────────────┐
│ play_isaacsim.py                      │
│ subscribes: /cmd_vel                  │
│ subscribes: /joint_states             │
│ subscribes: /imu                      │
│ runs RL policy                        │
│ publishes /joint_command              │
└───────────────────┬───────────────────┘
                    │
                    ▼
┌───────────────────────────────────────┐
│ Isaac Sim / ROS bridge                │
│ consumes /joint_command               │
│ produces /joint_states and /imu       │
└───────────────────────────────────────┘
```

That is the clean ROS architecture the port was aiming for: teleop isolated from policy logic, policy isolated from repository-specific helpers, and runtime assets shipped with the ROS package itself.

---

## Phase 1: Porting the Two ROS Nodes

### `gamepad_teleop.py`

The teleop node was ported into `mina_simulation/scripts/gamepad_teleop.py`. Its job is straightforward:

- connect to the gamepad
- read velocity commands
- publish them as `geometry_msgs/Twist`

The original code depended on `Se2Gamepad`, which did not exist in the ROS workspace package. That dependency had to be vendored locally.

This was the first sign that the code was not simply “move two files and rebuild.” The script itself looked self-contained, but its behavior depended on a policy-side helper that only existed deeper in the Mina source tree.

### `play_isaacsim.py`

The policy node was ported into `mina_simulation/scripts/play_isaacsim.py`. It preserves the same core behavior:

- subscribe to `/cmd_vel`
- subscribe to `/joint_states`
- subscribe to `/imu`
- build the observation vector expected by the policy
- run the policy via `RlController`
- publish `JointState` commands to `/joint_command`

This script also depended on code that only existed in Mina’s low-level policy package.

That meant the real porting task was not just moving entrypoints. It was extracting the minimum policy runtime needed for those entrypoints to work outside the original repository structure.

---

## Phase 2: Porting Hidden Dependencies

The original ROS scripts were not truly standalone. They imported two helper modules from elsewhere in the Mina source tree:

- `Se2Gamepad`
- `RlController`

To preserve the intended behavior without forcing `mina-ros-env` to import code from another repository layout, those helpers were copied into the package as local modules:

- `mina_simulation/scripts/gamepad.py`
- `mina_simulation/scripts/rl_controller.py`

This was necessary because the ROS workspace must remain self-contained once built and installed.

This is the key architectural cleanup of the whole effort. Before this step, the ROS package was only pretending to be independent. After this step, the package actually owned the code it needed to run.

---

## Phase 3: Packaging the Runtime Assets

The next blocker was that `play_isaacsim.py` assumes a default policy YAML and a default checkpoint exist relative to the runtime package.

Those files were copied into the ROS package:

- `config/policy_humanoid.yaml`
- `config/checkpoints/policy_humanoid.onnx`

Without these two files, the installed node could start only if the user manually pointed it at assets outside the package. That would defeat the purpose of a clean ROS package deployment.

This phase mattered because it removed another hidden assumption in the old setup: that the policy node would always be launched from a working directory that already had access to Mina’s config and checkpoint files. Installed ROS nodes should not depend on that.

---

## Phase 4: Fixing Build and Install Rules

### Installing only the real executables

At first, all Python files under `scripts/` were installed as executable programs. That caused helper modules to show up as ROS executables:

- `gamepad.py`
- `rl_controller.py`

That is the wrong ROS package surface. Only these should be executable:

- `gamepad_teleop.py`
- `play_isaacsim.py`

The fix was to split installation rules in `mina_simulation/CMakeLists.txt`:

- install node entrypoints with `install(PROGRAMS ...)`
- install helper modules with `install(FILES ...)`

### Installing config conditionally

The package was also updated to install `launch/` always, and `config/` and `worlds/` only when present. This prevents future install-time breakage from assuming optional directories always exist.

By this point the package structure was much closer to a real ROS package: clear entrypoints, clear internal modules, and a clean installed resource layout.

---

## Phase 5: The `mina_description` Build Failure

The first full workspace build did not fail inside `mina_simulation` at all. It failed in `mina_description`.

### Problem

`mina_description/CMakeLists.txt` tried to install a directory called `config/` unconditionally:

```cmake
install(
  DIRECTORY urdf meshes launch rviz config
  DESTINATION share/${PROJECT_NAME}/
)
```

But the package did not actually contain a `config/` directory.

### Result

The dependency chain failed before `mina_simulation` could be fully validated.

### Fix

The install rule was rewritten so `urdf`, `meshes`, `launch`, and `rviz` are always installed, while `config` is installed only if it exists.

That removed the unrelated packaging fault and allowed the dependency-aware build to complete.

This was an important debugging moment because it showed that validating the port required building through dependencies, not just checking the edited package in isolation. The port itself was not enough; the surrounding workspace had to be coherent too.

---

## Phase 6: Runtime Failure in the Installed Policy Node

Once the packages built, the next step was smoke testing the installed nodes through `ros2 run`.

### What passed immediately

`gamepad_teleop.py --help` worked.

### What failed

`play_isaacsim.py --help` failed immediately with:

```text
ModuleNotFoundError: No module named 'omegaconf'
```

### Why this happened

The original Mina code used `OmegaConf` to load the policy YAML. In the ROS workspace runtime environment, `omegaconf` was not available.

Even worse, this failure happened before the node did any useful work, so the installed package could not be considered robust.

This is exactly the kind of issue that only appears once a package is exercised through `ros2 run`. Source-tree execution can hide missing runtime dependencies for a long time.

---

## Phase 7: Cleaning the Runtime Dependency Chain

### Removing the hard `omegaconf` dependency

The policy node was changed to load its YAML using `PyYAML`, then recursively convert dictionaries into a `SimpleNamespace` structure so the rest of the code could keep the same attribute-style access.

This preserved the code path while removing a nonessential dependency.

### Resolving packaged config and checkpoint paths correctly

The original default config path was `./configs/policy_humanoid.yaml`, which works only in a specific source-tree working directory.

That is not acceptable for installed ROS execution.

The fix was:

- if the user-provided config path exists, use it
- otherwise, when the default path is requested, fall back to `get_package_share_directory("mina_simulation")/config/policy_humanoid.yaml`
- if `policy_checkpoint_path` inside the YAML is relative, resolve it relative to the loaded config file

This made the installed package self-contained.

### Making heavyweight imports lazy

`rl_controller.py` originally imported `torch` and `onnxruntime` at module import time.

That creates two problems:

- ONNX use should not fail early just because Torch is not installed
- a module-level import makes the whole script fragile before any policy selection logic runs

The fix was to lazy-import:

- `torch` only inside the Torch policy path
- `onnxruntime` only inside the ONNX policy path

This matches the actual runtime intent.

### Ensuring debug output does not fail on missing directories

`play_isaacsim.py` writes debug logs. The code was updated to create `debug/` before opening those files.

Once these changes were in place, the policy node no longer depended on a very specific execution context. That was the final step from “ported source files” to “clean ROS runtime behavior.”

---

## Final Installed Behavior

After the packaging and runtime fixes, the installed package had the correct visible executables:

- `mina_simulation gamepad_teleop.py`
- `mina_simulation play_isaacsim.py`

The helper modules no longer appear as standalone ROS executables.

The policy node also now resolves its config and checkpoint from the installed package layout rather than assuming it is being run from the Mina repository root.

In other words, the package now behaves like a ROS package should: it exposes the right entrypoints, owns the files it needs, and can be executed through standard ROS commands without special handling.

---

## Problems Encountered and Fixes Applied

| Problem | Root Cause | Fix |
|---|---|---|
| ROS scripts depended on nonlocal modules | `gamepad_teleop.py` and `play_isaacsim.py` imported code outside the ROS package | Vendored `gamepad.py` and `rl_controller.py` into `mina_simulation/scripts` |
| Policy assets missing from ROS package | `play_isaacsim.py` needed YAML and ONNX files | Copied `policy_humanoid.yaml` and `policy_humanoid.onnx` into package `config/` |
| Wrong ROS executable surface | Helper modules were installed as programs | Install nodes with `PROGRAMS`, helpers with `FILES` |
| `mina_description` blocked the build | It installed a non-existent `config/` directory | Made `config` install conditional |
| Installed policy node crashed on startup | Hard dependency on `omegaconf` | Replaced config loading with `PyYAML` + namespace conversion |
| Installed node could not rely on source-tree cwd | Default config path was relative to a specific repo layout | Added package-share fallback and relative checkpoint resolution |
| Potential import fragility for policy backends | `torch` and `onnxruntime` imported at module load time | Changed to lazy imports |

---

## Validation Steps

### 1. Rebuild the dependency chain

```bash
cd /home/alex/dev/mina-ros-env
source /opt/ros/humble/setup.bash
colcon build --packages-up-to mina_simulation --event-handlers console_cohesion+
```

This verifies both:

- `mina_description`
- `mina_simulation`

### 2. Source the built workspace

```bash
cd /home/alex/dev/mina-ros-env
source /opt/ros/humble/setup.bash
source install/setup.bash
```

### 3. Verify the installed ROS executables

```bash
ros2 pkg executables mina_simulation
```

Expected result:

```text
mina_simulation gamepad_teleop.py
mina_simulation play_isaacsim.py
```

### 4. Smoke test the policy node

```bash
ros2 run mina_simulation play_isaacsim.py --help
```

This confirms:

- Python import path is correct
- the installed executable is discoverable
- the startup path no longer fails on `omegaconf`

### 5. Smoke test the teleop node

```bash
ros2 run mina_simulation gamepad_teleop.py --help
```

This confirms the teleop entrypoint is installed and importable.

---

## Useful Run Commands

### Build only up to the simulation package

```bash
cd /home/alex/dev/mina-ros-env
source /opt/ros/humble/setup.bash
colcon build --packages-up-to mina_simulation --event-handlers console_cohesion+
```

### Run the policy node with packaged defaults

```bash
cd /home/alex/dev/mina-ros-env
source /opt/ros/humble/setup.bash
source install/setup.bash
ros2 run mina_simulation play_isaacsim.py
```

### Run the policy node with policy disabled

```bash
cd /home/alex/dev/mina-ros-env
source /opt/ros/humble/setup.bash
source install/setup.bash
ros2 run mina_simulation play_isaacsim.py --no-policy
```

### Run the policy node with an explicit config

```bash
cd /home/alex/dev/mina-ros-env
source /opt/ros/humble/setup.bash
source install/setup.bash
ros2 run mina_simulation play_isaacsim.py --config /home/alex/dev/mina-ros-env/src/mina_simulation/config/policy_humanoid.yaml
```

### Run the gamepad teleop bridge

```bash
cd /home/alex/dev/mina-ros-env
source /opt/ros/humble/setup.bash
source install/setup.bash
ros2 run mina_simulation gamepad_teleop.py
```

### Run the gamepad teleop bridge on a custom topic/rate

```bash
cd /home/alex/dev/mina-ros-env
source /opt/ros/humble/setup.bash
source install/setup.bash
ros2 run mina_simulation gamepad_teleop.py --topic /cmd_vel --rate 50
```

---

## Key Lessons Learned

**1. Source-tree code is often less standalone than it appears.** The original ROS scripts looked portable, but they relied on helper modules and runtime assets that lived elsewhere in the repository.

**2. A clean ROS package must own its runtime assets.** If a node needs a default config or checkpoint, those files need to be installed with the package and resolved via the package share path.

**3. Installed-node testing catches a different class of bugs than source inspection.** The `omegaconf` failure only surfaced when the node was exercised through `ros2 run` in the built workspace.

**4. Package boundaries matter.** Helper modules should not appear as ROS executables. That is a packaging smell and a sign that the install rules are exposing implementation details.

**5. Dependency-aware builds matter.** The `mina_description` issue had nothing to do with the bridge logic itself, but it still had to be fixed before the port could be considered truly integrated.

---

## Files Touched During the Port

### In the ROS workspace

- `mina-ros-env/src/mina_simulation/CMakeLists.txt`
- `mina-ros-env/src/mina_simulation/package.xml`
- `mina-ros-env/src/mina_simulation/scripts/gamepad.py`
- `mina-ros-env/src/mina_simulation/scripts/gamepad_teleop.py`
- `mina-ros-env/src/mina_simulation/scripts/rl_controller.py`
- `mina-ros-env/src/mina_simulation/scripts/play_isaacsim.py`
- `mina-ros-env/src/mina_simulation/config/policy_humanoid.yaml`
- `mina-ros-env/src/mina_simulation/config/checkpoints/policy_humanoid.onnx`
- `mina-ros-env/src/mina_description/CMakeLists.txt`

### In the Mina notes directory

- `Mina/bridge_porting_clean_ros_architecture.md`

---

## Final Outcome

The bridge code is now properly ported into the ROS workspace instead of depending on the Mina source tree layout.

The key result is not just that the scripts exist in a new folder, but that the ROS package now has clean boundaries:

- code dependencies are package-local
- runtime assets are package-local
- install rules expose only the intended nodes
- the package builds cleanly in dependency context
- the installed nodes pass `ros2 run` smoke tests

This is the minimum clean ROS architecture needed before moving on to full live integration against Isaac Sim topics and real teleop input.
