#!/usr/bin/env python3
"""Update the earlier occupancy/35 GB checkout without changing repository remotes."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

BRANCH = "fix/occupancy-native-player-recovery"
BASELINE = "8dc45bf37125e53cd0340aee185b9b8cbb222dd5"
REPOSITORY = "https://github.com/kusheshwapandit1483-byte/TM_Seating_sanand.git"


def git(*args):
    return subprocess.check_output(["git", *args], text=True).strip()


def update_remote(explicit=None):
    if explicit:
        return explicit
    for remote in git("remote").splitlines():
        url = git("remote", "get-url", remote).lower().rstrip("/").removesuffix(".git")
        if url.endswith("kusheshwapandit1483-byte/tm_seating_sanand"):
            return remote
    return REPOSITORY


def known_rollback(root, changed):
    if not set(changed).issubset({"app.js", "index.html"}):
        return False
    for name in changed:
        expected = subprocess.check_output(["git", "show", f"{BASELINE}:{name}"], text=True)
        if (root / name).read_text(encoding="utf-8").replace("\r\n", "\n") != expected.replace("\r\n", "\n"):
            return False
    return True


def main(remote=None):
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        raise RuntimeError("Run as the administrator account without sudo; service application requests sudo itself")
    root = Path(git("rev-parse", "--show-toplevel")).resolve()
    installed = subprocess.check_output(["systemctl", "show", "tm-camera-monitor.service", "-p", "WorkingDirectory", "--value"], text=True).strip()
    if not installed or Path(installed).resolve() != root:
        raise RuntimeError(f"The active service uses {installed or 'an unknown folder'}. Run there; no source or services changed")
    if subprocess.run(["git", "merge-base", "--is-ancestor", BASELINE, "HEAD"]).returncode != 0:
        raise RuntimeError("This is not the earlier 35 GB checkout; use the separate raspberrypibase recovery for that version")
    settings = root / "preview-settings.json"
    if settings.is_file() and json.loads(settings.read_text()).get("mode", "webrtc") != "webrtc":
        raise RuntimeError("Existing MJPEG/HLS mode retained; this update is for the native WebRTC player")
    changed = git("diff", "--name-only", "HEAD").splitlines()
    if changed and not known_rollback(root, changed):
        raise RuntimeError("Other local changes found; no edits have been discarded")
    source = update_remote(remote)
    subprocess.run(["git", "fetch", source, BRANCH], check=True)
    target = git("rev-parse", "FETCH_HEAD")
    if changed:
        subprocess.run(["git", "stash", "push", "-m", "Preserve original 35 GB player rollback", "--", *changed], check=True)
        print("Your original player rollback is preserved in git stash.", flush=True)
    exists = subprocess.run(["git", "show-ref", "--verify", "--quiet", f"refs/heads/{BRANCH}"]).returncode == 0
    subprocess.run(["git", "switch", BRANCH] if exists else ["git", "switch", "--no-track", "-c", BRANCH, target], check=True)
    subprocess.run(["git", "merge", "--ff-only", target], check=True)
    subprocess.run(["sudo", "python3", str(root / "scripts/apply-native-preview.py")], check=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--remote", help="Override the automatically selected repository remote")
    args = parser.parse_args()
    try:
        main(args.remote)
    except Exception as exc:
        print(f"Upgrade stopped: {exc}", file=sys.stderr)
        sys.exit(1)
