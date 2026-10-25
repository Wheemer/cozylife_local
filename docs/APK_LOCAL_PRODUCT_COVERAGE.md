# APK local product coverage

This integration uses the bundled CozyLife APK catalogue (`model.json`) as the
product inventory. A catalogue entry is eligible for local support only when
the APK marks it as Wi-Fi (`net: "02"`) and there is evidence for the DPID
meaning and a safe Home Assistant entity mapping.

## Implemented local families

| APK family | Local entity coverage | Evidence |
| --- | --- | --- |
| Electrical | Relay/outlet switching, metering, and documented plug options | APK catalogue and `electrician_en.md` |
| Light | On/off, brightness, colour, colour temperature, countdown, and power-off-memory configuration when advertised | APK light transport and `light_en.md` |
| Motor | Native fan power, documented percentage/gear speeds, and wind modes | APK catalogue and `motor_en.md` |
| Sensor | Temperature, humidity, battery, contact, water leak, and the Wi-Fi multi-sensor's motion/contact profile | APK catalogue and `sensor_en.md` |
| Home appliance | Documented DPID-1 `switch_power` as a generic switch | APK catalogue and `appliance_en.md` |
| Energy storage | Battery status, battery level/temperature, total and documented branch input/output power, remaining times, fault code, and documented branch-output switches when advertised | APK catalogue and `battery_en.md` |
| Gateway | Documented discovery-mode configuration switch | APK catalogue and `gateway_en.md` |
| Video intercom/doorbell | Reported active-call, battery, low-battery, and motion state when the documented video profile is advertised | CozyLife video project and `doorbell_en.md` |
| Smart speaker | X7 power, volume, play/pause, and Bluetooth/local/line-in source selection | Downloaded CozyLife X7 panel |
| AI lamp | On/off and brightness for the two Wi-Fi AI-lamp profiles | Downloaded CozyLife AI-lamp panel |
| AI companion | S3 expression selection (happy, sad, disappointed, angry, surprised) | Downloaded CozyLife S3 panel |

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
- AI conversation products, other than the two mapped AI-lamp profiles,
  remain in the evidence-gathering queue. Their
  201--203 DPID controls are not mapped in the APK resources or CozyLife's
  published DPID documentation yet. Once recovered from their panel source,
  the integration will add only entities that match verified local behavior.

## Product-audit queue

Every Wi-Fi category in the APK catalogue is in scope. The current audit has
separated products with verified local semantics from products whose controls
are still opaque:

- Electrical: 288 of 292 Wi-Fi profiles have verified entities; the four
  remaining profiles advertise no actionable DPID.
- Lights (334), motors (4), and home appliances (7) have verified local
  entities.
- Sensors: all 15 Wi-Fi profiles have documented entities. One is labelled a
  Zigbee door magnet, but the APK's authoritative transport flag is Wi-Fi and
  it shares the documented local contact profile with the other door magnets.
- Energy: 13 of 15 Wi-Fi profiles expose documented telemetry. The 12 full
  profiles that advertise branch-output power DPIDs also expose the matching
  DPID-1 output bits as local switches. The other two advertise only a
  bit-mask output DPID without a channel map, so no channels are invented.
- Gateways: the one Wi-Fi profile has a documented discovery-mode entity.
- Camera & lock: 10 of 43 profiles advertise the verified video-state DPID
  set. The remaining lock and camera controls await a verified local/session
  protocol.
- DIY: all three Wi-Fi profiles expose only opaque DPID-51 JSON.
- Smart speaker (2): both Wi-Fi X7 profiles have APK-panel-backed local media
  players. Power, volume, play/pause, and source selection are exposed.
- AI conversation (32): two AI-lamp profiles have panel-verified local light
  controls, and the S3 profile has a panel-verified expression selector. The
  remaining conversational controls stay in the evidence queue: their panel
  services are downloaded from the CozyLife API, and no local entity is
  invented until that panel source is obtained and traced.
- Outdoor travel has no Wi-Fi profiles in this APK catalogue.

## Powered-metering compatibility invariant

The APK establishes `cmd:2, attr:[0]` as the powered-product state request,
but a successful response may be partial.  A transport change is incomplete
unless it preserves all of the following:

- Keep previously received DPID state when a later listener report is partial.
- For a supported plug, request only the catalogue-advertised metering and
  option DPIDs omitted from the `attr:[0]` report.  Do not treat a successful
  `res:0` as proof that every advertised DPID was returned.
- Expose DPID 26 (**Energy**) independently from DPID 42 (**Total energy**)
  when both are advertised; neither entity may suppress the other.
- Before release, compare the powered-device DPID and fallback path against
  the last known working implementation (1.0.16), and test a partial response
  that omits DPID 27 (Current) and DPID 29 (Voltage).

This is an evidence-preservation rule, not a product-specific workaround: the
fallback is limited to DPIDs already advertised by the local product catalogue.

## Transport rule

The APK uses `cmd:2, attr:[1]` for sleeping-sensor status checks and
`cmd:2, attr:[0]` for a powered product's complete property state. The
integration preserves that split: high-frequency waking-sensor probes never
become a polling scheme for powered products.
