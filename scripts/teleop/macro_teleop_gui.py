#!/usr/bin/env python3
# Copyright (c) 2025, The Berkeley Humanoid Lite Project Developers.

"""
Macro Action Gamepad Controller for Humanoid Robot

A Tkinter GUI that maps gamepad axes/triggers to pre-defined "Macro Actions"
that control multiple joints at once. Each axis provides a 0.0-1.0 intensity
multiplier that scales the target positions for all joints in that macro.

Usage:
    # Terminal 1: Start the joy node (connects to your gamepad)
    ros2 run joy joy_node

    # Terminal 2: Run this GUI
    cd ~/dev/Mina
    uv run python scripts/teleop/macro_teleop_gui.py

    # Use triggers/axes to activate macros. The intensity (0-1) scales the motion.

ROS2 Interfaces:
    Subscriber: /joy (sensor_msgs/msg/Joy)
    Publisher:  /joint_command (sensor_msgs/msg/JointState) @ 25 Hz
"""

import tkinter as tk
from tkinter import ttk
from typing import Optional

import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Joy, JointState


# ═══════════════════════════════════════════════════════════════════════════════
# Macro Action Definitions
# ═══════════════════════════════════════════════════════════════════════════════
# Each macro defines a group of joints and their maximum target positions.
# The actual target = max_position * intensity, where intensity is 0.0-1.0
# from the mapped gamepad axis.

MACRO_ACTIONS = {
    "ArmsRaise": {
        "joints": ["arm_left_shoulder_pitch_joint", "arm_right_shoulder_pitch_joint"],
        "max_positions": [1.5, 1.5],
    },
    "LegsBend": {
        "joints": [
            "leg_left_hip_pitch_joint",
            "leg_right_hip_pitch_joint",
            "leg_left_knee_pitch_joint",
            "leg_right_knee_pitch_joint",
            "leg_left_ankle_pitch_joint",
            "leg_right_ankle_pitch_joint",
        ],
        "max_positions": [-0.2, -0.2, 0.8, 0.8, -0.3, -0.3],
    },
    "ArmsSpread": {
        "joints": ["arm_left_shoulder_roll_joint", "arm_right_shoulder_roll_joint"],
        "max_positions": [1.5, -1.5],
    },
    "BoxerGuard": {
        "joints": [
            "arm_left_shoulder_pitch_joint",
            "arm_right_shoulder_pitch_joint",
            "arm_left_elbow_pitch_joint",
            "arm_right_elbow_pitch_joint",
        ],
        "max_positions": [0.5, 0.5, 1.5, 1.5],
    },
}

# Default axis assignments (can be changed in GUI)
# Maps macro name -> axis index
DEFAULT_AXIS_MAP = {
    "ArmsRaise": 5,      # R2 trigger
    "LegsBend": 2,       # L2 trigger
    "ArmsSpread": 0,     # Left stick X
    "BoxerGuard": 4,     # L1 (as axis, some controllers)
}

DEFAULT_DEBUG_BUTTON = 4  # Usually L1/LB


