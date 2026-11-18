# PyTestLab profile skill

An agent skill for authoring instrument profiles from PDF programming guides and datasheets, then checking them through PyTestLab against real instruments. Supports workflows using PyVISA, LAMB, or other available lab tooling without prescribing one transport.

## Use

Place this directory in your agent's skills search path as `ptl-profile-skill`, or ask the agent to read `SKILL.md` directly. The skill does not install packages, discover network devices, or send instrument commands on its own.

Example request, with paths and connection details supplied for your lab:

> Use ptl-profile-skill to create a PyTestLab profile for the model covered by these programming-guide and datasheet PDFs. Use the PyTestLab checkout and existing bench configuration in this workspace. Inspect the available transport, document the proposed hardware tests, and confirm the permitted settings before changing instrument state. Validate the profile through public PyTestLab APIs and report any unsupported or untested capabilities.

Supply or make discoverable:

- Programming guide and datasheet, including applicable firmware/option supplements.
- Target PyTestLab environment and requested capability coverage.
- For live verification: explicit resource, expected identity, exclusive access, physical setup, allowed actions and limits, and safe final state.

Without hardware, the agent can author the profile and complete offline checks. It must label live verification blocked. A real-device run also requires operator-approved actions; possessing a VISA address or LAMB URL does not authorize writes.

## Public-release compatibility

Start with the installed release, not this skill's authoring version. The skill
includes a release-compatibility gate before any profile or command examples.
The feedback runner needs the newer four-command offline CLI; older releases
must use their available validators individually. Missing checks remain visible.
No claim is made that the entire runner works on PyPI 0.2.4.

## Get feedback after an edit

Run from the workspace root, using the target environment's CLI:

```sh
python ptl-profile-skill/scripts/profile_feedback.py "$PROFILE" \
  --ptl "$PTL_EXECUTABLE" --output "$RUN_DIR/offline-01"
```

Read `feedback.json` and the named logs, correct the reported causes, and rerun into a new directory. The default mode runs only offline gates. Reports are checkpointed atomically before and after commands; Ctrl-C retains the active interrupted stage and prior results rather than losing the report.

The skill also requires `ptl instrument test` (the existing profile-selected hardware pytest suite) and `ptl instrument full-test` when their actions are authorized. The runner's `suite` and `full-test` modes retain their diagnostics, including pytest JUnit. LAMB suites require explicit `--lamb-url "$LAMB_URL"`; inherited defaults and YAML URL fields do not replace approval. Live lanes stop on failure. Hardware runs are never automatic: review settings, recovery/reset/output actions, identity preflight and safe-off first. Terminating a timed-out command group is not physical safe-off.

See [CLI feedback and repair](references/cli-feedback.md) for exact invocations, approval flags, exit codes, and coverage reconciliation. Passed checks do not imply every profile capability was exercised; skipped, blocked and uncovered functionality must remain visible.

## Contents

- `SKILL.md`: discovery, document extraction, profile authoring, offline gates, live verification, and delivery criteria.
- `references/profile-authoring.md`: PyTestLab schema, semantic operations, specifications, and validation APIs.
- `references/hardware-validation.md`: transport examples, hardware safety, public API verification, and evidence handling.
- `references/cli-feedback.md`: mandatory CLI hardware lanes, feedback capture, safety limits, and the repair loop.
- `scripts/profile_feedback.py`: standard-library runner; offline by default, with JSON/logs and explicitly approved hardware modes.
- `tests/test_profile_feedback.py`: regression tests for result interpretation and hardware authorization boundaries.
- `evals/evals.json`: scenario prompts and expected behavior for evaluating an agent with this skill.

The references describe the API shape found in the local PyTestLab 0.3.0 checkout during authoring. Agents must inspect their target version rather than assuming those APIs are universal. CLI examples require the target environment's `ptl` executable.

## Evaluate

Run each prompt in `evals/evals.json` in a separate agent session with the skill loaded. Supply fixture documents and an isolated test workspace where a scenario needs them. Evaluate the recorded actions and artifacts against every expectation. These are evaluation specifications, not claims that live hardware tests have run. Never use an energized or shared instrument as an unattended evaluation fixture.

## Authoring verification

Checked against the local PyTestLab 0.3.0 checkout:

- Skill frontmatter, relative Markdown links, and eight evaluation specifications parse correctly.
- All four YAML fragments validate against the current PyTestLab models. The documented Python validator ran on a temporary, schema-valid fragment harness; this is not a complete instrument profile.
- The bundled EDU34450A passed schema, loader/operation-contract, all 20 command-build checks, and the strict operation-contract CLI check.
- The deeper canonical parameter validator rejected that bundled profile's missing `set_range_auto.state` metadata. The skill retains this diagnostic rather than treating command-build success as complete parameter coverage.
- The hardware example rejects execution without opt-in. Its documented pytest case produced `1 skipped`, with no hardware contact.
- The feedback runner completed all four offline CLI gates on the bundled EDU34450A and retained JSON plus per-command logs.
- A temporary incomplete candidate produced failed diagnostics; replacing it with the known profile produced passing offline checks in a new directory with a different hash, preserving the original report.
- The final focused PyTestLab CLI suites passed 88 tests; the feedback runner passed 21 regression tests. Ruff checks passed for the changed Python files.
- CLI hardware modes rejected missing hardware/write approvals before creating artifacts or executing checks. Existing report directories were not overwritten.
- Selected variants work with and without a default. Approved setup/query sequences write prerequisites before reading the final response; live failures stop later aliases/semantic operations. LAMB suites reject inherited/default URL approval and pin the explicitly approved server.
- Actual feedback-runner smoke checks completed all four offline gates, retained a completed schema and interrupted loader on Ctrl-C with exit 130, and stopped a SIGTERM-ignoring descendant before returning from timeout.

No physical instrument or live CLI hardware lane was tested, and the eight agent evaluation scenarios have not been executed.

Run the feedback regression tests with:

```sh
python -m unittest discover -s ptl-profile-skill/tests -v
```
