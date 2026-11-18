# CLI verification and repair loop

The existing CLI hardware suite is a required validation lane, not an optional suggestion. Run it and the profile-wide `full-test` when their reviewed actions are authorized. If either is unavailable or unsafe, record that lane as blocked and complete the permitted checks; do not replace it with a simulator pass or a few raw queries.

## What the two hardware commands prove

| Command | Behavior in the inspected PyTestLab checkout | Limits |
| --- | --- | --- |
| `ptl instrument test "$INSTANCE_PROFILE"` | Selects the real-hardware pytest suite for the profile's device class. Sets `PYTESTLAB_INSTRUMENT_TEST_PROFILE` and selects `requires_real_hw` tests. | Requires the source checkout's tests and pytest. Only five classes are registered. Suites may skip connection failures, write settings, recover/reset, or enable outputs. They do not exhaust all profile functionality. |
| `ptl instrument full-test "$PROFILE" --backend visa --address "$ADDRESS"` | Executes sampled profile aliases and supported semantic operations on real hardware, honoring `scpi_variant`. Writes, setup-bearing query sequences, and `self_test` are skipped by default; a live failure stops the sweep. | Query classification is not a safety proof. Queries may consume queues or initiate measurements. Sampled arguments do not exhaust channels, options, ranges or combinations. Binary aliases use text queries in the current generic runner. |

Source anchors: `pytestlab/cli.py:instrument_test`, `instrument_full_test`, `_profile_scpi_command_rows`, `_sample_semantic_operation_arguments`, `_semantic_operation_rows`, and `_print_instrument_check_table`.

`full-test` currently prints “All ... passed” even when rows were skipped. Read every row and the summary notes. Do not parse that sentence as complete coverage. It has no JSON/JUnit option in this version; the feedback helper retains its output and requires manual review rather than guessing counts from formatted tables.

## Review before execution

1. Inspect the installed CLI implementation, selected test module, availability checks, fixtures, invoked driver methods, and teardown. The `requires_real_hw` marker selects tests; it does not provide opt-in or enforce safe settings.
2. Check actual test values against the operator envelope and physical setup. For example, the inspected PSU suite enables outputs, the AWG suite includes output-enable/reset/self-test cases, and multimeter availability recovery can call `attempt_error_recovery`. Do not run these under query-only permission. Several tests lack failure-safe teardown; require a reviewed independent safe-off procedure and correct unsafe tests when in scope.
3. Confirm exact identity and exclusive access using the hardware reference before each lane. These CLIs do not enforce an expected serial identity before every action.
4. The pytest CLI command does not accept `--backend` or `--address`. Supply a reviewed local instance YAML through the repository-supported backend/address fields. For a built-in LAMB backend, including the no-address default, pass explicit `--lamb-url "$LAMB_URL"`: the CLI pins `LAMB_SERVER` and the suite-scoped approved URL for that pytest run and restores previous environment values afterward. A URL in `backend.url`, an ignored top-level `lamb_url`, or an inherited environment value is not approval and does not replace this option. Keep specimen-specific data out of the reusable model profile. Record both model and instance hashes and their differences; SCPI/specification/operation content must match the candidate, apart from reviewed instance overrides.
5. Inspect `devices/factory.py:_resolve_simulation_mode` and `_build_backend`: suites call `AutoInstrument.from_config` without explicit `simulate=False`, so `PYTESTLAB_SIMULATE` and the profile backend matter. Use a real backend, disable simulation, and verify the actual selected transport. Custom imports/backends and pytest plugins/configuration also need review. Never count replay/simulation as physical verification.
6. `full-test` defaults to LAMB with a default server if none is supplied. Always pass the reserved address and explicit backend; for LAMB also pass the approved URL. Add `--include-writes --yes` only after approving the entire generated command/operation sweep, including sample arguments, reset/clear/output actions, and cleanup.

If a suite is missing in an installed wheel, locate/use the intended source checkout or report the suite lane blocked. `full-test` provides different evidence; it is not a silent substitute for the missing pytest suite. If no registered suite exists for the device class, report this and implement appropriate public-API cases when in scope.

Use a CLI that implements selected-variant-aware checks, setup-write/query dispatch and fail-fast live sweeps. The alias and semantic passes stop after a live error, and semantic checks do not start after a failed alias pass. Offline command-build checks still collect independent errors. The pytest suite stops after its first failing case. If the installed version lacks these behaviors, block the affected hardware lane until it is updated; do not compensate by altering valid profile syntax.

## One-command feedback capture

Use the Python helper in this skill with the target environment's `ptl` executable. It uses only Python's standard library. Resolve the script path from the skill directory; examples below run from the workspace root.

Start offline after every profile edit:

```sh
python ptl-profile-skill/scripts/profile_feedback.py "$PROFILE" \
  --ptl "$PTL_EXECUTABLE" --output "$RUN_DIR/offline-01"
```

It runs schema, loader, command-build, and strict operation/parameter-contract CLI checks. It continues independent offline checks so one failure does not hide other diagnostics. It does not install packages or initiate a hardware lane by default. As with the underlying CLI, use only trusted reviewed drivers/config models.

For an approved LAMB pytest hardware suite:

