# Emulator fixtures

These directories hold the assets the `gf-machine-emulator` example needs, plus
the places it writes to at run time:

| Directory | Contents |
|---|---|
| `IMG/` | Canned camera frames the emulator uploads in place of live captures: `HOME_1..4.jpg` (the head-camera homing sequence, `HOME_4.jpg` doubling as the plain bed image) and `HEAD_LASER_.230.jpg` / `HEAD_NO_LASER_.230.jpg` (the measure-laser pair). |
| `MOTION/` | Three sample pulse files (`.puls`) for exercising the pulse-file decoder and the motion path without a service session. Downloaded pulse files land here too and are ignored by git. |
| `FW/`, `LOG/` | Empty targets for firmware downloads and emulator logs; only the placeholder READMEs are tracked. |

The fixtures are machine-generated: camera frames captured on, and pulse files
produced by the Glowforge web service for, the maintainer's own machine, kept
solely as test inputs for the parsers and the emulator. Machine identity is
redacted (the serial in the pulse files is zeroed and the filenames use
`xxxxx`). They are outside the scope of this repository's MIT grant, which the
root [`LICENSE`](../../LICENSE) states.
