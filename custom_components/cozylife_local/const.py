"""Constants for the CozyLife (New) integration."""

DOMAIN = "cozylife_local"

# DPID mapping (example, these should be confirmed from pid_list)
# These are taken from the old integration's const.py and will need to be dynamically
# mapped based on the device's PID and the fetched pid_list.
SWITCH = '1'
WORK_MODE = '2'
TEMP = '3'
BRIGHT = '4'
HUE = '5'
SAT = '6'
LIGHT_COUNTDOWN = '13'
LIGHT_NORMAL_TIMER = '14'
LIGHT_POWER_MEMORY = '9'  # 0 = on, 1 = restore previous state
LIGHT_SPEED = '8'          # raw ÷10 = effect speed percentage
LIGHT_SENSITIVITY = '10'   # microphone sensitivity, range 1-10
LIGHT_CHIP_POINT = '12'    # DreamColor LED count
LIGHT_RGB_TURN = '11'      # LED color channel order
LIGHT_STATIC_FADE = '20'   # static DreamColor fade toggle
LIGHT_MUSIC_INDEX = '15'   # DreamColor music effect mode
LIGHT_STATIC_COLOR = '16'  # DreamColor whole-strip static hue/saturation payload
LIGHT_BOOT_STATUS = '44'   # DreamColor power-on behavior

# Sensor DPIDs (temperature/humidity sensor, PID=Z4tRml)
SENSOR_TEMPERATURE = '8'   # raw value ÷ 10 = °C
SENSOR_HUMIDITY = '4'      # raw value = %
SENSOR_BATTERY = '9'       # raw value ÷ 10 = %
SENSOR_TYPE_CODE = '03'

# Known sensor PIDs — used as the primary discriminator to identify sensor devices
KNOWN_SENSOR_PIDS = {'Z4tRml'}

# Electrician / smart plug DPIDs
PLUG_COUNTDOWN = '2'        # countdown/timer, seconds
PLUG_TIMER_SCHEDULE = '3'   # encoded app timer/schedule payload
PLUG_POWER_ON_STATE = '18'  # relay restore behavior after power loss, enum
PLUG_LED_STATUS = '19'      # indicator LED behavior, enum
PLUG_BACKLIGHT_STATUS = '20'  # backlight enable flag
PLUG_CHILD_LOCK = '21'        # child-lock enable flag
PLUG_ENERGY = '26'          # add_ele, scale 3, kWh
PLUG_TOTAL_ENERGY = '42'    # total_ele, raw ÷1000 = kWh
PLUG_CLEAR_LOG = '43'       # dd, clear device log command
PLUG_CURRENT = '27'         # cur_current, mA
PLUG_POWER = '28'           # cur_power, scale 1, W
PLUG_VOLTAGE = '29'         # cur_voltage, scale 1, V
PLUG_FAULT = '30'           # fault enum
PLUG_OVERCURRENT_PROTECTION = '32'  # overcurrent protection enable flag
PLUG_TIMER_STATUS = '72'    # last off reason / timer status enum

# Supported device types (derived from old integration, actual support depends on pid_list)
SWITCH_TYPE_CODE = '00'
LIGHT_TYPE_CODE = '01'
RGB_LIGHT_TYPE_CODE = '02'  # Some RGB lights report type code 02
SUPPORT_DEVICE_CATEGORY = [SWITCH_TYPE_CODE, LIGHT_TYPE_CODE, RGB_LIGHT_TYPE_CODE]

PLATFORMS = [
    "light",
    "switch",
    "sensor",
    "binary_sensor",
    "button",
    "number",
    "select",
    "time",
]

DEFAULT_MIN_KELVIN = 2000
DEFAULT_MAX_KELVIN = 6500

# Confirmed model-specific color temperature ranges.
# CozyLife lights report DPID 3 on a normalized 0-1000 scale, but do not expose
# the physical Kelvin endpoints over the local protocol.
LIGHT_KELVIN_RANGES = {
    "rju4K7": (2700, 6500),
}

SENSOR_REPORT_INTERVAL_DPID = '14'

# APK panel 2023120210160168: values and ranges are verified from its source.
SENSOR_TEMP_SENSITIVITY_DPID = '25'   # ÷10 = °C, range 0.5-3.0
SENSOR_HUMIDITY_SENSITIVITY_DPID = '24'  # raw %, range 5-30
SENSOR_TEMPERATURE_HIGH_DPID = '20'  # ÷10 = °C, range -10.0-110.0
SENSOR_TEMPERATURE_LOW_DPID = '21'   # ÷10 = °C, range -10.0-110.0
SENSOR_HUMIDITY_HIGH_DPID = '22'     # raw %, range 0-100
SENSOR_HUMIDITY_LOW_DPID = '23'      # raw %, range 0-100
