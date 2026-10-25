# CozyLife Local for Home Assistant

A **100% local** Home Assistant integration for supported CozyLife-compatible Wi-Fi devices. Control switches, plugs, lights, fans, sensors, and other verified APK-catalogued local profiles without a CozyLife cloud account.

---

### Key Features

*   🔒 **100% Local Control**: All commands are sent directly to devices on your local network. No internet connection, API keys, or cloud accounts required.
*   🔎 **APK-Derived Local Discovery**: A shared UDP/6095 listener uses the CozyLife app's discovery frames for setup scans and offers unconfigured announced devices for confirmation.
*   ⚡ **Low-Latency Switching**: Instant physical updates with automated bitmask handling for multi-gang switches (e.g. double or triple rocker wall switches).
*   📈 **High-Fidelity Power Monitoring**: Automatically exposes real-time Voltage, Current, Power, and Cumulative Energy (kWh) sensors on metered smart plugs.
*   💡 **Smart Lighting Enhancements**: Smooth color transitions, custom color-temperature Kelvin ranges, and color state safeguards to prevent hardware glitches.
*   🔋 **Sleeping Sensor Handling**: Uses the saved UDP identity for supported sleeping temperature/humidity sensors instead of requiring a second synchronous TCP response during setup.
*   📅 **Conditional Schedule Controls**: Provides Home Assistant-backed plug and light schedules where the verified local device profile supports them.

---

### Resources

*   📖 **[Detailed Setup & Troubleshooting Guide](https://github.com/soulripper13/cozylife_local/blob/main/docs/SETUP_GUIDE.md)**: Discovery, safe diagnostics, sleeping sensors, schedules, and network settings.
*   🔎 **[APK Local Product Coverage](https://github.com/soulripper13/cozylife_local/blob/main/docs/APK_LOCAL_PRODUCT_COVERAGE.md)**: Verified entity mappings and the evidence boundary for unsupported controls.
*   🐞 **[GitHub Repository & Issue Tracker](https://github.com/soulripper13/cozylife_local)**: Report bugs or device compatibility findings without sharing local IP addresses or device IDs.
