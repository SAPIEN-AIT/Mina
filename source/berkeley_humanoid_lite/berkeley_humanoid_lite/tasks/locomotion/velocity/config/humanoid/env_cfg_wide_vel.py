"""Wide-velocity variant of the humanoid task.

Trains on lin_vel_x=(-1.5, 1.5), lin_vel_y=(-0.8, 0.8), ang_vel_z=(-2.0, 2.0).
Deployment commands at ±1.0 / ±0.5 / ±1.5 sit comfortably mid-distribution,
giving the policy authority headroom beyond what it will ever be asked to do.
"""

import math

from isaaclab.utils import configclass

import berkeley_humanoid_lite.tasks.locomotion.velocity.mdp as mdp
from berkeley_humanoid_lite.tasks.locomotion.velocity.config.humanoid.env_cfg import (
    BerkeleyHumanoidLiteEnvCfg,
    CommandsCfg,
)


@configclass
class WideVelCommandsCfg(CommandsCfg):
    base_velocity = mdp.UniformVelocityCommandCfg(
        resampling_time_range=(3.0, 8.0),
        debug_vis=True,
        asset_name="robot",
        heading_command=True,
        heading_control_stiffness=0.5,
        rel_standing_envs=0.15,
        rel_heading_envs=1.0,
        ranges=mdp.UniformVelocityCommandCfg.Ranges(
            lin_vel_x=(-1.5, 1.5),
            lin_vel_y=(-0.8, 0.8),
            ang_vel_z=(-2.0, 2.0),
            heading=(-math.pi, math.pi),
        ),
    )


@configclass
class BerkeleyHumanoidLiteWideVelEnvCfg(BerkeleyHumanoidLiteEnvCfg):
    commands: WideVelCommandsCfg = WideVelCommandsCfg()
