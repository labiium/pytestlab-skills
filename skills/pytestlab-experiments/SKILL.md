---
name: pytestlab-experiments
description: Write simple PyTestLab measurement procedures using Bench and public factory APIs, including channel acquisition, repeated captures, resets when requested, and analysis of saved waveform data.
---

# Write the experiment, not connection plumbing

Inspect the installed PyTestLab version and the user's bench descriptor. Reuse an existing working instrument connection when supplied; otherwise prefer a named Bench alias. Use the public factory for standalone instruments, not profile-module imports or direct construction of device classes.

```python
from pytestlab import Bench

with Bench.open("bench.yaml") as bench:
    trace = bench.scope.read_channels(1)
    trace.to_dataframe().write_csv("waveform.csv")
```

`read_channels` returns a `ChannelReadingResult`, not an `(x, y)` tuple. Its Polars dataframe contains `Time (s)` and `Channel 1 (V)` for channel 1. Verify the actual result shape when the device/API differs.

For public 0.2.4, standalone factory setup uses `AutoInstrument.from_config(profile, address_override=...)` then `connect_backend()` and closing the owned session. Newer `from_preset`/`from_file` methods are version-dependent. Prefer Bench so ordinary experiment examples do not need manual lifecycle boilerplate.

## Keep procedures small

Use direct public method calls and loops. Don't add wrappers, retries, try/finally or a measurement framework unless needed by the actual task. Use cleanup when the procedure requires a safe output shutdown; closing the connection alone does not turn a source off.

```python
with Bench.open("bench.yaml") as bench:
    scope = bench.scope
    scope.set_channel_axis(channel=1, scale=0.5, offset=0)
    scope.set_time_axis(scale=0.001, position=0)
    scope.configure_trigger(channel=1, level=0)
    for capture in range(10):
        trace = scope.read_channels(1)
        trace.to_dataframe().write_csv(f"waveform-{capture:02d}.csv")
```

Vertical scale is V/div; time scale is s/div. Set probe attenuation consistently with the physical probe and input. Choose settings for the signal, not blindly from this example. Sequential captures do not imply fixed-interval or gapless sampling.

`scope.reset()` exists in 0.2.4. Use it only when requested/approved and the reset behavior is appropriate for the connected setup. Reset can discard useful settings. Place a requested reset **before** setting up the measurement; reapply configuration afterwards. Never reset another instrument implicitly.

For scalar measurements `scope.measure_voltage_peak_to_peak(1).values` preserves the structured value. Check the installed return type before converting units or serializing. Separate scalar queries and waveform captures may refer to different acquisitions.

## Analyse acquired data locally

Use the waveform's time axis, not guessed sample rate. A compact FFT shape is:

```python
import numpy as np

frame = trace.to_dataframe()
t = frame["Time (s)"].to_numpy()
v = frame["Channel 1 (V)"].to_numpy()
v = v - v.mean()
frequency = np.fft.rfftfreq(len(v), d=t[1] - t[0])
spectrum = np.abs(np.fft.rfft(v))
```

This is an unnormalised magnitude spectrum, not calibrated spectral amplitude or power. For amplitude estimates or non-integer cycles choose a suitable window and normalization; inspect uniform time spacing and sampling adequacy. Software cannot recover clipped samples or undo aliasing.

Read the saved waveform to verify count, units, finite samples, clipping and task-relevant validity. A finite scope sentinel is not a valid measurement. Don't add unrequested grading objectives or arbitrary certification requirements. Preserve raw data and distinguish physical observations from simulations, examples and derived plots.

If the installed API or device behavior is unclear, inspect the method signature and matching profile/manual rather than inventing calls. Use raw PyVISA for a specific unsupported diagnostic when appropriate, not as a replacement for a working PyTestLab factory/Bench workflow.
