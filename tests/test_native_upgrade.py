import importlib.util
from pathlib import Path
import tempfile
import shutil
from contextlib import ExitStack
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.request import build_opener, ProxyHandler

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("native_upgrade", ROOT / "scripts/upgrade-native-preview.py")
upgrade = importlib.util.module_from_spec(spec)
spec.loader.exec_module(upgrade)
spec = importlib.util.spec_from_file_location("native_apply", ROOT / "scripts/apply-native-preview.py")
apply = importlib.util.module_from_spec(spec)
spec.loader.exec_module(apply)


class NativeUpgradeTests(unittest.TestCase):
    def test_confirmed_rollback_is_preserved_but_other_changes_are_rejected(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "tests") as folder:
            root = Path(folder)
            (root / "app.js").write_text("original\n\n")
            with patch.object(upgrade.subprocess, "check_output", return_value="original\n\n"):
                self.assertTrue(upgrade.known_rollback(root, ["app.js"]))
                self.assertFalse(upgrade.known_rollback(root, ["server.py"]))
                (root / "app.js").write_text("original\n// custom code\n")
                self.assertFalse(upgrade.known_rollback(root, ["app.js"]))

    def install_fixture(self, ready, kind="python", url="http://127.0.0.1:8080"):
        stack = ExitStack()
        self.addCleanup(stack.close)
        root = Path(stack.enter_context(tempfile.TemporaryDirectory(dir=ROOT / "tests")))
        shutil.copytree(ROOT / "native-player-observer", root / "native-player-observer")
        (root / "scripts").mkdir()
        shutil.copyfile(ROOT / "scripts/native-kiosk.py", root / "scripts/native-kiosk.py")
        library = root / "installed"
        library.mkdir()
        runner = library / "kiosk.py"
        runner.write_text("original launcher")
        launcher = library / "tm-camera-kiosk-start"
        launcher.write_text('#!/bin/sh\nexec /usr/bin/python3 /usr/local/lib/tm-camera/kiosk.py\n' if kind == "python"
                            else '#!/bin/bash\nexec "${CHROME}" --kiosk "${URL}" --autoplay-policy=no-user-gesture-required\n')
        dropin = root / "systemd" / "96-native-player.conf"
        dropin.parent.mkdir()
        dropin.write_text("previous configuration")
        for name, value in {"ROOT": root, "EXTENSION": library / "observer", "WRAPPER": library / "native-kiosk.py",
                            "RUNNER": runner, "LAUNCHER": launcher, "DROPIN": dropin, "BACKUP_ROOT": root / "backups"}.items():
            stack.enter_context(patch.object(apply, name, value))
        stack.enter_context(patch.object(apply.os, "geteuid", return_value=0, create=True))
        stack.enter_context(patch.object(apply.subprocess, "check_output", return_value=str(root)))
        stack.enter_context(patch.object(apply, "show", side_effect=lambda p: {"LoadState": "loaded", "Environment": f'KIOSK_URL={url}', "ExecStart": str(launcher)}[p]))
        log = "TM_NATIVE_OBSERVER_READY\nTM_NATIVE_VIDEO_PROGRESS" if ready else "observer absent"
        run = stack.enter_context(patch.object(apply.subprocess, "run", return_value=SimpleNamespace(stdout=log, returncode=0)))
        receiver = Mock(parameters=[("tm_verify_port", "12345"), ("tm_verify_nonce", "test-nonce-012345678901234")],
                        snapshot=Mock(return_value=["dashboard-ready", "parent-ready", "parent-frames"] if ready else []),
                        verified=Mock(return_value=ready))
        manager = Mock()
        manager.__enter__ = Mock(return_value=receiver)
        manager.__exit__ = Mock(return_value=False)
        stack.enter_context(patch.object(apply, "Verification", return_value=manager))
        stack.enter_context(patch.object(apply.time, "sleep"))
        return root, runner, dropin, run

    def test_install_requires_decoded_frames_and_only_restarts_kiosk(self):
        root, runner, dropin, run = self.install_fixture(True)
        apply.main()
        self.assertEqual(runner.read_text(), "original launcher")
        self.assertIn("native-kiosk.py", dropin.read_text())
        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual([cmd for cmd in commands if "restart" in cmd], [["systemctl", "restart", "tm-camera-kiosk.service"]])
        self.assertTrue((root / "installed/observer/manifest.json").is_file())

    def test_unverified_observer_restores_previous_kiosk_files(self):
        root, runner, dropin, run = self.install_fixture(False)
        with self.assertRaisesRegex(RuntimeError, "Verification failed"):
            apply.main()
        self.assertEqual(dropin.read_text(), "previous configuration")
        self.assertEqual(runner.read_text(), "original launcher")
        self.assertFalse((root / "installed/native-kiosk.py").exists())
        self.assertFalse((root / "installed/observer/manifest.json").exists())
        self.assertTrue(all("tm-camera-monitor.service" not in call.args[0] and "mediamtx.service" not in call.args[0] for call in run.call_args_list))

    def test_shell_launcher_and_separate_user_are_retained(self):
        root, runner, dropin, run = self.install_fixture(True, kind="shell", url="http://127.0.0.1:8889/pramacam")
        apply.main()
        self.assertIn("--load-extension=", apply.LAUNCHER.read_text())
        self.assertNotIn("ExecStart=", dropin.read_text())
        self.assertNotIn("User=", dropin.read_text())
        self.assertIn("127.0.0.1:8080", dropin.read_text())
        self.assertNotIn("tm_verify_nonce", dropin.read_text())
        self.assertEqual(runner.read_text(), "original launcher")

    def test_failed_verification_restores_shell_launcher(self):
        root, runner, dropin, run = self.install_fixture(False, kind="shell")
        original = apply.LAUNCHER.read_bytes()
        with self.assertRaisesRegex(RuntimeError, "Verification failed"):
            apply.main()
        self.assertEqual(apply.LAUNCHER.read_bytes(), original)

    def test_unrecognised_launchers_and_other_extensions_are_not_replaced(self):
        for text in ('#!/bin/bash\nexec firefox --kiosk "$URL"\n',
                     '#!/bin/bash\nexec "${CHROME}" --load-extension=/custom --autoplay-policy=no-user-gesture-required\n'):
            with self.assertRaises(RuntimeError):
                apply.launcher_plan(text, False)

    def test_existing_shell_observer_patch_is_idempotent(self):
        text = '#!/bin/bash\nexec "${CHROME}" --autoplay-policy=no-user-gesture-required\n'
        kind, patched = apply.launcher_plan(text, False)
        self.assertEqual(kind, "shell")
        self.assertEqual(apply.launcher_plan(patched, False), ("shell", patched))

    def test_correct_repository_remote_is_selected_without_changing_origin(self):
        def output(*args):
            return {("remote",): "origin\ntm-updates", ("remote", "get-url", "origin"): "https://github.com/kusheshwapandit1483-byte/TM_cell5.git",
                    ("remote", "get-url", "tm-updates"): upgrade.REPOSITORY}[args]
        with patch.object(upgrade, "git", side_effect=output):
            self.assertEqual(upgrade.update_remote(), "tm-updates")
        with patch.object(upgrade, "git", return_value=""):
            self.assertEqual(upgrade.update_remote(), upgrade.REPOSITORY)

    def test_direct_receiver_requires_current_nonce_and_actual_parent_frame_report(self):
        opener = build_opener(ProxyHandler({}))
        with apply.Verification() as receiver:
            base = f"http://127.0.0.1:{receiver.server.server_port}/verify/"
            with self.assertRaises(HTTPError) as error:
                opener.open(base + "wrong-nonce/parent-frames", timeout=2)
            error.exception.close()
            self.assertEqual(receiver.snapshot(), [])
            for stage in ("dashboard-ready", "observer-ready", "observer-frames", "parent-ready"):
                with opener.open(base + receiver.nonce + "/" + stage, timeout=2) as response:
                    self.assertEqual(response.status, 204)
            self.assertFalse(receiver.verified())
            with opener.open(base + receiver.nonce + "/parent-frames", timeout=2):
                pass
            self.assertTrue(receiver.verified())


if __name__ == "__main__":
    unittest.main()
