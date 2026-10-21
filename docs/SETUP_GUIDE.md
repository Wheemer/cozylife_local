# CozyLife Local: Device Setup & Troubleshooting Guide

Welcome to the comprehensive setup and troubleshooting guide for **CozyLife Local** in Home Assistant. This integration allows you to control your CozyLife-compatible smart plugs, multi-gang switches, lights, and battery-powered sensors **100% locally**, removing any cloud dependency.

To get the most out of your local smart home setup and avoid connection drops, follow this detailed, step-by-step guide.

---

## Table of Contents
1. [Pre-Setup Checklist](#1-pre-setup-checklist)
2. [Step-by-Step Onboarding](#2-step-by-step-onboarding)
   * [Step 2.1: Onboarding with the CozyLife App](#step-21-onboarding-with-the-cozylife-app)
   * [Step 2.2: Assigning Static IPs / DHCP Reservations](#step-22-assigning-static-ips--dhcp-reservations)
   * [Step 2.3: Locating Your Device's IP Address](#step-23-locating-your-devices-ip-address)
3. [Home Assistant Integration Setup](#3-home-assistant-integration-setup)
4. [Device-Specific Guides & Behaviors](#4-device-specific-guides--behaviors)
   * [Wall Switches & Multi-Gang Switch Rockers](#wall-switches--multi-gang-switch-rockers)
   * [Smart Plugs & Power Monitoring Outlets](#smart-plugs--power-monitoring-outlets)
   * [Smart Bulbs, Ceiling Lights & LED Strips](#smart-bulbs-ceiling-lights--led-strips)
   * [Sleeping Battery-Powered Sensors (Temp/Humidity)](#sleeping-battery-powered-sensors-temphumidity)
5. [Network Troubleshooting & Router Tuning](#5-network-troubleshooting--router-tuning)
6. [Analyzing Discovery Logs & Submitting New Models](#6-analyzing-discovery-logs--submitting-new-models)

---

## 1. Pre-Setup Checklist

Before attempting to configure CozyLife Local, make sure you have:
*   A fully functional **Home Assistant** instance.
*   **HACS (Home Assistant Community Store)** installed.
*   A **2.4 GHz Wi-Fi Network** (CozyLife devices do not support 5 GHz Wi-Fi).
*   Administrative access to your home router to configure static IPs (highly recommended).
*   The **CozyLife App** installed on your mobile phone (iOS or Android) for the initial Wi-Fi configuration.

---

## 2. Step-by-Step Onboarding

### Step 2.1: Onboarding with the CozyLife App

> [!IMPORTANT]
> Because CozyLife devices do not have an ethernet port, they must first be joined to your local Wi-Fi network using the official CozyLife app. This integration **cannot** perform the initial wireless pairing.

1. **Factory Reset the Device**: 
   * **Switches/Plugs**: Press and hold the physical button for 5–7 seconds until the LED starts flashing rapidly.
   * **Smart Lights**: Turn the physical light switch off and on 3 to 5 times sequentially until the light bulb flashes rapidly.
2. **Connect via CozyLife App**:
   * Open the app, ensure your phone is connected to your **2.4 GHz Wi-Fi network** with Bluetooth enabled.
   * Click **+** (Add Device) and follow the prompts to input your Wi-Fi credentials.
   * Once successfully added, you can control the device via the CozyLife app.
   * **Note**: Keep the device powered on and connected to the Wi-Fi.

---

### Step 2.2: Assigning Static IPs / DHCP Reservations

Local integration relies on sending network packets directly to your device's IP address. If your router restarts or lease times expire, standard DHCP may assign a new IP address to your CozyLife device, making it appear "Unavailable" in Home Assistant.

> [!TIP]
> Setting up a **DHCP Reservation** (also known as a **Static IP reservation** or **IP binding**) in your router's settings is the most important step to guarantee long-term stability.

1. Log into your home router's admin portal (e.g., `192.168.1.1` or `192.168.0.1`).
2. Navigate to the **DHCP Server / LAN / IP Reservation** settings page.
3. Locate your CozyLife device in the list of active clients (look for its MAC address).
4. Assign a specific, fixed IP address (e.g., `192.168.1.150`) to its MAC address.
5. Save the configuration.

---

### Step 2.3: Locating Your Device's IP Address

You may need the local IP address to verify network connectivity or a DHCP reservation. Automatic discovery does not require entering it into the integration.

#### Method A: Inside the CozyLife App
1. Open the CozyLife App and tap on your device.
2. Click the **three dots `...` / Settings** icon in the top right corner.
3. Tap **Device Information**.
4. Scroll down to find the listed **IP Address** (e.g., `192.168.1.45`).

#### Method B: Router Client List
Look for hostnames representing popular IoT chips used by CozyLife. Common hostnames include:
*   `HF-LPT230` or similar prefix
*   `ESP_XXXXXX` (for ESP8266/ESP32 variants)
*   `hanyu_smart_device`
*   `CozyLife_XXXXXX`

#### Method C: Network Scanner Tools
Use tools like **Angry IP Scanner** (PC/Mac) or **Fing** (Mobile App) to scan **TCP** port `5555`. CozyLife's APK-style local discovery is a separate **UDP** broadcast exchange on port `6095`; a TCP scan cannot substitute for that discovery exchange.

---

## 3. Home Assistant Integration Setup

Once your device is connected to Wi-Fi, add CozyLife Local to Home Assistant:

```
Settings ➔ Devices & Services ➔ + Add Integration ➔ CozyLife Local
```

Click **Submit**. The integration creates one persistent discovery controller with no IP address, CIDR, or sensor-profile fields. It sends the APK-style UDP/6095 discovery frame every 500 ms. A newly announced device appears in Home Assistant's normal discovered-device card; click **Add** to create its entry. A powered device is verified through local TCP/5555 at that point.

For a supported sleeping temperature/humidity sensor, its UDP announcement supplies the PID and local address. When you click **Add**, the integration uses that saved announcement rather than asking the sensor to wake a second time, then begins the same 500 ms known-address status probing. It does not assume a fixed wake or report schedule.

After setup, the integration exposes the configured local IP address as a diagnostic sensor on the Home Assistant device page. Use that visible IP when checking DHCP reservations, router firewall rules, or direct port `5555` connectivity.

---

## 4. Device-Specific Guides & Behaviors

### Wall Switches & Multi-Gang Switch Rockers

Multi-gang devices (such as double or triple rocker wall switches) share a single IP address and a single CozyLife chip.

*   **Bitmask Control**: CozyLife controls multi-gang channels via a bitmask on Data Point ID 1 (`DPID 1`).
*   **Auto-Detection**: The integration automatically reads the device's capability maps and count attributes. It determines if it has 1, 2, 3, or more switch gangs.
*   **Individual Entities**: Home Assistant will create a distinct switch entity for each gang (e.g., `Switch 1`, `Switch 2`). You can rename these individually in the Home Assistant UI without losing local link stability.
*   **Switch Options**: When the device reports the required DPIDs, the integration exposes Power-on State (`DPID 18`) and LED Status (`DPID 19`).

---

### Smart Plugs & Power Monitoring Outlets

Our integration supports both standard on/off plugs and high-end smart plugs equipped with electricity metering chips.

*   **Metered Plugs**: If the device's local DPID response reports DPIDs `26` through `29`, the integration will automatically expose:
    *   **Voltage** (V) - `sensor.cozylife_device_voltage`
    *   **Current** (mA) - `sensor.cozylife_device_current`
    *   **Active Power** (W) - `sensor.cozylife_device_power`
    *   **Total Cumulative Energy** (kWh) - `sensor.cozylife_device_energy`
*   **Home Assistant Energy Dashboard**: The energy entity (`sensor.cozylife_device_energy`) utilizes standard `device_class: energy` and `state_class: total_increasing`. You can directly add it to the Home Assistant Energy Dashboard to track historical consumption!

---

### Smart Bulbs, Ceiling Lights & LED Strips

CozyLife smart lights can range from basic dimmable warm white to fully addressable RGB strip lights.

*   **Color Temp Settings**: During setup or via **Options**, you can configure the specific Kelvin range (default: `2000K` to `6500K`) to match your bulb's hardware capabilities.
*   **Work Mode Management**: 
    *   Some lights require toggling the work mode (DPID 2) when moving between white light and RGB color.
    *   The integration features automatic state corrections. When you set an RGB color, it correctly preserves work mode states to prevent standard bulb firmware from glitching or flashing dark blue.
*   **State Transitions**: Color changes and brightness steps are computed with high fidelity to provide smooth local dimming.

---

### Sleeping Battery-Powered Sensors (Temp/Humidity)

Battery-powered sensors can sleep between reports. When one answers the integration's local UDP discovery request, Home Assistant shows its normal discovered-device card. Click **Add**; the saved announcement supplies the product identity and local address, so the integration creates the Temperature, Humidity, and Battery entities without waiting for a second response.

The integration keeps the APK-derived local status transport available for valid reports, but does not configure a report interval or claim a fixed wake cadence. The device controls when it is available.

---

## 5. Network Troubleshooting & Router Tuning

If you have completed setup but your devices keep dropping off or showing as `Unavailable`, review these network settings:

*   **AP Isolation (Client Isolation)**: Some routers have a security feature called "Access Point Isolation" or "Wireless Client Isolation" enabled. This prevents Wi-Fi devices from talking directly to each other or to your Home Assistant server. **Ensure this setting is DISABLED** in your router's wireless options.
*   **UDP Discovery Broadcasts**: CozyLife discovery sends UDP broadcasts to port `6095`. After the one-time discovery controller is added, it keeps one shared local listener active and presents an announced, unconfigured device for confirmation. If discovery does not find a device, confirm that broadcasts can cross the relevant LAN segment; a device that never answers local discovery cannot be identified automatically.
*   **TCP Port 5555**: Local device status and control use TCP port `5555`. Ensure that no internal firewall or parent-router rule blocks traffic between Home Assistant and the device IP range.
*   **IP Ping Test**: A successful ping can confirm basic reachability, but a failed ping does not prove a CozyLife device is offline: devices may ignore ICMP and battery sensors can sleep between reports.

## 6. Analyzing Discovery Logs & Submitting New Models

If your device is added but some entities are missing (e.g., a smart plug that doesn't show its metering data), we need to check the exact **Data Points (DPIDs)** the hardware reports.

### Step 1: Enable CozyLife Debug Logs
Add the following configuration to your `configuration.yaml` file to capture raw device handshakes:

```yaml
logger:
  default: info
  logs:
    custom_components.cozylife_local: debug
```

Restart Home Assistant to apply the logging configuration.

### Step 2: Locate the Handshake Log
Go to `Settings ➔ System ➔ Logs` and search for `cozylife_local`. Look for the successful discovery line:

```text
Successfully discovered device 192.168.1.177 locally: DID=0011223344, PID=xyz987, Type=00, DPIDs=['1', '26', '27', '28', '29']
Detected 1 outlet entity/entities at 192.168.1.177 with DPIDs: ['1', '26', '27', '28', '29']
```

### Step 3: Open a GitHub Issue
If the entity mappings are incorrect:
1. Note your device model (e.g., Dual Socket, RGB Bulb model).
2. Copy the **PID** (Product ID) and **DPIDs** array from your debug logs.
3. Open an issue on our [GitHub Repository](https://github.com/soulripper13/cozylife_local/issues) using the "New Device Request" template.
4. Developers will map the PID and DPIDs in the model catalog to expose the correct entities automatically in the next integration update!
