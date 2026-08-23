"""Android PowerManager thermal statuses, mirrored server-side.

The ceiling lives here rather than on the phone so the fleet policy can be
tightened without shipping an APK.
"""

THERMAL_NONE = 0
THERMAL_LIGHT = 1
THERMAL_MODERATE = 2
THERMAL_SEVERE = 3
THERMAL_CRITICAL = 4
THERMAL_EMERGENCY = 5
THERMAL_SHUTDOWN = 6

# Compute up to and including LIGHT. At MODERATE the phone is already throttling
# and the owner would feel it -- back off before that, never after.
THERMAL_CEILING = THERMAL_LIGHT

LABELS = {
    THERMAL_NONE: "none",
    THERMAL_LIGHT: "light",
    THERMAL_MODERATE: "moderate",
    THERMAL_SEVERE: "severe",
    THERMAL_CRITICAL: "critical",
    THERMAL_EMERGENCY: "emergency",
    THERMAL_SHUTDOWN: "shutdown",
}
