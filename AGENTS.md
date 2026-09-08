# AGENTS.md

Instructions for AI coding agents that work in this repository. A human
contributor is welcome to read it too: it is the same set of rules.

## Glowforge-Utilities repository rules

`gfutilities` is the factory protocol and service layer that cloud mode
uses: authentication, the WebSocket client, action dispatch, the settings
report, and pulse-file handling. It is published on PyPI, and it also
carries a machine emulator that speaks the real protocol without hardware.

The protocol is documented at
<https://docs.forgefirm.org/technical/machine/cloud-protocol/>.

### Build and test

```sh
python3 -m pytest tests/
pip install -e .          # for work on the emulator
```

### Rules specific to this repository

- **The Glowforge protocol is undocumented and can change without notice.**
  Every claim here about it comes from an observed session or from the
  factory application. Never guess a field, and label an unverified reading
  as unverified.
- **This library talks to somebody else's service.** Do not add telemetry, do
  not widen what the machine reports, and keep the client's footprint the
  size the factory's is. A user of this library must stay within the
  vendor's terms of service.
- **A debug aid must never cost a job.** The optional pulse-file and image
  captures are non-fatal on every path: a missing directory or a full disk
  drops the capture with a warning, and the job runs on.
- The package reaches PyPI through a trusted publisher. Never cut a release
  unless asked.

## Project-wide rules

The sections above are specific to this repository. The rules below apply to
every repository of the OpenGlow ForgeFIRM project: the firmware components,
the build base, the documentation, and the hardware designs. Where a
repository rule and a rule below conflict, the safety, proof, and hygiene
rules below win. The documentation site, <https://docs.forgefirm.org/>, is
the source of truth for every fact about the machine and the firmware. Read
its Developers section before you change code.

### What ForgeFIRM is

ForgeFIRM is open firmware for stock Glowforge lasers (Basic, Plus, Pro). It
runs on the factory NXP i.MX6 control board with no hardware modification. It
replaces the cloud-dependent factory userspace with an open Linux image that
controls the machine locally. In GRBL mode the machine is a standard grblHAL
controller for LightBurn and other senders on TCP port 23. Cloud mode runs the
factory experience through the Glowforge web service, and it is a deliberate,
maintained feature. The stack is hardware validated: GRBL mode cuts real jobs,
and cloud mode runs end to end.

One controller runs at a time under `forgectrl`, the machine-services daemon,
which owns the pulse device, the cooling engine, the cameras, and the control
panel. The kernel module plays the pulse stream into the stepper drivers and
owns the laser latch and the safety readbacks. The Technical section of the
site describes the whole stack.

### The repositories

All repositories live under the OpenGlow organization on GitHub,
<https://github.com/openglow-org>. For development, check every repository out
as a sibling in one base directory named `openglow-forgefirm`. The build
scripts expect that layout. Each repository is its own git repository. The
base directory is not.

| Repository | Role | Default branch | License |
|---|---|---|---|
| `forgefirm` | The build and release base: the `meta-forgefirm` Yocto layer, the kas configuration, the image recipes, the install and release scripts, the acceptance tool `forgetest/`, the bench tools `scripts/bench/`, and the bench actuator firmware `fixture/`. It carries no project documentation. | `master` | MIT (layer metadata) |
| `meta-openglow` | The BSP layers `meta-glowforge-bsp` and `meta-openglow-core`: kernel, device tree, U-Boot, board recipes. | `scarthgap` | MIT (layer metadata) |
| `forgectrl` | The machine-services daemon (C) on HTTPS port 443, with the read-only routes on HTTP port 80: supervisor, pulse-device broker, motion-liveness gate, cooling engine, cameras, telemetry, settings, diagnostics, control panel, A/B updates. | `main` | MIT |
| `grblHAL-glowforge` | The grblHAL driver for the stock board, the GRBL-mode controller. The core is a submodule at `src/grbl`. Machine constants are in `src/boards/glowforge.h`. | `main` | GPL-3.0-or-later |
| `grblHAL-core` | The fork of the grblHAL core that the driver uses. Minimum change, no rationale comments. | `forgefirm` | The upstream grblHAL license |
| `kernel-module-glowforge` | `glowforge.ko`: the SDMA + EPIT pulse engine, the laser latch, the safety readbacks, the sensors. | `master` | GPL-2.0-or-later |
| `python3-gfhardware` | The `gfhardware` hardware library and the cloud-mode applications in `forgefirm-app/`. | `master` | MIT, with one LGPL-2.1-or-later component |
| `Glowforge-Utilities` | `gfutilities` on PyPI: the factory protocol and service layer that cloud mode uses. | `master` | MIT |
| `forgefirm-docs` | The documentation site. | `main` | CC BY-SA 4.0 |
| `openglow-serial-adapter` | The USB-C serial-console adapter for the control board. | `main` | CC BY-NC-SA 4.0 |

