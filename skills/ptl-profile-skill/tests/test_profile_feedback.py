import hashlib
import importlib.util
import io
import json
import os
import signal
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "profile_feedback.py"
spec = importlib.util.spec_from_file_location("profile_feedback", SCRIPT)
feedback = importlib.util.module_from_spec(spec)
spec.loader.exec_module(feedback)


class JUnitFeedbackTests(unittest.TestCase):
    def parse(self, xml):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "suite.xml"
            path.write_text(xml)
            return feedback.junit_feedback(path)

    def test_failure_and_setup_error_are_not_hidden_by_passing_cases(self):
        result = self.parse("""<testsuites><testsuite>
            <testcase classname="scope" name="identity"/>
            <testcase classname="scope" name="waveform"><failure message="wrong scaling">expected volts</failure></testcase>
            <testcase classname="scope" name="trigger"><error message="setup failed">timeout</error></testcase>
            </testsuite></testsuites>""")
        self.assertEqual(result["status"], "failed")
        self.assertEqual(
            result["counts"], {"passed": 1, "failed": 1, "errors": 1, "skipped": 0}
        )
        self.assertEqual(result["cases"][1]["detail"], "wrong scaling\nexpected volts")
        self.assertEqual(result["cases"][2]["status"], "errors")

    def test_skip_and_expected_failure_are_incomplete_not_passes(self):
        result = self.parse("""<testsuite>
            <testcase name="identity"/>
            <testcase name="optional"><skipped message="missing license"/></testcase>
            <testcase name="known_bug"><skipped type="pytest.xfail">expected failure</skipped></testcase>
            </testsuite>""")
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(result["counts"]["skipped"], 2)
        self.assertEqual(result["counts"]["passed"], 1)
        self.assertEqual(result["cases"][1]["detail"], "missing license")

    def test_empty_malformed_and_missing_reports_are_incomplete(self):
        for xml in ("<testsuites/>", "<testsuite>"):
            with self.subTest(xml=xml):
                self.assertEqual(self.parse(xml)["status"], "incomplete")
        with tempfile.TemporaryDirectory() as directory:
            result = feedback.junit_feedback(Path(directory) / "missing.xml")
            self.assertEqual(result["status"], "incomplete")
            self.assertEqual(result["counts"]["passed"], 0)

    def test_success_does_not_claim_complete_capability_coverage(self):
        result = self.parse(
            '<testsuite><testcase classname="dmm" name="identity"/></testsuite>'
        )
        self.assertEqual(result["status"], "checks_passed")
        self.assertIn("coverage still needs review", result["reason"])


class HardwareAuthorizationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.profile = Path(self.temp.name) / "candidate.yaml"
        self.profile.write_text("manufacturer: example\n")
        self.base = [str(self.profile), "--output", str(Path(self.temp.name) / "run")]

    def rejected(self, extra):
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
            feedback.parse_args(self.base + extra)
        self.assertEqual(raised.exception.code, 2)

    def test_hardware_and_state_changes_require_separate_acknowledgements(self):
        self.rejected(["--mode", "suite"])
        self.rejected(["--mode", "suite", "--hardware-approved"])
        self.rejected(
            [
                "--mode",
                "full-test",
                "--hardware-approved",
                "--backend",
                "visa",
                "--address",
                "reserved-resource",
                "--include-writes",
            ]
        )

    def test_hardware_selection_never_uses_an_implicit_server_or_resource(self):
        self.rejected(["--mode", "full-test", "--hardware-approved"])
        self.rejected(
            [
                "--mode",
                "full-test",
                "--hardware-approved",
                "--backend",
                "lamb",
                "--address",
                "reserved-resource",
            ]
        )
        self.rejected(
            [
                "--mode",
                "suite",
                "--hardware-approved",
                "--writes-approved",
                "--address",
                "ignored-resource",
            ]
        )

    def test_suite_rejects_full_test_only_selection_options(self):
        for option in (["--backend", "visa"], ["--include-writes"]):
            with self.subTest(option=option):
                self.rejected(
                    [
                        "--mode",
                        "suite",
                        "--hardware-approved",
                        "--writes-approved",
                        *option,
                    ]
                )

    def test_nonfinite_and_unbounded_deadlines_are_rejected(self):
        for timeout in ("0", "-1", "nan", "inf"):
            with self.subTest(timeout=timeout):
                self.rejected(["--timeout", timeout])


