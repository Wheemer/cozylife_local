# Contributing to CozyLife Local

Thank you for helping improve local CozyLife support. This project accepts a
new entity only when its local behavior is evidenced by the bundled CozyLife
APK catalogue, a recovered panel/protocol source, or a reproducible local
device report.

## Reporting a device or compatibility problem

Open a GitHub issue with:

- the CozyLife Local version;
- the product name and any model information printed on the hardware;
- the expected and actual Home Assistant entities;
- safe CozyLife debug output containing the relevant DPID names; and
- the steps that reproduce the problem.

Enable **Verbose integration debug logs** in the affected config entry only
while reproducing the problem. The integration redacts local identity and raw
state values, but screenshots and manually added text can still expose private
information. Do not post a local IP address, device ID, Wi-Fi credentials, or
raw protocol/state payloads.

## Product-catalogue changes

`custom_components/cozylife_local/model.json` is refreshed by an automated
pull request. Do not treat a new or changed catalogue record as permission to
invent an entity mapping. A contribution that adds or changes entity support
must explain the DPID meaning and local transport evidence, update the relevant
documentation, and add or update regression coverage.

## Development and pull requests

1. Fork the repository and create a focused branch.
2. Keep unrelated formatting, catalog-refresh, and behavior changes out of the
   same pull request.
3. Run `python3 -m pytest -q` and `python3 -m compileall -q custom_components`
   before opening the pull request.
4. Describe the user-visible behavior, evidence, tests, and any migration or
   documentation effect in the pull request body.

Do not publish a release tag from a feature branch. Release preparation is a
separate reviewed change because the release workflow validates that the tag
and manifest version match.