The full table, the license details, and the component diagram are on the
site: <https://docs.forgefirm.org/developers/>.

### Safety first, and in that order

The machine is a laser, and the operator is the safety authority at it. These
rules bind every change, in every repository, that can affect what the machine
does.

- The hardware chain is the safety boundary. Software only adds gates on top
  of it. `LASER_ON` is never a bare GPIO. The kernel laser latch is locked by
  default, and every close of the pulse device locks it again.
- Never defeat a switch, an interlock, or a readback, in hardware or in
  software, not even for a test.
- Never change a fire gate, a thermal limit, or a safety default to make a
  test pass.
- The order of work is absolute: emission and motion first, robustness and
  hygiene after. Review a change that touches emission or motion for that
  before anything else.
- A change that can put energy where it was not commanded gets a regression
  test with the fix, in the same commit, never after.
- Position counters, homing anchors, and a homed flag are not proof of
  physical motion. The head accelerometer is, and so are the operator's eyes.

### Proof before done

A feature or fix is not complete until it is proven. The order of preference:

1. A host test that runs in CI. The Test page of the site lists the host tests
   of each repository.
2. A bench drill on the bench reference, recorded in the commit message that
   carries the change.
3. Documented reasoning.

Also:

- Run the host tests of this repository before every commit. Where the build
  uses `-Werror`, a warning is a failure.
