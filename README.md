# Poolside for Home Assistant

Home Assistant integration for [Poolside Tech](https://poolside.tech) **Attendant**
pool and spa controllers.

Communication is entirely local: the integration discovers the controller on
your network via zeroconf and talks to it over an encrypted websocket session
(Noise Protocol Framework) with pushed status updates — no cloud, no polling.

## Features

- **climate** — heater/chiller control per body of water
- **fan** — variable-speed pumps and water features
- **light** — underwater/landscape lights, including effects and brightness
  where supported
- **switch** — single-speed pumps and other on/off controls
- **select** — heating/cooling mode selection
- **sensor** — water temperature, body-of-water state, site mode, and optional
  read-only pool device telemetry

## Requirements

- Poolside Tech Attendant software **2.7.0 or later**
- Administrator access to the controller (to approve the pairing request)
- The controller must be reachable on your local network

## Installation

### HACS (recommended)

1. In HACS, choose **Custom repositories** from the overflow menu, add
   `https://github.com/PoolsideStan/poolside-official-integration` with type **Integration**.
2. Search for **Poolside** in HACS and download it.
3. Restart Home Assistant.

### Manual

Copy `custom_components/poolside` into your Home Assistant `config/custom_components`
directory and restart Home Assistant.

## Configuration

Configuration is via the UI. If the controller is on the same network, it is
discovered automatically (Settings → Devices & Services). Otherwise choose
**Add Integration → Poolside** and enter the controller's host and port.

Pairing is a one-time step: the flow shows a key fingerprint and the request
must be approved on the Attendant controller itself. Administrator access to
the controller is required.

Options (Settings → Devices & Services → Poolside → Configure):

- **Expose pool devices** — create read-only telemetry sensors for the
  physical equipment (pumps, heaters, actuators, ...) the controller operates.

## Dashboard

Home Assistant builds a dashboard automatically from the devices the
integration creates, which is enough to control everything. For a hand-built
starting point, [`docs/dashboard.yaml`](docs/dashboard.yaml) is a reference
dashboard covering a two-body site: per-body views with heat, pumps, features
and lights, chemistry gauges, and an equipment view for pool-device telemetry.

Paste it into **Settings → Dashboards → Add dashboard → New dashboard from
scratch**, then **⋮ → Raw configuration editor**. It uses built-in cards only,
so no HACS front-end dependencies are needed. The entity IDs in it are
examples — the file documents which ones are fixed and which are derived from
your controller's own control names.

## Relationship to Home Assistant Core

This integration has been submitted for inclusion in Home Assistant Core.
This repository distributes the same integration via HACS in the meantime.
The protocol client lives in the separate
[aiopoolside](https://github.com/PoolsideStan/aiopoolside) library
([PyPI](https://pypi.org/project/aiopoolside/)).

Because the domain is `poolside` in both, your configuration and entities
will carry over when the core version ships — at that point, remove this
custom component and restart (an installed custom integration always
overrides the core one).

## Issues

Please report problems at
[github.com/PoolsideStan/poolside-official-integration/issues](https://github.com/PoolsideStan/poolside-official-integration/issues).

## License

MIT
