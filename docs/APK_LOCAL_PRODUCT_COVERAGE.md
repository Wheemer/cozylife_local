# APK local product coverage

This integration uses the bundled CozyLife APK catalogue (`model.json`) as the
product inventory. A catalogue entry is eligible for local support only when
the APK marks it as Wi-Fi (`net: "02"`) and there is evidence for the DPID
meaning and a safe Home Assistant entity mapping.

## Implemented local families

| APK family | Local entity coverage | Evidence |
| --- | --- | --- |
| Electrical | Relay/outlet switching, metering, and documented plug options | APK catalogue and `electrician_en.md` |
| Light | On/off, brightness, colour, colour temperature, modes, and documented options | APK light transport and `light_en.md` |
| Motor | Native fan power, documented percentage/gear speeds, and wind modes | APK catalogue and `motor_en.md` |
| Sensor | Temperature, humidity, battery, contact, water leak, and the Wi-Fi multi-sensor's motion/contact profile | APK catalogue and `sensor_en.md` |
| Home appliance | Documented DPID-1 `switch_power` as a generic switch | APK catalogue and `appliance_en.md` |
| Energy storage | Battery, total input/output power, remaining times, and battery temperature when advertised | APK catalogue and `battery_en.md` |
| Gateway | Documented discovery-mode configuration switch | APK catalogue and `gateway_en.md` |
| Video intercom/doorbell | Reported active-call, battery, low-battery, and motion state when the documented video profile is advertised | CozyLife video project and `doorbell_en.md` |

## Deliberately not guessed

- A Wi-Fi entry with no usable DPID cannot provide a reliable entity merely
  because it appears in the APK catalogue.
- DIY/passthrough products advertise DPID 51 as an opaque JSON configuration
  payload. There is no schema in the APK or published DPID documentation, so
  the integration must not expose an unsafe free-form control.
- Lock-only products commonly advertise only DPID 101. DPID 101 is documented
  as a call-state event for the video protocol, not as an unlock command.
  Lock actuation and video streaming stay out of scope until their local
  encrypted/session protocol is independently verified.
- Voice/AI and speaker products remain in the evidence-gathering queue. Their
  201--203 and 1--25 DPID sets have no verified local entity semantics in the
  APK resources or CozyLife's published DPID documentation yet. Once those
  controls are mapped, the integration will add only entities that match the
  verified local behavior.

## Transport rule

The APK uses `cmd:2, attr:[1]` for sleeping-sensor status checks and
`cmd:2, attr:[0]` for a powered product's complete property state. The
integration preserves that split: high-frequency waking-sensor probes never
become a polling scheme for powered products.