class MacroTeleopGuiNode(Node):
    """ROS2 node with Tkinter GUI for macro action gamepad control."""

    def __init__(self):
        super().__init__("macro_teleop_gui_node")

        # Latest joy message (None until first message received)
        self._latest_joy: Optional[Joy] = None

        # ═══════════════════════════════════════════════════════════════════════
        # ROS2 Setup
        # ═══════════════════════════════════════════════════════════════════════

        # Subscriber to /joy
        self.create_subscription(Joy, "/joy", self._joy_callback, 10)

        # Publisher to /joint_command
        self._pub = self.create_publisher(JointState, "/joint_command", 10)

        # 25 Hz timer for processing and publishing
        self._timer = self.create_timer(1.0 / 25.0, self._timer_callback)

        # ═══════════════════════════════════════════════════════════════════════
        # Tkinter GUI Setup
        # ═══════════════════════════════════════════════════════════════════════

        self._root = tk.Tk()
        self._root.title("Macro Teleop - Gamepad to Joint Commands")
        self._root.protocol("WM_DELETE_WINDOW", self._on_close)

        # Storage for GUI widgets (per macro)
        self._axis_vars = {}      # macro_name -> IntVar for axis index
        self._invert_vars = {}    # macro_name -> BooleanVar for invert
        self._intensity_labels = {}  # macro_name -> Label showing current intensity

        # Debug modifier button index
        self._debug_button_var = tk.IntVar(value=DEFAULT_DEBUG_BUTTON)

        # Joy status indicator
        self._joy_status_var = tk.StringVar(value="Waiting for /joy...")

        self._build_gui()

        self.get_logger().info("Macro Teleop GUI Node started. Waiting for /joy messages...")

    def _build_gui(self):
        """Construct the Tkinter GUI."""
        root = self._root

        # ─────────────────────────────────────────────────────────────────────
        # Global Settings Frame
        # ─────────────────────────────────────────────────────────────────────
        global_frame = ttk.LabelFrame(root, text="Global Settings", padding=10)
        global_frame.pack(fill=tk.X, padx=10, pady=5)

        # Joy status
        ttk.Label(global_frame, text="Joy Status:").grid(row=0, column=0, sticky=tk.W)
        status_label = ttk.Label(global_frame, textvariable=self._joy_status_var,
                                  foreground="gray")
        status_label.grid(row=0, column=1, columnspan=3, sticky=tk.W, padx=5)
        self._status_label = status_label

        # Debug modifier button
        ttk.Label(global_frame, text="Debug Modifier Button:").grid(row=1, column=0, sticky=tk.W)
        debug_spin = ttk.Spinbox(global_frame, from_=0, to=15, width=5,
                                  textvariable=self._debug_button_var)
        debug_spin.grid(row=1, column=1, sticky=tk.W, padx=5)
        ttk.Label(global_frame, text="(Hold to print active macros to terminal)",
                  foreground="gray").grid(row=1, column=2, sticky=tk.W)

        # ─────────────────────────────────────────────────────────────────────
        # Macro Actions Frame
        # ─────────────────────────────────────────────────────────────────────
        macros_frame = ttk.LabelFrame(root, text="Macro Action Mappings", padding=10)
        macros_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        # Header row
        headers = ["Macro Action", "Axis Index", "Invert", "Intensity", "Joints"]
        for col, header in enumerate(headers):
            ttk.Label(macros_frame, text=header, font=("TkDefaultFont", 9, "bold")
                      ).grid(row=0, column=col, padx=5, pady=2, sticky=tk.W)

        # Per-macro rows
        for i, (macro_name, macro_data) in enumerate(MACRO_ACTIONS.items()):
            row = i + 1

            # Macro name label
            ttk.Label(macros_frame, text=macro_name, font=("TkDefaultFont", 9, "bold")
                      ).grid(row=row, column=0, padx=5, pady=4, sticky=tk.W)

            # Axis index spinbox
            axis_var = tk.IntVar(value=DEFAULT_AXIS_MAP.get(macro_name, 0))
            self._axis_vars[macro_name] = axis_var
            axis_spin = ttk.Spinbox(macros_frame, from_=0, to=7, width=5,
                                     textvariable=axis_var)
            axis_spin.grid(row=row, column=1, padx=5, pady=4)

            # Invert checkbox
            invert_var = tk.BooleanVar(value=False)
            self._invert_vars[macro_name] = invert_var
            invert_check = ttk.Checkbutton(macros_frame, variable=invert_var)
            invert_check.grid(row=row, column=2, padx=5, pady=4)

            # Current intensity label (read-only)
            intensity_label = ttk.Label(macros_frame, text="0.00", width=6,
                                         relief=tk.SUNKEN, anchor=tk.CENTER)
            intensity_label.grid(row=row, column=3, padx=5, pady=4)
            self._intensity_labels[macro_name] = intensity_label

            # Joints info (abbreviated)
            joints_str = ", ".join(j.replace("_joint", "") for j in macro_data["joints"])
            if len(joints_str) > 50:
                joints_str = joints_str[:47] + "..."
            ttk.Label(macros_frame, text=joints_str, foreground="gray",
                      font=("TkDefaultFont", 8)).grid(row=row, column=4, padx=5, pady=4, sticky=tk.W)

        # ─────────────────────────────────────────────────────────────────────
        # Instructions Frame
        # ─────────────────────────────────────────────────────────────────────
        info_frame = ttk.LabelFrame(root, text="Instructions", padding=10)
        info_frame.pack(fill=tk.X, padx=10, pady=5)

        instructions = (
            "1. Run 'ros2 run joy joy_node' in another terminal to start the gamepad driver.\n"
            "2. Map each Macro Action to a gamepad axis (triggers are usually axes 2, 5).\n"
            "3. The axis value is normalized to 0.0-1.0 intensity, which scales max_positions.\n"
            "4. Check 'Invert' if your axis rests at 1.0 instead of -1.0 or 0.0.\n"
            "5. Hold Debug Modifier button to see active macro intensities in terminal.\n"
            "6. Publishing to /joint_command at 25 Hz."
        )
        ttk.Label(info_frame, text=instructions, justify=tk.LEFT).pack(anchor=tk.W)

        # Set minimum window size
        root.update_idletasks()
        root.minsize(650, 400)

    def _joy_callback(self, msg: Joy):
        """Store the latest Joy message."""
        self._latest_joy = msg

    def _axis_to_intensity(self, axis_value: float, invert: bool) -> float:
        """
        Convert axis value to 0.0-1.0 intensity.

        Triggers typically:
          - Rest at -1.0 or 0.0
          - Go to 1.0 when fully pressed

        This function normalizes various behaviors to 0.0-1.0.
        """
        if invert:
            axis_value = -axis_value

        # Handle triggers that rest at -1.0 and go to 1.0
        # Normalize from [-1, 1] to [0, 1]
        intensity = (axis_value + 1.0) / 2.0

        # Clamp to [0, 1]
        return max(0.0, min(1.0, intensity))

    def _timer_callback(self):
        """25 Hz callback: process joy, update GUI, publish targets."""
        # Update Tkinter (non-blocking alternative to mainloop)
        try:
            self._root.update_idletasks()
            self._root.update()
        except tk.TclError:
            # Window was closed
            return

        joy = self._latest_joy

        # Update status indicator
        if joy is None:
            self._joy_status_var.set("Waiting for /joy...")
            self._status_label.configure(foreground="red")
        else:
            n_axes = len(joy.axes)
            n_buttons = len(joy.buttons)
            self._joy_status_var.set(f"Connected ({n_axes} axes, {n_buttons} buttons)")
            self._status_label.configure(foreground="green")

        # Build JointState message
        joint_names = []
        joint_positions = []
        active_macros = []  # For debug logging

        for macro_name, macro_data in MACRO_ACTIONS.items():
            # Get axis index from GUI
            try:
                axis_idx = self._axis_vars[macro_name].get()
            except tk.TclError:
                axis_idx = 0

            # Get invert setting from GUI
            try:
                invert = self._invert_vars[macro_name].get()
            except tk.TclError:
                invert = False

            # Read axis value (default to -1.0 which gives intensity 0.0)
            if joy is not None and 0 <= axis_idx < len(joy.axes):
                axis_value = joy.axes[axis_idx]
            else:
                axis_value = -1.0  # Resting state

            # Convert to intensity
            intensity = self._axis_to_intensity(axis_value, invert)

            # Update GUI intensity label
            self._intensity_labels[macro_name].configure(text=f"{intensity:.2f}")

            # Calculate joint targets
            for joint_name, max_pos in zip(macro_data["joints"], macro_data["max_positions"]):
                target = max_pos * intensity
                joint_names.append(joint_name)
                joint_positions.append(target)

            # Track active macros for debug
            if intensity > 0.05:
                active_macros.append((macro_name, intensity))

        # Publish JointState message
        msg = JointState()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.name = joint_names
        msg.position = joint_positions
        msg.velocity = []
        msg.effort = []
        self._pub.publish(msg)

        # Debug logging (only if modifier button is pressed)
        if joy is not None:
            try:
                debug_button_idx = self._debug_button_var.get()
            except tk.TclError:
                debug_button_idx = DEFAULT_DEBUG_BUTTON

            if 0 <= debug_button_idx < len(joy.buttons):
                if joy.buttons[debug_button_idx] == 1:
                    if active_macros:
                        macros_str = ", ".join(
                            f"{name}={intensity:.2f}" for name, intensity in active_macros
                        )
                        self.get_logger().info(f"Active: {macros_str}")

                        # Also log joint targets
                        targets_str = ", ".join(
                            f"{n}: {p:+.2f}" for n, p in zip(joint_names, joint_positions)
                            if abs(p) > 0.01
                        )
                        if targets_str:
                            self.get_logger().info(f"  Joints: {targets_str}")
                    else:
                        self.get_logger().info("No active macros")

    def _on_close(self):
        """Handle window close button."""
        self.get_logger().info("GUI closed. Shutting down...")
        self._root.quit()
        self._root.destroy()
        raise SystemExit(0)

    def shutdown(self):
        """Clean shutdown of GUI."""
        try:
            self._root.quit()
            self._root.destroy()
        except tk.TclError:
            pass


def main():
    rclpy.init()
    node = MacroTeleopGuiNode()

    try:
        # Use spin() - the timer callback handles Tkinter updates
        rclpy.spin(node)
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        node.get_logger().info("Shutting down Macro Teleop GUI Node...")
        node.shutdown()
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
