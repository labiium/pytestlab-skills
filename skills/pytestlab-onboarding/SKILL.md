---
name: pytestlab-onboarding
description: Set up PyTestLab for a first experiment, discover available instrument resources, match or create an instrument profile, and prepare a reusable Bench configuration for a coding agent.
---

# Set up a PyTestLab bench

Get the user from an instrument and a measurement goal to a working PyTestLab project. Keep setup out of the experiment code. Prefer the existing project environment; do not replace a working installation.

## Install and inspect

For a new project, create `.venv`, activate it and install `pytestlab`. For VISA without an installed vendor runtime, add `pyvisa-py`; it supports the LAN example below. USB/GPIB may need platform drivers or additional libraries. Install only what the chosen connection needs.

```sh
python -m venv .venv
source .venv/bin/activate
python -m pip install pytestlab pyvisa-py
ptl --version
ptl --help
```

On Windows activate with `.venv\Scripts\Activate.ps1`. Record the installed version. Discover actual CLI/API support instead of assuming a development checkout matches PyPI. The public 0.2.4 baseline has `ptl profile list`, `ptl profile show`, `ptl profile validate`, `Bench.open` and `AutoInstrument.from_config`; later explicit factory methods may not exist.

## Discover the equipment

`ptl profile list` lists **supported profiles**, not physical devices. Inspect `ptl instrument --help` for version-specific instrument discovery. If no discovery subcommand exists, run this PyVISA discovery snippet in its own short Python process (not inside an active experiment session):

```python
import pyvisa

rm = pyvisa.ResourceManager()
resources = rm.list_resources()
rm.close()
print(resources)
```

Listing resources does not open them or send a command, though network discovery can contact discovery services. Restrict work to the user's bench/network. An empty list does not prove the scope is absent: use the instrument's network settings or the user-supplied VISA address. Do not scan arbitrary subnets or open every discovered resource. Do not assume the first result is the intended instrument.

Match the chosen resource to the user's model or an existing bench inventory. When needed and appropriate for that instrument, request a bounded identification query on the selected resource, not a sweep of all resources. `*IDN?` is common for SCPI devices but not universal. Never reset equipment just to discover it.

## Match or create the model profile

```sh
ptl profile list
ptl profile show keysight/DSOX1204G
```

Use the correct model/profile. No matching profile? Use the companion **ptl-profile-skill** skill to build a reusable profile from the manufacturer's programming manual and datasheet. Match the schema and driver of the installed PyTestLab version, validate offline, then verify requested operations on the authorized hardware. Do not copy another model's SCPI or limits merely because the devices look similar.

A profile implements device behavior; a bench descriptor stores lab-specific addresses and aliases. Do not put private serials, IP addresses or credentials into a reusable model profile. A new profile may need a driver/parser change if the installed driver cannot represent its behavior; don't call unsupported behavior finished.

## Create bench.yaml

For the public 0.2.4 API:

```yaml
bench_name: First measurement
instruments:
  scope:
    profile: keysight/DSOX1204G
    address: TCPIP0::192.168.1.100::inst0::INSTR
```

Replace the example resource. For a local profile use an existing YAML path in `profile:` on 0.2.4. Check the installed schema before using newer `file:` fields. Choose clear aliases such as `scope`, `generator`, or `supply`.

Write the smallest experiment with **pytestlab-experiments**. Opening a real Bench establishes instrument connections. Confirm the intended resource, wiring, limits and requested operations before execution. Do not energize outputs or reset instruments as an implicit setup step. No hardware available? Finish configuration/API checks and state that physical validation remains pending, rather than pretending simulation proves the bench works.

Deliver the bench file, short experiment, exact run command and saved data location. Explain only a concrete blocker. Avoid adding compliance/signing infrastructure when the task is a simple first measurement.
