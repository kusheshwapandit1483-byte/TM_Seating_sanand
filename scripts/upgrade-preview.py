#!/usr/bin/env python3
"""Switch to the recovery release, migrating only the known local MJPEG one-line edit."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

BRANCH = "fix/occupancy-preview-recovery"


def git(*args):
    return subprocess.check_output(["git", *args], text=True).strip()


def local_mode_edit(original, current):
    original = original.replace("\r\n", "\n")
    current = current.replace("\r\n", "\n")
    needle = 'get("live") || "webrtc"'
    return needle in original and current == original.replace(needle, 'get("live") || "mjpeg"')


def main(mode):
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        raise RuntimeError("Run this upgrade as your normal administrator account, without sudo; it requests sudo only when applying services")
    root = Path(git("rev-parse", "--show-toplevel")).resolve()
    if not (root / "server.py").is_file() or not (root / "app.js").is_file():
        raise RuntimeError("Run from the installed TM_Seating_sanand checkout")
    os.chdir(root)
    subprocess.run(["git", "fetch", "origin", BRANCH], check=True)
    target = git("rev-parse", "FETCH_HEAD")
    changed = git("diff", "--name-only", "HEAD").splitlines()
    migrated = False
    if changed:
        original = subprocess.check_output(["git", "show", "HEAD:app.js"], text=True)
        if changed != ["app.js"] or not local_mode_edit(original, (root / "app.js").read_text(encoding="utf-8")):
            raise RuntimeError("Other local edits are present. They have not been discarded; preserve them before upgrading.")
        migrated = True
    if mode == "auto":
        mode = "mjpeg" if migrated else "webrtc"
        settings = root / "preview-settings.json"
        if settings.is_file():
            saved = json.loads(settings.read_text()).get("mode")
            if saved in ("webrtc", "mjpeg"):
                mode = saved
    if migrated:
        subprocess.run(["git", "stash", "push", "-m", "Preserve local MJPEG default before preview recovery", "--", "app.js"], check=True)
        print("The previous one-line MJPEG edit is preserved in git stash and migrated to site settings.", flush=True)
    exists = subprocess.run(["git", "show-ref", "--verify", "--quiet", f"refs/heads/{BRANCH}"]).returncode == 0
    subprocess.run(["git", "switch", BRANCH] if exists else ["git", "switch", "--no-track", "-c", BRANCH, target], check=True)
    subprocess.run(["git", "merge", "--ff-only", target], check=True)
    subprocess.run(["sudo", "python3", str(root / "scripts/apply-preview-recovery.py"), "--mode", mode], check=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("auto", "webrtc", "mjpeg"), default="auto")
    args = parser.parse_args()
    try:
        main(args.mode)
    except Exception as exc:
        print(f"Upgrade stopped: {exc}", file=sys.stderr)
        sys.exit(1)
