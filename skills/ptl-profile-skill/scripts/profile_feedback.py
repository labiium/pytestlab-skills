#!/usr/bin/env python3
"""Retain PyTestLab CLI diagnostics without treating exit status as coverage."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import os
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import traceback
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

_LOGGER = logging.getLogger(__name__)


def junit_feedback(path: Path) -> dict:
    counts = dict.fromkeys(("passed", "failed", "errors", "skipped"), 0)
    cases = []
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError) as exc:
        return {
            "status": "incomplete",
            "reason": str(exc),
            "counts": counts,
            "cases": cases,
        }
    for case in root.iter("testcase"):
        outcome, detail = "passed", ""
        for tag, status in (
            ("error", "errors"),
            ("failure", "failed"),
            ("skipped", "skipped"),
        ):
            child = case.find(tag)
            if child is not None:
                outcome = status
                detail = "\n".join(filter(None, (child.get("message"), child.text)))
                break
        counts[outcome] += 1
        cases.append(
            {
                "class": case.get("classname", ""),
                "name": case.get("name", ""),
                "status": outcome,
                "detail": detail,
            }
        )
    if counts["failed"] or counts["errors"]:
        status, reason = "failed", "pytest reports failures or errors"
    elif not cases or counts["skipped"]:
        status, reason = (
            "incomplete",
            "no test cases or skipped/xfail cases need review",
        )
    else:
        status, reason = (
            "checks_passed",
            "recorded cases passed; capability coverage still needs review",
        )
    return {"status": status, "reason": reason, "counts": counts, "cases": cases}


def checkpoint_report(path: Path, report: dict) -> None:
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=".feedback-",
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            json.dump(report, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def process_group_running(pgid: int) -> bool:
    if sys.platform.startswith("linux"):
        # killpg(0) also finds orphaned zombies, which cannot write logs or run code.
        for entry in Path("/proc").iterdir():
            if not entry.name.isdigit():
                continue
            try:
                fields = (entry / "stat").read_text().rpartition(")")[2].split()
            except (FileNotFoundError, ProcessLookupError):
                continue
            if int(fields[2]) == pgid and fields[0] not in {"Z", "X"}:
                return True
        return False
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    return True


def close_process_group(process: subprocess.Popen) -> tuple[bool, list[str]]:
    errors = []
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            pass
        except Exception as exc:
            _LOGGER.exception("Could not signal command process group")
            errors.append(f"{signal.Signals(sig).name}: {type(exc).__name__}: {exc}")
        if sig == signal.SIGTERM:
            try:
                process.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                pass
            except Exception as exc:
                _LOGGER.exception("Could not wait for command after SIGTERM")
                errors.append(f"waiting after SIGTERM: {type(exc).__name__}: {exc}")
    try:
        process.wait(timeout=5)
    except Exception as exc:
        _LOGGER.exception("Could not reap command after SIGKILL")
        errors.append(f"reaping command after SIGKILL: {type(exc).__name__}: {exc}")
    deadline = time.monotonic() + 5
    stopped = False
    try:
        while process_group_running(process.pid):
            if time.monotonic() >= deadline:
                errors.append("process group still has live members after SIGKILL")
                break
            time.sleep(0.02)
        else:
            stopped = True
    except Exception as exc:
        _LOGGER.exception("Could not confirm command process-group termination")
        errors.append(
            f"cannot confirm process-group termination: {type(exc).__name__}: {exc}"
        )
    return stopped, errors


def run_check(
    name: str,
    command: list[str],
    output: Path,
    timeout: float,
    env: dict,
    checkpoint=None,
) -> dict:
    log_path = output / f"{name}.log"
    started = time.monotonic()
    row = {
        "name": name,
        "command": command,
        "log": log_path.name,
        "returncode": None,
        "status": "running",
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    interrupted = False
    process = None
    previous_handler = None

    def on_interrupt(signum, frame):
        nonlocal interrupted
        interrupted = True

    print(f"Running {name}: {shlex.join(command)}", flush=True)
    try:
        if os.name != "posix" or not callable(getattr(os, "killpg", None)):
            raise RuntimeError("isolated process-group execution requires POSIX killpg")
        if sys.platform.startswith("linux") and not Path("/proc/self/stat").is_file():
            raise RuntimeError("Linux process-group termination checks require /proc")
        # Deferring SIGINT across Popen prevents losing a child during its startup.
        previous_handler = signal.signal(signal.SIGINT, on_interrupt)
        if checkpoint is not None:
            checkpoint(row)
        with log_path.open("w", encoding="utf-8") as log:
            try:
                if not interrupted:
                    deadline = time.monotonic() + timeout
                    process = subprocess.Popen(
                        command,
                        stdout=log,
                        stderr=subprocess.STDOUT,
                        env=env,
                        start_new_session=True,
                    )
                    row["process_group"] = process.pid
                    if checkpoint is not None:
                        checkpoint(row)
                    while not interrupted:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            row["timed_out"] = True
                            row["reason"] = (
                                f"command exceeded {timeout:g}s; process termination is not instrument safe-off; do not replay hardware blindly"
                            )
                            break
                        try:
                            process.wait(timeout=min(remaining, 0.05))
                            break
                        except subprocess.TimeoutExpired:
                            pass
                    row["status"] = (
                        "checks_passed"
                        if process.returncode == 0 and not row.get("timed_out")
                        else "failed"
                    )
            except KeyboardInterrupt:
                interrupted = True
            except Exception as exc:
                _LOGGER.exception(
                    "Command execution failed; retaining stage diagnostics"
                )
                row.update(
                    status="failed",
                    reason=f"{type(exc).__name__}: {exc}",
                    runner_error=True,
                )
                traceback.print_exc(file=log)
            finally:
                if process is not None:
                    # Also close workers left behind by a normally exiting command.
                    stopped, errors = close_process_group(process)
                    row["returncode"] = process.returncode
                    row["process_group_stopped"] = stopped
                    if errors:
                        row.update(status="failed", cleanup_errors=errors)
                    log.flush()
                if interrupted:
                    row["status"] = "interrupted"
                    row["interrupted"] = True
                    prior = row.get("reason")
                    row["reason"] = (
                        (prior + "; " if prior else "")
                        + "interrupted; do not replay hardware blindly; process termination is not instrument safe-off"
                    )
                if "reason" in row:
                    log.write(f"\nFeedback runner: {row['reason']}\n")
                for error in row.get("cleanup_errors", []):
                    log.write(f"\nFeedback runner cleanup: {error}\n")
    except KeyboardInterrupt:
        interrupted = True
        row.update(status="interrupted", reason="interrupted before command startup")
    except Exception as exc:
        _LOGGER.exception(
            "Command startup or cleanup failed; retaining stage diagnostics"
        )
        row.update(
            status="failed", reason=f"{type(exc).__name__}: {exc}", runner_error=True
        )
    finally:
        if interrupted:
            row["status"] = "interrupted"
        row["elapsed_seconds"] = round(time.monotonic() - started, 3)
        row["finished_at"] = datetime.now(timezone.utc).isoformat()
        try:
            if checkpoint is not None:
                checkpoint(row)
                if interrupted and row["status"] != "interrupted":
                    row.update(
                        status="interrupted",
                        reason="interrupted while saving stage results",
                    )
                    checkpoint(row)
        finally:
            if previous_handler is not None:
                signal.signal(signal.SIGINT, previous_handler)
    print(f"{name}: {row['status']} ({log_path})", flush=True)
    return row


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "profile", type=Path, help="local reviewed YAML profile, not a packaged key"
    )
    parser.add_argument(
        "--ptl", default="ptl", help="target environment's ptl executable"
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="new artifact directory; never overwritten",
    )
    parser.add_argument(
        "--mode", choices=("offline", "suite", "full-test"), default="offline"
    )
    parser.add_argument(
        "--timeout", type=float, default=120, help="seconds per CLI command"
    )
    parser.add_argument(
        "--hardware-approved",
        action="store_true",
        help="acknowledge reviewed actions, identity preflight and exclusive access",
    )
    parser.add_argument(
        "--writes-approved",
        action="store_true",
        help="acknowledge reviewed writes, recovery and safe-off plan",
    )
    parser.add_argument("--backend", choices=("visa", "lamb"))
    parser.add_argument(
        "--address", help="explicit reserved VISA resource (server-side for LAMB)"
    )
    parser.add_argument(
        "--lamb-url", help="explicit approved LAMB URL, without credentials"
    )
    parser.add_argument(
        "--include-writes",
        action="store_true",
        help="full-test only; requires --writes-approved",
    )
    parser.add_argument("--timeout-ms", type=int, default=5000)
    args = parser.parse_args(argv)
    if not math.isfinite(args.timeout) or args.timeout <= 0 or args.timeout_ms <= 0:
        parser.error("timeouts must be finite and positive")
    if not args.profile.is_file() or args.profile.suffix not in {".yaml", ".yml"}:
        parser.error("profile must be an existing local YAML file")
    if args.mode == "offline":
        if any(
            (
                args.hardware_approved,
                args.writes_approved,
                args.backend,
                args.address,
                args.lamb_url,
                args.include_writes,
            )
        ):
            parser.error("hardware options require --mode suite or --mode full-test")
    else:
        if not args.hardware_approved:
            parser.error(
                "hardware execution requires --hardware-approved after manual safety review"
            )
        if args.mode == "suite":
            if not args.writes_approved:
                parser.error(
                    "the hardware suite can write, reset, recover and enable outputs; --writes-approved is required"
                )
            if any((args.backend, args.address, args.include_writes)):
                parser.error(
                    "suite uses the reviewed instance profile; --backend/--address/--include-writes apply only to full-test"
                )
        else:
            if not args.backend or not args.address:
                parser.error("full-test requires explicit --backend and --address")
            if args.backend == "lamb" and not args.lamb_url:
                parser.error(
                    "LAMB requires an explicit --lamb-url; no default server is selected"
                )
            if args.backend == "visa" and args.lamb_url:
                parser.error("--lamb-url applies only to LAMB")
            if args.include_writes and not args.writes_approved:
                parser.error("--include-writes requires --writes-approved")
    args.profile = args.profile.resolve()
    args.output = args.output.resolve()
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        args.output.mkdir(parents=True, exist_ok=False)
    except OSError as exc:
        print(f"Cannot create new artifact directory: {exc}", file=sys.stderr)
        return 1
    executable = shutil.which(args.ptl) or args.ptl
    report = {
        "format": "ptl-profile-feedback/v1",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "profile": str(args.profile),
        "profile_sha256": None,
        "cwd": str(Path.cwd()),
        "mode": args.mode,
        "ptl": executable,
        "hardware_verification": "not_asserted",
        "coverage": "manual_reconciliation_required",
        "status": "running",
        "checks": [],
    }
    path = args.output / "feedback.json"
    env = os.environ.copy()
    env.update(NO_COLOR="1", COLUMNS="240", PYTHONUNBUFFERED="1")
    status = "running"

    def record_check(row):
        if not any(existing is row for existing in report["checks"]):
            report["checks"].append(row)
        if row["status"] == "interrupted":
            report["status"] = "interrupted"
        checkpoint_report(path, report)

    try:
        checkpoint_report(path, report)
        report["profile_sha256"] = hashlib.sha256(args.profile.read_bytes()).hexdigest()
        checkpoint_report(path, report)
        commands = (
            ("schema", ["profile", "validate-schema"]),
            ("loader", ["profile", "validate"]),
            ("commands", ["instrument", "check-commands"]),
            ("operations", ["instrument", "check-operation-contract"]),
        )
        for name, subcommand in commands:
            command = [executable, *subcommand, str(args.profile)]
            if name == "operations":
                command.extend(["--strict", "--check-parameters"])
            row = run_check(
                name, command, args.output, args.timeout, env, checkpoint=record_check
            )
            if (
                row["status"] == "interrupted"
                or row.get("runner_error")
                or row.get("cleanup_errors")
            ):
                status = row["status"]
                break
        if status != "interrupted":
            status = (
                "failed"
                if any(row["status"] == "failed" for row in report["checks"])
                else "checks_passed"
            )
        if args.mode != "offline":
            if status != "checks_passed":
                report["hardware_not_run"] = (
                    "offline checks failed or were interrupted; hardware execution was not started"
                )
            else:
                env["PYTESTLAB_SIMULATE"] = "0"
                command = [
                    executable,
                    "instrument",
                    "test" if args.mode == "suite" else "full-test",
                    str(args.profile),
                ]
                if args.mode == "suite":
                    if args.lamb_url is not None:
                        command.extend(["--lamb-url", args.lamb_url])
                    # Do not inherit hidden selectors, workers or an old JUnit destination.
                    env["PYTEST_ADDOPTS"] = shlex.join(
                        [
                            "-x",
                            "-ra",
                            "-vv",
                            "--tb=short",
                            f"--junitxml={args.output / 'suite.junit.xml'}",
                        ]
                    )
                else:
                    command.extend(
                        [
                            "--backend",
                            args.backend,
                            "--address",
                            args.address,
                            "--timeout-ms",
                            str(args.timeout_ms),
                        ]
                    )
                    if args.lamb_url:
                        command.extend(["--lamb-url", args.lamb_url])
                    if args.include_writes:
                        command.extend(["--include-writes", "--yes"])
                row = run_check(
                    args.mode,
                    command,
                    args.output,
                    args.timeout,
                    env,
                    checkpoint=record_check,
                )
                if args.mode == "suite":
                    feedback = junit_feedback(args.output / "suite.junit.xml")
                    row["pytest"] = feedback
                    status = (
                        row["status"]
                        if row["status"] in {"failed", "interrupted"}
                        else feedback["status"]
                    )
                elif row["status"] in {"failed", "interrupted"}:
                    status = row["status"]
                else:
                    status = "incomplete"
                    row["review"] = (
                        "full-test has no structured report; inspect every alias/operation row, warning and skip in the log"
                    )
                record_check(row)
    except KeyboardInterrupt:
        status = "interrupted"
        report["reason"] = "interrupted; no later stages were dispatched"
        if report["checks"] and report["checks"][-1]["status"] == "running":
            report["checks"][-1].update(status="interrupted", reason=report["reason"])
    except Exception as exc:
        _LOGGER.exception("Feedback workflow failed; retaining completed stages")
        status = "failed"
        report["reason"] = f"{type(exc).__name__}: {exc}"
        if report["checks"] and report["checks"][-1]["status"] == "running":
            report["checks"][-1].update(status="failed", reason=report["reason"])
    report["status"] = status
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    report["next_action"] = (
        "Execution interrupted. Inspect retained logs and instrument state; process termination is not safe-off. Do not retry hardware blindly."
        if status == "interrupted"
        else (
            "Read failed check logs and pytest case diagnostics; fix the cause, then rerun into a new directory."
            if status == "failed"
            else "Read logs and resolve skipped/missing results against the capability matrix; do not infer full coverage from exit status."
        )
    )
    final_interrupt = False

    def retain_final_interrupt(signum, frame):
        nonlocal final_interrupt
        final_interrupt = True

    previous_handler = signal.signal(signal.SIGINT, retain_final_interrupt)
    try:
        checkpoint_report(path, report)
        if final_interrupt:
            status = report["status"] = "interrupted"
            report["next_action"] = (
                "Interrupted while retaining results. Inspect logs and instrument state; do not retry hardware blindly."
            )
            checkpoint_report(path, report)
    finally:
        signal.signal(signal.SIGINT, previous_handler)
    print(f"{status}: {path}\n{report['next_action']}")
    return {"checks_passed": 0, "failed": 1, "incomplete": 2, "interrupted": 130}[
        status
    ]


if __name__ == "__main__":
    raise SystemExit(main())
