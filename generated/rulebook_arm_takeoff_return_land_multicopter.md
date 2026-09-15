# PX4 Multicopter Arm, Take Off, Return and Land — Robot Skill Rulebook

## Overview

This rulebook describes bringing a PX4 multicopter from powered-off through an
automatic takeoff, an automatic return to the home position, and an automatic
landing, ending with the vehicle disarmed. Each step lists the readiness
conditions the PX4 User Guide states for it, including safety switch
engagement, preflight checks, and the position estimates each flight mode
requires.

## Skill

**arm_takeoff_return_land_multicopter** is a high-level skill. It is composed
of the following primitive actions: power up vehicle, engage safety switch, run
preflight checks, arm vehicle, execute automatic takeoff, ascend to return
altitude, fly to nearest landing point, descend to descent altitude, wait at
descent altitude, execute automatic landing, and disarm vehicle.

## Objects

The objects involved in this skill are: vehicle, battery, motors, actuators,
safety switch, gnss module, rc transmitter, throttle stick, yaw stick, ground
station, home position, and landing point.

## States

The relevant states of the world are: vehicle powered, actuators locked,
vehicle disarmed, sensors initialising, estimator converging, vehicle prearmed,
arming possible, preflight checks passed, vehicle healthy, local position
valid, global position valid, vehicle armed, home position set, takeoff
altitude reached, position held, return altitude reached, at landing point,
descent altitude reached, land delay elapsed, and vehicle landed.

## Primitive actions and their rules

**power_up_vehicle** — The robot connects the battery and powers the vehicle,
leaving all actuators locked and the vehicle disarmed while sensor
initialisation and EKF2 convergence begin. After this action, vehicle powered
is true. After this action, actuators locked is true. After this action,
vehicle disarmed is true. After this action, sensors initialising is true.
After this action, estimator converging is true.

**engage_safety_switch** — The robot presses the safety switch on the GPS
module, which with the default COM_PREARM_MODE = 1 configuration enables prearm
mode and makes arming possible. This action requires that vehicle powered is
already true as a precondition. After this action, vehicle prearmed is true.
After this action, arming possible is true.

**run_preflight_checks** — The robot lets PX4 run the preflight sensor quality
and estimator checks covering IMU consistency, compass calibration, GNSS lock
and EKF2 health, position estimate accuracy, battery voltage above
COM_ARM_BAT_MIN, RC signal and Remote ID, confirming the vehicle is in a
healthy state for the selected mode. This action requires that vehicle powered
is already true as a precondition. This action requires that sensors
initialising is already true as a precondition. This action requires that
estimator converging is already true as a precondition. After this action,
preflight checks passed is true. After this action, vehicle healthy is true.
After this action, local position valid is true. After this action, global
position valid is true.

**arm_vehicle** — The robot commands arming with the default stick gesture,
holding the throttle stick at minimum and the yaw stick at maximum for one
second, which powers the motors and actuators and captures the home position at
the current location. This action requires that preflight checks passed is
already true as a precondition. This action requires that vehicle healthy is
already true as a precondition. This action requires that arming possible is
already true as a precondition. This action requires that vehicle prearmed is
already true as a precondition. After this action, vehicle armed is true. After
this action, home position set is true.

**execute_automatic_takeoff** — The robot engages Takeoff mode so the
multicopter ascends vertically at MPC_TKO_SPEED (1.5 m/s by default) to the
altitude set by MIS_TAKEOFF_ALT (2.5 m by default), which the manual states
requires a valid local position estimate on an armed vehicle. This action
requires that vehicle armed is already true as a precondition. This action
requires that local position valid is already true as a precondition. After
this action, takeoff altitude reached is true. After this action, position held
is true.

**ascend_to_return_altitude** — The robot engages Return mode and the vehicle
ascends to the minimum return altitude set by RTL_RETURN_ALT (60 m by default),
maintaining its current altitude if already higher; the manual states Return
mode requires a global position estimate and a home position already set. This
action requires that takeoff altitude reached is already true as a
precondition. This action requires that global position valid is already true
as a precondition. This action requires that home position set is already true
as a precondition. After this action, return altitude reached is true.

**fly_to_nearest_landing_point** — The robot flies the vehicle at constant
altitude to the nearest safe landing point, choosing between rally points and
the home position by the shortest horizontal geofence-aware path. This action
requires that return altitude reached is already true as a precondition. After
this action, at landing point is true.

**descend_to_descent_altitude** — The robot descends the vehicle rapidly to the
descent altitude set by RTL_DESCEND_ALT (30 m by default) on arrival over the
landing point. This action requires that at landing point is already true as a
precondition. After this action, descent altitude reached is true.

**wait_at_descent_altitude** — The robot holds the vehicle at the descent
altitude for the period set by RTL_LAND_DELAY, useful for deploying landing
gear, loitering indefinitely if the value is -1. This action requires that
descent altitude reached is already true as a precondition. After this action,
land delay elapsed is true.

**execute_automatic_landing** — The robot initiates the landing sequence so
Land mode descends the vehicle vertically at MPC_LAND_SPEED until touchdown is
detected; the manual states the mode requires angular velocity and attitude
estimation plus local altitude and local position relative to the EKF2 origin.
This action requires that land delay elapsed is already true as a precondition.
This action requires that local position valid is already true as a
precondition. After this action, vehicle landed is true.

**disarm_vehicle** — The robot lets the vehicle auto-disarm the time set by
COM_DISARM_LAND (2 seconds by default) after landing, or commands a manual
disarm by holding the throttle stick at minimum and the yaw stick at minimum
for one second, removing power from the motors and actuators. This action
requires that vehicle landed is already true as a precondition. This action
requires that vehicle armed is already true as a precondition. After this
action, vehicle disarmed is true.

## Ordering rules

The vehicle must be powered up before the safety switch can be engaged. The
safety switch must be engaged before arming is possible. The preflight checks
must pass and the vehicle must be in a healthy state before it can be armed.
The vehicle must be armed and have a valid local position estimate before it
can switch into Takeoff mode. Takeoff must complete and a global position
estimate and set home position must exist before Return mode can run. The
return sequence runs in order: ascend to the return altitude, fly to the
nearest safe landing point, descend to the descent altitude, wait for the land
delay, then initiate landing. Landing must be detected before the vehicle
disarms, automatically after the COM_DISARM_LAND timeout or by manual stick
gesture.

## Source

Generated from /private/tmp/claude-501/-Users-apple-dev-personal-
Grasp/36c43e24-8700-49f9-9b68-82a88bf4b32d/scratchpad/px4_quadcopter_sortie.md