```sh
python ptl-profile-skill/scripts/profile_feedback.py "$INSTANCE_PROFILE" \
  --ptl "$PTL_EXECUTABLE" --output "$RUN_DIR/suite-01" --mode suite \
  --lamb-url "$LAMB_URL" --hardware-approved --writes-approved --timeout 600
```

For a VISA suite omit `--lamb-url`; the instance profile must select the reserved VISA resource. The acknowledgements mean identity preflight, exclusive access, suite actions/values, recovery, and safe-off have already been reviewed with the operator. They are not an instrument interlock. The runner repeats offline gates first and will not start hardware if they fail. It sets `PYTESTLAB_SIMULATE=0` and replaces `PYTEST_ADDOPTS` with fail-fast verbose diagnostics and a fresh JUnit destination. Other pytest configuration/plugins and explicit simulator backends are not neutralized: inspect them yourself. The helper does not support hidden test filtering or parallel hardware execution.

For an approved profile-wide LAMB sweep, initially omitting write aliases:

```sh
python ptl-profile-skill/scripts/profile_feedback.py "$PROFILE" \
  --ptl "$PTL_EXECUTABLE" --output "$RUN_DIR/full-01" --mode full-test \
  --backend lamb --address "$ADDRESS" --lamb-url "$LAMB_URL" \
  --hardware-approved --timeout-ms 5000 --timeout 600
```

For VISA use `--backend visa --address "$ADDRESS"` without `--lamb-url`. Only if the whole write sweep is approved, add `--include-writes --writes-approved`; the runner then passes the CLI's required `--yes`. This also authorizes setup writes preceding a query; the CLI writes setup messages and queries the final response-producing message. Never add those switches simply to reduce skips. If the sweep exceeds approved scope, leave it blocked and test the permitted individual capabilities explicitly.

Timeouts bound a command, not hardware safety. The runner launches commands in isolated process groups on supported POSIX platforms, terminates the group on timeout/interruption, and reaps the launched process; unsupported platforms fail explicitly rather than claim process-tree containment. Programs that deliberately detach into another session escape this boundary and must not be used as validation commands. Terminating a hung CLI may bypass its teardown and leave outputs active. The helper does not reset equipment, retry commands, restore settings, verify identity, or perform safe-off. Confirm the final physical state yourself, especially after failure, timeout or interruption. It never automatically reruns a live lane after a fix.

Every invocation requires a new output directory and produces:

- `feedback.json`: profile hash/path, invocation context, commands, return codes, durations, log paths, status, and next action. Atomic checkpoints retain the initial report and each started/completed stage; interruption preserves prior results and marks the active stage interrupted. Hardware verification is always `not_asserted`; coverage always requires capability reconciliation.
- One combined stdout/stderr log per command, preserving failures and warnings without terminal-table scraping.
- For a successfully launched pytest suite, `suite.junit.xml`; parsed case outcomes/reasons also appear in JSON. Missing/malformed/empty JUnit or skipped/xfail cases produce incomplete feedback, not success. JUnit does not enumerate deselected tests or establish every instrument assertion, so inspect the log and expected suite inventory too.

Exit codes: `0` means the executed checks passed, not that every capability is verified; `1` means a command/test failed; `2` means incomplete results requiring review or a rejected invocation; `130` means interrupted, with feedback retained. A successful `full-test` invocation yields `2` because its human-readable rows/skips still need reconciliation. A nonzero hardware command is a failure, never automatically converted into a hardware-unavailable skip.

Artifacts can contain unit identifiers and instrument data. Use an access-controlled location, do not embed credentials in URLs/arguments, and redact before sharing. Do not edit the profile while a run is in progress.

## Turn diagnostics into corrections

1. Read `feedback.json`, failed command logs, and pytest failure/error/skip details. Preserve the first failure and original instrument errors; do not clear them merely to make a retry pass.
2. Map every profile command/query alias and every advertised public operation to its CLI row, pytest case and assertion, or an explicit supplemental test. Include channels, semantic choices, conditional ranges, options and binary paths where applicable. A test name alone is not evidence that all its sub-operations executed before an earlier assertion failed.
3. For each missing or failing capability record: source citation, alias/operation, expected behavior, observed response/error, cause category, proposed correction, and the exact targeted rerun. Distinguish a wrong SCPI mapping from absent license, setup/no-signal, transport framing, unsupported test assumptions, or unsafe permission scope.
4. Correct the smallest actual cause in the candidate or in-scope driver/test. Do not weaken assertions, disable supported features, suppress uncertainty errors, or reclassify unexpected exceptions as skips. For a test that hardcodes another model's capabilities, make it profile-aware when in scope while preserving behavioral assertions.
5. Rerun offline gates into a new directory. Once the safe state is reestablished, explicitly rerun the affected approved hardware cases and then the reviewed broader lanes for final evidence. Keep before/after artifact paths and hashes. Do not blindly replay a timed-out write.
6. Update the capability matrix and report counts of passed, failed, unsupported, blocked and untested capabilities separately. Completion requires every claimed capability to have suitable physical PyTestLab evidence; unexecuted, skipped, or unsupported cases do not count as passes. Deliver any remaining blockers explicitly.

The feedback runner collects diagnostics. The agent owns cause analysis, coverage reconciliation and safe final-state confirmation.
