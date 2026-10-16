import importlib.util
import io
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


server = load("preview_server", ROOT / "server.py")
upgrade = load("preview_upgrade", ROOT / "scripts/upgrade-preview.py")


class PreviewServerTests(unittest.TestCase):
    def test_old_and_new_ffmpeg_timeout_options(self):
        cases = [
            ("-timeout <int> wait for incoming connections\n-stimeout <int64> socket TCP I/O operations", ["-stimeout", "8000000"]),
            ("-timeout <int64> set timeout of socket I/O operations", ["-timeout", "8000000"]),
            ("-timeout <int> wait for incoming connections", []),
        ]
        for help_text, expected in cases:
            with self.subTest(help_text=help_text):
                server.preview_rtsp_timeout_args.cache_clear()
                with patch.object(server.subprocess, "run", return_value=Mock(stdout=help_text, stderr="")):
                    self.assertEqual(server.preview_rtsp_timeout_args(), expected)
        server.preview_rtsp_timeout_args.cache_clear()

    def test_missing_ffmpeg_help_keeps_watchdog_fallback(self):
        server.preview_rtsp_timeout_args.cache_clear()
        with patch.object(server.subprocess, "run", side_effect=OSError("not found")):
            self.assertEqual(server.preview_rtsp_timeout_args(), [])
        server.preview_rtsp_timeout_args.cache_clear()

    def test_stalled_decoder_is_killed_without_touching_recorder(self):
        process = Mock(poll=Mock(return_value=None))
        stop = Mock(wait=Mock(return_value=False))
        with patch.object(server.time, "monotonic", return_value=21), patch.object(server, "stop_recorder") as recording:
            server.watch_preview_process(process, stop, [0])
            process.kill.assert_called_once()
            recording.assert_not_called()

    def test_decoder_with_recent_frames_is_not_killed(self):
        process = Mock(poll=Mock(return_value=None))
        stop = Mock(wait=Mock(side_effect=[False, True]))
        with patch.object(server.time, "monotonic", return_value=19):
            server.watch_preview_process(process, stop, [0])
        process.kill.assert_not_called()

    def test_stream_configures_timeout_emits_jpeg_and_closes_pipe(self):
        payload = b"\xff\xd8valid-test-jpeg\xff\xd9"
        process = Mock(stdout=io.BytesIO(payload), poll=Mock(return_value=0))
        handler = Mock(wfile=io.BytesIO())
        with patch.object(server.subprocess, "Popen", return_value=process) as launch, patch.object(server, "preview_rtsp_timeout_args", return_value=["-stimeout", "8000000"]):
            server.stream_live_mjpeg(handler)
        args = launch.call_args.args[0]
        self.assertEqual(args[args.index("-stimeout") + 1], "8000000")
        self.assertIn(payload, handler.wfile.getvalue())
        self.assertTrue(process.stdout.closed)
        handler.connection.settimeout.assert_called_once_with(10)

    def test_only_known_mjpeg_default_edit_is_migrated(self):
        original = 'const mode = get("live") || "webrtc";\n\n'
        current = original.replace('|| "webrtc"', '|| "mjpeg"')
        self.assertTrue(upgrade.local_mode_edit(original, current))
        self.assertTrue(upgrade.local_mode_edit(original, current.replace("\n", "\r\n")))
        self.assertFalse(upgrade.local_mode_edit(original, current + "// unrelated edit\n"))


if __name__ == "__main__":
    unittest.main()