- A component that ships in the image is also evaluated against the release
  acceptance catalog in the `forgefirm` repository (Test page, "Coverage
  currency"). A behavior change with no catalog consequence gets a sentence
  of justification in the commit message.
- For a component that ships in the image, the proof build is the Yocto
  cross-build that the Build page of the site defines, never a native host
  build.
- Report outcomes faithfully. Quote failing output as it is. Name a skipped
  step as skipped. Never narrate an expected result as an observed one.

### The workflow: prove locally, push when proven

- Nothing goes to a public repository until it is proven: in the host tests
  for host-only changes, on the bench for changes that alter what the machine
  does.
- Iterate locally. Then make one commit and one push for the proven change.
  No chains of fix-up pushes. Every push runs CI, and CI minutes are a budget.
- Commit only finished work, and only when asked. Never commit intermediate
  findings, working notes, plans, audits, or status files. If a conclusion is
  still moving, it is not ready for a repository. Unpushed mistakes come out
  with `git reset --mixed <base>`, which keeps the working tree.
- "Commit" and "commit and build" include the push of every touched
  repository, in dependency order. Never hold a push back to protect earlier
  unpushed commits on the same branch. The only exception is an explicit "do
  not push" for that session.
- Never push, force-push, tag, cut a release, refresh a lockfile, bump a
  submodule pointer, or bump a pin unless asked.

### Pins

A firmware component reaches the image only through its pin: an exact
`SRCREV` in `meta-forgefirm` (in the `forgefirm` repository) for ForgeFIRM
components, or in `meta-openglow` for BSP components. There is no `AUTOREV`.
A commit in a component repository changes nothing on a machine until its pin
moves. The order is always: push the source repository, bump its pin, then
run `bitbake -c fetch <recipe>` to make sure that the pin resolves. Some
pairs of repositories have a CI coupling that fixes the push order between
them. The Release flow page of the site lists them, and each affected
repository names its own in the section above.

### Git conventions

- A commit is attributed to the human who is responsible for it, in both the
  author and the committer fields. Never list an AI assistant as an author or
  a co-author. Never add an AI attribution trailer or a "Generated with" line
  to a commit message or a pull request. In a fresh clone, set the local
  `user.name` and `user.email` before the first commit.
- A commit message says what changed and why, in the present tense, in
  Simplified Technical English. The "why" of a change to fork code lives here
  and nowhere else.
- Keep line endings LF in every file that Linux tooling consumes: patches,
  shell scripts, recipes, device trees. Check the bytes after an edit from a
  Windows tool. A CRLF patch breaks `do_patch`.
- Never commit build output, `__pycache__`, virtual environments, editor
  files, a local knowledge graph (`graphify-out/`), captured machine data that
  carries an identity, tokens, keys, or passwords.
- Never rewrite published history unless the operator asks for it.
- A pull request to an upstream repository is never automatic. The developer
  decides if and when to send one. You can remind them that upstream
  candidates exist, but the decision is theirs.

### Writing rules

They apply to everything: documentation, README files, code comments,
docstrings, log and console text, commit messages, issue text, and replies.

- **American English.** analyze, behavior, color, center, gray, catalog,
  license, judgment, program, artifact, percent, toward, among, while,
  learned. The style lint in `forgefirm-docs` rejects the British forms.
- **ASD-STE100 Simplified Technical English.** Short sentences: 20 words in
  a procedure, 25 in a description. One topic per sentence, one instruction
  per sentence. Active voice, imperative in procedures, simple present in
  descriptions. "Must" for necessity, "can" for possibility. No "should",
  "would", "could", "may". Prefer: do, make sure, use, start, show, occur,
  before, after, because, for example, that is. No "etc.", no "and/or".
  Noun clusters of three words at most. Warnings and cautions come before the
  step they protect. Warm and direct is good. A joke that shades a fact is
  not.
- **No em dashes.** Never, anywhere. Use a colon, a period, or a comma.
- **Present tense, present state.** Text describes the code and the machine
  as they are. No history narrative, no "the old version did", no "evolved
  from", no story of how the code got here. Upstream copyright and
  derivation attribution to public projects stays: that is license credit,
  not narrative.
- **No workstation paths.** Nothing outside a repository appears as a path in
  it: no drive letters, no WSL mounts, no home directories, no sibling-repo
  relative paths, no private reference directories. Name the artifact
  instead: "the captured factory pulse files", "that bench session".
- **No bench-machine identity.** Never a hostname, a serial number, a fuse
  value, a private IP address, or a login of the bench machine, in any file,
  comment, commit message, sample, or test fixture. Use neutral placeholders
  of the `ABC-123` form. Record the fact of a verification, never the values.
- **No factory firmware code.** Never cite the factory firmware binary or its
  code: no offsets, no addresses, no quoted or decompiled code. State the
  recovered fact (a formula, a constant, a behavior) and stop. Hardware
  addresses (IOMUX pad values, sensor registers, boot offsets) are fine.
- **No rationale comments in fork code.** Code destined upstream (for
  example, the grblHAL core fork) carries the minimum change and no "why we
  did this" comments. The reasoning goes in the commit message. Patches
  carried in the Yocto layers are exempt. The `ForgeFIRM:` comment convention
  there is deliberate.
- **Interface docs describe the lever, not the policy.** A sysfs attribute
  doc says what the attribute is, its range, its units, its conversion. How
  the factory firmware drives it does not belong there.
- **No definitive legal or regulatory statements.** The project is not a
  lawyer. The only permitted form: modifying the machine, including
  replacing its firmware, may have legal and regulatory ramifications, and it
  is the end user's responsibility to adhere to the laws, regulations,
  certifications, and insurance terms that apply where they are. Warranty
  wording is "may void your warranty", never "voids". Never cite a regulation,
  a standard, or a certification regime as applying or not applying. The
  affiliation disclaimer lives in the site footer only. It is also the user's
  responsibility to make sure that they stay within the vendor's terms of
  service when they use a feature that calls that vendor's cloud services.
- **Copyright.** Copyright in this project belongs to 514 LLC d/b/a
  OpenGlow. Every new source file carries two lines in its header, in the
  comment style of the file: `Copyright <year> 514 LLC d/b/a OpenGlow` and
  `Written by Scott Wiederhold`. Keep the year, or the year range, that
  the file already has. The copyright notices of other holders stay
  unchanged.
- **SPDX.** Every new source file carries an `SPDX-License-Identifier` line
  under the license of its repository.

### Where documentation goes

- **One home.** A fact lives on the documentation site, or it does not exist.
  A repository README says what the repository is, how to build and test it,
  and where the documentation is. Nothing else.
- **The currency rule.** A change carries a documentation commit when it adds,
  removes, or renames an interface (a sysfs attribute, an HTTP route, a
  settings key, a G-code or `$` setting), or when it corrects a measured
  fact. A measured fact says how it was obtained, and on what: unless the page
  says otherwise, a measurement on the site was taken on the bench reference,
  which the site defines.
- **A moved document is deleted.** No stub and no redirect stays at the old
  path.
- **Every diagram is Mermaid.** No ASCII art and no image of a diagram.
- There is no roadmap page. Open items are tracked as GitHub issues once the
  repositories accept them.
- Do not add plan files, audit files, working notes, or status files to any
  repository. The project has **no status document and no dated log**: the
  site describes the present, and the record of what was done, how it was
  proven, and what it replaced goes in the commit message that carries the
  change.

### Analysis and reporting

- Rank engineering options on technical merit only: performance, correctness,
  robustness, verifiability. Process cost (an acceptance campaign, an image
  flash, a new dependency) is a one-line footnote, never a ranking factor.
  The operator decides the process cost.
- Never infer or assume a hardware fact. Label every unverified claim as
  unverified, and say how to verify it.
- Report only what the observations confirm. Everything else is labeled
  "unconfirmed" and is not acted on.

### General practice for agents

- Read the README of this repository, this file, and the Developers section
  of the site before you change code.
- Orient in the code before you edit. If a local knowledge graph
  (`graphify-out/`) exists in the working tree or its parent, query it first.
  It is a local artifact and is never committed.
- Keep a change minimal and scoped to the request. No drive-by reformatting,
  no unrelated cleanups, no renames for taste. Match the style of the
  surrounding code.
- Do not add a dependency without a stated reason. The target is a
  single-core machine with a small eMMC, and every package rides the image.
- Extend an existing test or tool instead of adding a parallel one.
- Never create a file in the repository that the request does not need.
- Ask before anything destructive or outward-facing: a push, a force-push, a
  tag, a release, a history rewrite, or any change to a machine.
- When a fact is unknown, say so and say how to find out. Never fill a gap
  with a plausible number.
- Use the exact names of the project: kas, bitbake, pin, SRCREV, campaign,
  fingerprint, drill, sysfs, the latch, the armed window. Do not invent names.

### Before you commit

1. The host tests of this repository pass, with `-Werror` where the build
   uses it.
2. The change is proven at the highest level available: CI test, bench drill,
   or documented reasoning.
3. For a component that ships in the image: the acceptance catalog covers the
   changed behavior, or the commit message says why it has no catalog
   consequence.
4. New files carry the 514 LLC d/b/a OpenGlow copyright, the `Written by`
   line, and an SPDX line. Line endings are LF.
5. No em dashes, no British spellings, no workstation paths, no bench
   identity, no factory firmware code, no history narrative, no AI
   attribution.
6. Documentation is current: interfaces and measured facts (the currency
   rule).
7. The commit is one settled change, attributed to the human author, and it
   is pushed only when the operator asks.