@unittest.skipUnless(os.name == "posix", "process-group safety requires POSIX")
class RunnerLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.profile = self.root / "candidate.yaml"
        self.profile.write_text("manufacturer: reviewed-example\n")
        self.output = self.root / "run"
        self.behavior = self.root / "behavior.json"
        self.behavior.write_text("{}")
        self.worker = self.root / "worker.py"
        self.worker.write_text(
            textwrap.dedent("""
            import os
            import signal
            import subprocess
            import sys
            import time
            from pathlib import Path

            root = Path(__file__).parent
            role = sys.argv[1]
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
            if role == "child":
                subprocess.Popen([sys.executable, __file__, "grandchild"])
            (root / (role + ".pid")).write_text(str(os.getpid()))
            until = time.monotonic() + 8
            with (root / "heartbeat").open("a") as stream:
                while time.monotonic() < until:
                    stream.write(role + "\\n")
                    stream.flush()
                    print("worker diagnostic " + role, flush=True)
                    time.sleep(0.025)
        """)
        )
        self.ptl = self.root / "ptl-fixture"
        self.ptl.write_text(
            "#!"
            + sys.executable
            + "\n"
            + textwrap.dedent("""
            import json
            import os
            import signal
            import subprocess
            import sys
            import time
            from pathlib import Path

            root = Path(__file__).parent
            behavior = json.loads((root / "behavior.json").read_text())
            stage = {
                "validate-schema": "schema", "validate": "loader",
                "check-commands": "commands", "check-operation-contract": "operations",
                "test": "suite", "full-test": "full-test",
            }[sys.argv[2]]
            snapshot = json.loads((root / "run" / "feedback.json").read_text())
            (root / (stage + ".snapshot.json")).write_text(json.dumps(snapshot))
            print("retained diagnostic " + stage, flush=True)
            if stage == "suite" and "junit" in behavior:
                (root / "run" / "suite.junit.xml").write_text(behavior["junit"])
            if behavior.get("block") == stage:
                def retain_term(signum, frame):
                    (root / "term-received").touch()
                signal.signal(signal.SIGTERM, retain_term)
                subprocess.Popen([sys.executable, str(root / "worker.py"), "child"])
                until = time.monotonic() + 8
                while time.monotonic() < until:
                    print("leader diagnostic", flush=True)
                    time.sleep(0.025)
            if stage == "suite":
                if "--lamb-url" in sys.argv:
                    os.environ["APPROVED_TEST_URL"] = sys.argv[sys.argv.index("--lamb-url") + 1]
                raise SystemExit(subprocess.call(
                    [sys.executable, "-m", "pytest", str(root / "hardware_suite.py")]
                ))
            raise SystemExit(behavior.get("exit", {}).get(stage, 0))
        """)
        )
        self.ptl.chmod(0o755)

    def arguments(self, *extra):
        return [
            str(self.profile),
            "--ptl",
            str(self.ptl),
            "--output",
            str(self.output),
            "--timeout",
            "3",
            *extra,
        ]

    def spawn(self, *extra, env=None):
        process = subprocess.Popen(
            [sys.executable, str(SCRIPT), *self.arguments(*extra)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
            env=env,
        )
        self.addCleanup(self.stop_runner, process)
        return process

    def stop_runner(self, process):
        if process.poll() is None:
            process.send_signal(signal.SIGINT)
            try:
                process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()
        path = self.output / "feedback.json"
        if path.exists():
            report = json.loads(path.read_text())
            for row in report["checks"]:
                if row["status"] == "running" and "process_group" in row:
                    try:
                        os.killpg(row["process_group"], signal.SIGKILL)
                    except ProcessLookupError:
                        pass
        for role in ("child", "grandchild"):
            path = self.root / (role + ".pid")
            if path.exists():
                try:
                    os.kill(int(path.read_text()), signal.SIGKILL)
                except ProcessLookupError:
                    pass

    def await_tree(self, process=None):
        deadline = time.monotonic() + 5
        while (
            not (self.root / "grandchild.pid").exists()
            or not (self.root / "heartbeat").exists()
        ):
            if process is not None and process.poll() is not None:
                out, err = process.communicate()
                self.fail(f"runner exited before tree started: {out}\n{err}")
            if time.monotonic() >= deadline:
                self.fail("finite subprocess tree did not start")
            time.sleep(0.01)

    def assert_tree_stopped(self):
        heartbeat = self.root / "heartbeat"
        before = heartbeat.read_bytes()
        time.sleep(0.15)
        self.assertEqual(heartbeat.read_bytes(), before)
        if sys.platform.startswith("linux"):
            for role in ("child", "grandchild"):
                pid = (self.root / (role + ".pid")).read_text()
                stat = Path("/proc") / pid / "stat"
                if stat.exists():
                    state = stat.read_text().rpartition(")")[2].split()[0]
                    self.assertIn(state, {"Z", "X"}, f"{role} is still running")

    def report(self):
        return json.loads((self.output / "feedback.json").read_text())

    def test_started_and_completed_stages_are_durable_before_next_dispatch(self):
        process = self.spawn()
        out, err = process.communicate(timeout=10)
        self.assertEqual(process.returncode, 0, out + err)
        for index, stage in enumerate(("schema", "loader", "commands", "operations")):
            snapshot = json.loads((self.root / (stage + ".snapshot.json")).read_text())
            self.assertEqual(snapshot["status"], "running")
            self.assertEqual(len(snapshot["checks"]), index + 1)
            self.assertEqual(snapshot["checks"][-1]["status"], "running")
            self.assertEqual(
                snapshot["profile_sha256"],
                hashlib.sha256(self.profile.read_bytes()).hexdigest(),
            )
            for completed in snapshot["checks"][:-1]:
                self.assertEqual(completed["status"], "checks_passed")
                self.assertEqual(completed["returncode"], 0)
                self.assertIn("finished_at", completed)
        self.assertEqual(self.report()["status"], "checks_passed")
        self.assertFalse(list(self.output.glob(".feedback-*")))

    def test_initial_report_survives_unexpected_stage_exception(self):
        def broken_stage(*args, **kwargs):
            initial = self.report()
            self.assertEqual(initial["status"], "running")
            self.assertEqual(initial["checks"], [])
            self.assertIsNotNone(initial["profile_sha256"])
            raise RuntimeError("dispatch failed unexpectedly")

        with (
            mock.patch.object(feedback, "run_check", side_effect=broken_stage),
            redirect_stderr(io.StringIO()),
        ):
            self.assertEqual(feedback.main(self.arguments()), 1)
        report = self.report()
        self.assertEqual(report["status"], "failed")
        self.assertIn("dispatch failed unexpectedly", report["reason"])
        self.assertIn("finished_at", report)

    def test_atomic_checkpoint_keeps_previous_json_on_replacement_failure(self):
        self.output.mkdir()
        path = self.output / "feedback.json"
        feedback.checkpoint_report(path, {"status": "running", "checks": []})
        with (
            mock.patch.object(
                feedback.os, "replace", side_effect=OSError("disk unavailable")
            ),
            self.assertRaises(OSError),
        ):
            feedback.checkpoint_report(
                path, {"status": "failed", "checks": [{"name": "schema"}]}
            )
        self.assertEqual(self.report(), {"status": "running", "checks": []})
        self.assertEqual(list(self.output.glob(".feedback-*")), [])

    def test_timeout_stops_real_descendants_and_retains_truthful_diagnostics(self):
        self.behavior.write_text(json.dumps({"block": "loader"}))
        process = self.spawn("--timeout", "1.5")
        self.await_tree(process)
        out, err = process.communicate(timeout=10)
        self.assertEqual(process.returncode, 1, out + err)
        report = self.report()
        self.assertEqual(report["status"], "failed")
        row = report["checks"][1]
        self.assertEqual(row["name"], "loader")
        self.assertEqual(row["status"], "failed")
        self.assertTrue(row["timed_out"])
        self.assertTrue(row["process_group_stopped"])
        self.assertEqual(row["returncode"], -signal.SIGKILL)
        self.assertIn("exceeded", row["reason"])
        log = self.output / row["log"]
        self.assertIn("retained diagnostic loader", log.read_text())
        self.assertIn("grandchild", log.read_text())
        before = log.read_bytes()
        self.assert_tree_stopped()
        self.assertEqual(log.read_bytes(), before)

    def test_interrupt_during_timeout_cleanup_retains_both_causes(self):
        self.behavior.write_text(json.dumps({"block": "schema"}))
        process = self.spawn("--timeout", "1.5")
        self.await_tree(process)
        deadline = time.monotonic() + 5
        while not (self.root / "term-received").exists():
            if process.poll() is not None or time.monotonic() >= deadline:
                self.fail("runner did not enter timeout cleanup")
            time.sleep(0.005)
        process.send_signal(signal.SIGINT)
        out, err = process.communicate(timeout=10)
        self.assertEqual(process.returncode, 130, out + err)
        report = self.report()
        self.assertEqual(report["status"], "interrupted")
        self.assertEqual(len(report["checks"]), 1)
        row = report["checks"][0]
        self.assertTrue(row["timed_out"])
        self.assertEqual(row["status"], "interrupted")
        self.assertIn("exceeded", row["reason"])
        self.assertIn("interrupted", row["reason"])
        self.assertTrue(row["process_group_stopped"])
        self.assert_tree_stopped()

    def test_interrupt_preserves_completed_checks_and_stops_dispatch_and_cleanup_interrupts(
        self,
    ):
        self.behavior.write_text(json.dumps({"block": "loader"}))
        process = self.spawn(
            "--mode", "suite", "--hardware-approved", "--writes-approved"
        )
        self.await_tree(process)
        running = self.report()
        self.assertEqual(
            [row["status"] for row in running["checks"]], ["checks_passed", "running"]
        )
        process.send_signal(signal.SIGINT)
        time.sleep(0.1)
        process.send_signal(signal.SIGINT)
        out, err = process.communicate(timeout=10)
        self.assertEqual(process.returncode, 130, out + err)
        report = self.report()
        self.assertEqual(report["status"], "interrupted")
        self.assertEqual(report["profile_sha256"], running["profile_sha256"])
        self.assertEqual(report["checks"][0], running["checks"][0])
        self.assertEqual(len(report["checks"]), 2)
        row = report["checks"][1]
        self.assertEqual(row["status"], "interrupted")
        self.assertTrue(row["process_group_stopped"])
        self.assertEqual(row["returncode"], -signal.SIGKILL)
        self.assertIn("not instrument safe-off", row["reason"])
        self.assertIn(
            "retained diagnostic loader", (self.output / row["log"]).read_text()
        )
        self.assertIn("hardware_not_run", report)
        self.assertFalse((self.root / "commands.snapshot.json").exists())
        self.assertFalse((self.root / "suite.snapshot.json").exists())
        self.assert_tree_stopped()

    def test_interrupt_during_process_startup_cannot_lose_the_spawned_tree(self):
        self.output.mkdir()
        self.behavior.write_text(json.dumps({"block": "schema"}))
        (self.output / "feedback.json").write_text('{"checks": []}')
        real_popen = subprocess.Popen

        def interrupted_start(*args, **kwargs):
            process = real_popen(*args, **kwargs)
            self.addCleanup(self.stop_runner, process)
            self.await_tree(process)
            os.kill(os.getpid(), signal.SIGINT)
            return process

        with mock.patch.object(
            feedback.subprocess, "Popen", side_effect=interrupted_start
        ):
            row = feedback.run_check(
                "schema",
                [str(self.ptl), "profile", "validate-schema", str(self.profile)],
                self.output,
                3,
                os.environ.copy(),
            )
        self.assertEqual(row["status"], "interrupted")
        self.assertEqual(row["returncode"], -signal.SIGKILL)
        self.assertTrue(row["process_group_stopped"])
        self.assert_tree_stopped()

    def test_timeout_already_expired_during_startup_still_closes_spawned_tree(self):
        self.output.mkdir()
        self.behavior.write_text(json.dumps({"block": "schema"}))
        (self.output / "feedback.json").write_text('{"checks": []}')
        real_popen = subprocess.Popen

        def delayed_start(*args, **kwargs):
            process = real_popen(*args, **kwargs)
            self.addCleanup(self.stop_runner, process)
            self.await_tree(process)
            time.sleep(0.15)
            return process

        with mock.patch.object(feedback.subprocess, "Popen", side_effect=delayed_start):
            row = feedback.run_check(
                "schema",
                [str(self.ptl), "profile", "validate-schema", str(self.profile)],
                self.output,
                0.1,
                os.environ.copy(),
            )
        self.assertEqual(row["status"], "failed")
        self.assertTrue(row["timed_out"])
        self.assertEqual(row["returncode"], -signal.SIGKILL)
        self.assertTrue(row["process_group_stopped"])
        self.assert_tree_stopped()

    def test_unsupported_process_group_control_fails_before_dispatch(self):
        self.output.mkdir()
        marker = self.root / "must-not-launch"
        with mock.patch.object(feedback.os, "killpg", new=None):
            row = feedback.run_check(
                "unsupported",
                [
                    sys.executable,
                    "-c",
                    f"from pathlib import Path; Path({str(marker)!r}).touch()",
                ],
                self.output,
                1,
                os.environ.copy(),
            )
        self.assertEqual(row["status"], "failed")
        self.assertIn("requires POSIX killpg", row["reason"])
        self.assertIsNone(row["returncode"])
        self.assertFalse(marker.exists())

    def test_interrupted_suite_retains_partial_junit_diagnostics(self):
        self.behavior.write_text(
            json.dumps(
                {
                    "block": "suite",
                    "junit": '<testsuite><testcase name="setup"><error message="partial setup">retained detail</error></testcase></testsuite>',
                }
            )
        )
        process = self.spawn(
            "--mode", "suite", "--hardware-approved", "--writes-approved"
        )
        self.await_tree(process)
        running = self.report()
        self.assertEqual(len(running["checks"]), 5)
        self.assertEqual(running["checks"][-1]["status"], "running")
        process.send_signal(signal.SIGINT)
        out, err = process.communicate(timeout=10)
        self.assertEqual(process.returncode, 130, out + err)
        report = self.report()
        self.assertEqual(report["status"], "interrupted")
        row = report["checks"][-1]
        self.assertEqual(row["status"], "interrupted")
        self.assertEqual(row["pytest"]["counts"]["errors"], 1)
        self.assertEqual(
            row["pytest"]["cases"][0]["detail"], "partial setup\nretained detail"
        )
        self.assertTrue(row["process_group_stopped"])
        self.assert_tree_stopped()

    def test_unexpected_cleanup_error_is_recorded_without_skipping_kill_and_reap(self):
        self.output.mkdir()
        self.behavior.write_text(json.dumps({"block": "schema"}))
        (self.output / "feedback.json").write_text('{"checks": []}')
        real_wait = subprocess.Popen.wait
        failed = False

        def broken_cleanup_wait(process, timeout=None):
            nonlocal failed
            if timeout == 0.5 and not failed:
                failed = True
                raise RuntimeError("cleanup wait failed unexpectedly")
            return real_wait(process, timeout=timeout)

        with mock.patch.object(subprocess.Popen, "wait", new=broken_cleanup_wait):
            row = feedback.run_check(
                "schema",
                [str(self.ptl), "profile", "validate-schema", str(self.profile)],
                self.output,
                1.5,
                os.environ.copy(),
            )
        self.assertEqual(row["status"], "failed")
        self.assertEqual(row["returncode"], -signal.SIGKILL)
        self.assertTrue(row["process_group_stopped"])
        self.assertIn("cleanup wait failed unexpectedly", row["cleanup_errors"][0])
        self.assertIn(
            "cleanup wait failed unexpectedly", (self.output / "schema.log").read_text()
        )
        self.assert_tree_stopped()

    def test_unexpected_wait_error_reaps_tree_and_preserves_stage_evidence(self):
        self.output.mkdir()
        self.behavior.write_text(json.dumps({"block": "schema"}))
        (self.output / "feedback.json").write_text('{"checks": []}')
        real_wait = subprocess.Popen.wait
        failed = False

        def broken_wait(process, *args, **kwargs):
            nonlocal failed
            if not failed:
                failed = True
                self.await_tree()
                raise RuntimeError("unexpected wait failure")
            return real_wait(process, *args, **kwargs)

        with mock.patch.object(subprocess.Popen, "wait", new=broken_wait):
            row = feedback.run_check(
                "schema",
                [str(self.ptl), "profile", "validate-schema", str(self.profile)],
                self.output,
                3,
                os.environ.copy(),
            )
        self.assertEqual(row["status"], "failed")
        self.assertTrue(row["runner_error"])
        self.assertTrue(row["process_group_stopped"])
        self.assertEqual(row["returncode"], -signal.SIGKILL)
        self.assertIn(
            "unexpected wait failure", (self.output / "schema.log").read_text()
        )
        self.assert_tree_stopped()

    def test_suite_stops_after_first_failure_and_preserves_junit_case_evidence(self):
        (self.root / "hardware_suite.py").write_text(
            textwrap.dedent("""
            import os
            from pathlib import Path

            def test_reviewed_connection_and_failure():
                assert os.environ["PYTESTLAB_SIMULATE"] == "0"
                assert os.environ["APPROVED_TEST_URL"] == "http://approved.invalid:8000"
                assert False, "first setup failed before later actions"

            def test_later_action_must_not_run():
                Path(__file__).with_name("unsafe-later-action").touch()
        """)
        )
        env = dict(os.environ, PYTEST_ADDOPTS="-k do_not_select_anything --maxfail=5")
        process = self.spawn(
            "--mode",
            "suite",
            "--hardware-approved",
            "--writes-approved",
            "--lamb-url",
            "http://approved.invalid:8000",
            env=env,
        )
        out, err = process.communicate(timeout=15)
        self.assertEqual(process.returncode, 1, out + err)
        report = self.report()
        self.assertEqual(report["status"], "failed")
        self.assertFalse((self.root / "unsafe-later-action").exists())
        row = report["checks"][-1]
        self.assertEqual(row["pytest"]["counts"]["failed"], 1)
        self.assertEqual(len(row["pytest"]["cases"]), 1)
        self.assertIn(
            "first setup failed before later actions",
            row["pytest"]["cases"][0]["detail"],
        )
        self.assertEqual(
            row["pytest"]["cases"][0]["name"], "test_reviewed_connection_and_failure"
        )


if __name__ == "__main__":
    unittest.main()
