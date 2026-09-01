# Glowforge Utilities (`gfutilities`)

[![PyPI version](https://img.shields.io/pypi/v/gfutilities.svg)](https://pypi.org/project/gfutilities/)
[![Python versions](https://img.shields.io/pypi/pyversions/gfutilities.svg)](https://pypi.org/project/gfutilities/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

A collection of utilities to aid in firmware development for the
[Glowforge](https://glowforge.com/) laser cutter. Its centerpiece is a **machine
emulator** that authenticates to the Glowforge cloud service, speaks the
real-time control protocol, and responds to the service the way a physical
Glowforge does — useful for studying the protocol, exercising the cloud
workflow, and as a foundation for alternative control software.

---

> ## ⚠️ Disclaimer — please read
>
> This project is **not affiliated nor endorsed by Glowforge, Inc.**
>
> As Glowforge's own software is in continuous BETA, so will this software.
> It is recommended not to rely on this code for production as Glowforge does
> not publish their protocols nor do they provide any change notices.
>
> As a result, **this code may break without warning.**
>
> ## USE AT YOUR OWN RISK!

---

## Table of contents

- [What it does](#what-it-does)
- [How it works](#how-it-works)
- [Requirements](#requirements)
- [Installation](#installation)
- [Configuration](#configuration)
- [Running the emulator](#running-the-emulator)
- [Startup / action sequence](#startup--action-sequence)
- [Project layout](#project-layout)
- [Library overview](#library-overview)
- [The pulse (`.puls`) file format](#the-pulse-puls-file-format)
- [Machine settings](#machine-settings)
- [Logging](#logging)
- [Compatibility](#compatibility)
- [License](#license)

## What it does

`gfutilities` implements the machine side of the Glowforge cloud workflow:

- **Authenticates** a machine to the web service (`/machines/sign_in`) using its
  serial number and password, retrieving the session and WebSocket tokens.
- **Checks for and downloads firmware** advertised by the service.
- Opens the **real-time WebSocket control channel** to the status service and
  reacts to the service's *action* messages.
- **Emulates** a machine's responses: it reports the full machine **settings**
  schema, uploads camera images, downloads and parses **motion ("pulse")
  files**, and emits the lifecycle **events** the service expects (`:starting`,
  `:capture:*`, `:upload:*`, `:completed`, …).
- Provides helpers for working with the **pulse byte-stream** format (decoding
  motion statistics, generating simple linear moves).

The bundled [`examples/gf-machine-emulator.py`](examples/gf-machine-emulator.py)
ties these together into a runnable emulator. The `Emulator` responds to the
service with canned camera images and the downloaded motion files, so a full
homing → motion → print cycle completes without any hardware attached.

## How it works

Two channels are used, mirroring the real device:

1. **HTTPS (`requests`)** — sign-in, firmware download, image upload, and motion
   (pulse) file download.
2. **WebSocket (`websocket-client`)** — a persistent, auto-reconnecting control
   channel (subprotocol `glowforge`) carrying JSON *action* messages from the
   service and *event* messages from the machine.

```
                        ┌──────────────────────────────┐
   HTTPS  sign_in /     │     Glowforge cloud          │
   update / images /    │  app.glowforge.com (HTTPS)   │
   motion files         │  status.glowforge.com (WSS)  │
            ┌──────────▶└─────────────┬────────────────┘
            │                         │    WSS: action messages  ▲ events
            │                         ▼                          │
   ┌────────┴────────┐        ┌─────────────────┐       ┌────────┴─────────┐
   │  authentication │        │   WsClient      │──────▶│   GFUIService    │
   │  websocket(HTTP)│        │ (websocket-     │  rx   │  dispatch loop   │
   └─────────────────┘        │   client thread)│◀──────│                  │
                              └─────────────────┘  tx   └────────┬─────────┘
                                                                 │ actions
                                                                 ▼
                                                    ┌─────────────────────────┐
                                                    │  BaseMachine / Emulator │
                                                    │  (settings, images,     │
                                                    │   motion/pulse files)   │
                                                    └─────────────────────────┘
```

`GFUIService` owns the receive/transmit queues and dispatches each incoming
action to the machine object; the machine performs the work (capture, upload,
download, etc.) and pushes status events back onto the transmit queue, which the
`WsClient` drains to the service.

## Requirements

- **Python 3.8+**
- [`requests`](https://pypi.org/project/requests/) ≥ 2.31
- [`urllib3`](https://pypi.org/project/urllib3/) ≥ 2
- [`websocket-client`](https://pypi.org/project/websocket-client/) ≥ 1.7

(See [`requirements.txt`](requirements.txt) / [`setup.py`](setup.py).)

## Installation

Install the latest release from [PyPI](https://pypi.org/project/gfutilities/):

```bash
pip install gfutilities
```

Or install from source (for development, or to track `master`):

```bash
git clone https://github.com/ScottW514/Glowforge-Utilities.git
cd Glowforge-Utilities

# (recommended) create and activate a virtual environment
python -m venv .venv
# Windows:  .venv\Scripts\activate
# POSIX:    source .venv/bin/activate

pip install -e .          # editable install (or `pip install .`)
```

## Configuration

The emulator is driven by an INI-style configuration file. Copy the sample and
edit it:

```bash
cd examples
cp gf-machine-emulator.cfg.sample gf-machine-emulator.cfg
```

Configuration is parsed by [`gfutilities/configuration.py`](gfutilities/configuration.py):
section/option names are upper-cased into flat `SECTION.OPTION` keys, the literal
strings `True`/`False` become booleans, and `%(name)s` interpolation is
supported within a section.

| Section | Key | Purpose |
|---|---|---|
| `[SERVICE]` | `server_url` | HTTPS API base (default `https://app.glowforge.com`). |
| | `status_service_url` | WebSocket control URL (`wss://status.glowforge.com`). |
| `[MACHINE]` | `serial`, `password` | **Credentials** the machine signs in with (see below). |
| | `hostname`, `head_id`, `head_serial`, `head_firmware` | Optional identity overrides reported in the settings report. |
| `[FACTORY_FIRMWARE]` | `check` | Whether to query/download advertised firmware. |
| | `download_dir` | Where downloaded firmware is written. |
| | `fw_version`, `app_version` | Optional reported-version overrides. |
| `[EMULATOR]` | `base_dir` | Root for the emulator's resource folders. |
| | `image_src_dir` | Canned camera images (`HOME_1..4.jpg`, `HEAD_*.jpg`; an optional `LID_IMAGE.jpg` overrides `HOME_4.jpg` as the plain bed image). |
| | `motion_dl_dir` | Where downloaded motion/pulse files are written. |
| | `bypass_homing` | Experimental: start with the homing cycle skipped. |
| | `material_thickness` | Selects which canned head image to return. |
| `[LOGGING]` | `file`, `level`, `console_level` | Log file path and log levels (the example's handlers). |
| | `save_puls`, `save_sent_images`, `dir` | Optional debug captures of downloaded pulse files and uploaded images, written under `dir` (both off by default). |
| `[THERMAL]`, `[MOTION]` | … | Additional tunables read from the config file. |

### Obtaining your machine credentials

`serial` and `password` are derived from the i.MX6 OCOTP fuses on a real
Glowforge (the serial from `HW_OCOTP_MAC0`, the password from `HW_OCOTP_SRK0..7`).

You must have [serial](https://docs.forgefirm.org/install/serial-access/) access to your device.

Hostname is simple.  It's the identifier shown at the command prompt (use all caps).  

To obtain the serial number, enter the following in the python shell on your GF:
```python
  def read_file(filename):
      with open(filename) as f:
          return f.read()
  int(read_file('/sys/fsl_otp/HW_OCOTP_MAC0'), 16)
```
To obtain the password, enter the following in the python shell on your GF:
```python
  def read_file(filename):
      with open(filename) as f:
          return f.read()
  password = ''
  for x in range(8):
      password += "%08x" % int(read_file('/sys/fsl_otp/HW_OCOTP_SRK%d' % d), 16)
  print password
```
> **DO NOT SHARE your serial or password — they cannot be changed. Keep them secret.**

## Running the emulator

Run from the `examples/` directory so the relative `_RESOURCES` paths and the
`gf-machine-emulator.cfg` file resolve correctly:

```bash
cd examples
python gf-machine-emulator.py
```

The entry point parses `gf-machine-emulator.cfg`, configures logging, then:

```python
from gfutilities.configuration import parse
from gfutilities import GFUIService, Emulator

parse('gf-machine-emulator.cfg')
service = GFUIService(Emulator())
service.connect()   # sign in, check firmware, open the WSS channel
service.run()       # dispatch service actions until interrupted
```

## Startup / action sequence

Once connected, the service drives the machine through a sequence of actions.
The emulator handles each and replies with the appropriate events:

| Action | Emulator behavior |
|---|---|
| `settings` | Sends the full [machine settings](#machine-settings) report. |
| `update_check` | Ignored. |
| `hunt` | Downloads the focus-homing pulse file; emits `hunt:starting` / `hunt:completed`. |
| `lid_image` / `head_image` / `lidar_image` | "Captures" a canned JPEG and **uploads it to the presigned storage URL** supplied in the action's `endpoint` field; emits `:capture:*` and `:upload:*` events. |
| `motion` | Downloads and parses the motion pulse file; emits `motion:starting` / `motion:completed`. |
| `print` | Downloads the print pulse file, waits for the button, then emits the warmup/running/return-to-home/completed events. |

## Project layout

```
Glowforge-Utilities/
├── gfutilities/
│   ├── __init__.py            # exports GFUIService, Emulator, BaseMachine
│   ├── _common.py             # LOGGER_NAME, MachineSetting namedtuple
│   ├── configuration.py       # INI config parsing, get_cfg / set_cfg
│   ├── service/
│   │   ├── authentication.py  # machine sign-in (HTTPS)
│   │   ├── gfuiservice.py     # GFUIService: connect + action dispatch loop
│   │   └── websocket.py       # WSS client, HTTP helpers, image upload, pulse download
│   ├── device/
│   │   ├── basemachine.py     # BaseMachine abstract base + action threads
│   │   ├── emulator.py        # Emulator: canned-image / pulse-file machine
│   │   └── settings.py        # MACHINE_SETTINGS schema + settings report
│   └── puls/
│       └── pulsedata.py       # decode_all_steps, generate_linear_puls
├── examples/
│   ├── gf-machine-emulator.py         # runnable emulator entry point
│   ├── gf-machine-emulator.cfg.sample # configuration template
│   └── _RESOURCES/                    # IMG/ MOTION/ FW/ LOG/ assets
├── requirements.txt
├── setup.py
├── LICENSE
└── README.md
```

## Library overview

| Component | Responsibility |
|---|---|
| `GFUIService` ([service/gfuiservice.py](gfutilities/service/gfuiservice.py)) | Top-level connector: authenticates, checks firmware, opens the WSS channel, and runs the action-dispatch loop. |
| `authenticate_machine` ([service/authentication.py](gfutilities/service/authentication.py)) | Signs the machine in over HTTPS (with retry/back-off) and stores the auth/WS tokens. |
| `WsClient` + helpers ([service/websocket.py](gfutilities/service/websocket.py)) | `websocket-client` control channel plus HTTP helpers: `firmware_check` (version probe only — factory firmware is never downloaded), `img_upload`, `load_motion`, `send_wss_event`. |
| `BaseMachine` ([device/basemachine.py](gfutilities/device/basemachine.py)) | Abstract base implementing the action lifecycle and threading; concrete machines override the `_initialize`, `_head_image`, `_lid_image`, `_hunt`, `_motion`, `_button_wait`, and `_shutdown` hooks. |
| `Emulator` ([device/emulator.py](gfutilities/device/emulator.py)) | The reference `BaseMachine` implementation used by the example. |
| `settings` ([device/settings.py](gfutilities/device/settings.py)) | `MACHINE_SETTINGS` schema and the `send_report` settings-report builder. |
| `puls` ([puls/pulsedata.py](gfutilities/puls/pulsedata.py)) | `decode_all_steps` (motion statistics from a pulse stream) and `generate_linear_puls`. |

### Extending

`BaseMachine` is the extension point: subclass it (as `Emulator` does) and
implement the hardware hooks to back the cloud protocol with something other
than canned assets.

## The pulse (`.puls`) file format

Motion, hunt, and print "pulse" files describe a job. Each begins with a small
header — a magic (`GF1`), a header length, and a series of 4-character
key/value tags (machine-setting overrides for the job) — followed by a raw,
per-tick step/laser byte-stream that clocks the X/Y/Z steppers and the laser.

- `load_motion()` downloads a pulse file, parses the header (buffering across
  chunks so headers larger than one read are handled), writes the body, and
  returns header data plus computed motion statistics.
- `decode_all_steps()` decodes a pulse byte-stream into per-axis step counts and
  converts them to millimeters/inches.
- `generate_linear_puls()` produces a simple trapezoidal-profile linear move.

## Machine settings

`MACHINE_SETTINGS` in [device/settings.py](gfutilities/device/settings.py) is the
catalog of 4-character setting codes the machine exchanges with the service
(`<2-char subsystem><2-char field>`, e.g. `EFid` = exhaust-fan idle duty,
`HTvl` = head-temperature value). `send_report()` serializes the reportable
entries into the settings report the service requests at startup. Each entry is
a `MachineSetting(type, in_report, min, max, default, …)`.

## Logging

Logging uses the standard `logging` module under the logger name `openglow`.
The example configures both a console handler and a file handler; set
`[LOGGING] level` / `console_level` to `DEBUG`, `INFO`, `WARNING`, `ERROR`, or
`CRITICAL`.

## Compatibility

- Runs on current Python (3.8+; developed/tested on 3.14) with modern
  `requests` / `urllib3` 2.x / `websocket-client`.
- Exercised against Glowforge production firmware **`2.6.0-2228`**.

Because the protocol is undocumented and changes without notice, compatibility
with any given service/firmware version is **not guaranteed** — see the
disclaimer.

## License

[MIT](LICENSE) © 2026 Scott Wiederhold &lt;s.e.wiederhold@gmail.com&gt;

---

> ## ⚠️ Reminder
>
> This project is **not affiliated nor endorsed by Glowforge, Inc.**, Glowforge's
> protocols are undocumented and change without notice, and **this code may break
> without warning.**
>
> ## USE AT YOUR OWN RISK!
