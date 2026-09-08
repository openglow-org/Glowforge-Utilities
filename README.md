# Glowforge Utilities (`gfutilities`)

The machine side of the Glowforge cloud workflow, as a Python library:
authentication, the WebSocket control channel, action dispatch, the machine
settings report, and pulse-file handling. It also carries a **machine
emulator** that speaks the real protocol without any hardware attached.

It is the protocol and service layer that
[ForgeFIRM](https://github.com/openglow-org/forgefirm)'s cloud mode is built
on, and it is useful on its own to anyone who wants to see how a Glowforge
talks to its service.

> **The Glowforge protocol is undocumented and can change without notice.**
> Compatibility with any given service or firmware version is not guaranteed.
> This project is not affiliated with or endorsed by Glowforge.

## Documentation

Everything is on **<https://docs.forgefirm.org/>**. This README is an index
card.

| Subject | Page |
|---|---|
| The wire protocol: the two channels, sign-in, the envelopes, the actions, the events, progress reporting | [Cloud protocol](https://docs.forgefirm.org/technical/machine/cloud-protocol/) |
| This library: the components, the configuration file, the emulator, the project layout, the machine settings schema | [Cloud mode](https://docs.forgefirm.org/technical/forgefirm/cloud-mode/) |
| The pulse file and its header, tag by tag | [Factory firmware](https://docs.forgefirm.org/technical/machine/factory-firmware/) |
| Getting a machine's own credentials | [Cloud mode, for the operator](https://docs.forgefirm.org/usage/cloud-mode/) |

## Install

```sh
pip install gfutilities
```

From source, for development:

```sh
git clone https://github.com/openglow-org/Glowforge-Utilities.git
cd Glowforge-Utilities
python -m venv .venv
. .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e .
```

Python 3.8 or newer, with `requests` 2.31 or newer, `urllib3` 2 or newer, and
`websocket-client` 1.7 or newer.

## Run the emulator

`examples/gf-machine-emulator.py` ties the library together into a runnable
machine. It answers the service with canned camera images and the downloaded
motion files, so a full homing, motion and print cycle completes with no
hardware. Run it from `examples/`, so its relative resource paths resolve:

```sh
cd examples
cp gf-machine-emulator.cfg.sample gf-machine-emulator.cfg
python gf-machine-emulator.py
```

The configuration file needs a machine's serial and password, which come from
the processor's fuses. **They cannot be changed, so keep them secret.**
[Cloud mode](https://docs.forgefirm.org/usage/cloud-mode/) shows how to read
them.

## Test

```sh
python3 -m pytest tests/
```

## Contributing

[AGENTS.md](AGENTS.md) carries the rules for this repository and for the
project. They apply to human contributors too.

## License

MIT. See [LICENSE](LICENSE).
