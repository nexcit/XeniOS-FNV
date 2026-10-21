#!/usr/bin/env python3
"""
phone.py - iPhone helper for the XeniOS dev loop (Windows/macOS/Linux, USB).

Talks to the iPhone through pymobiledevice3 over the USB cable. Everything it
fetches lands in <repo>/.phone/ (git-ignored) so Claude Code can read it.

Commands
  check                 Is the phone visible? Is XeniOS installed? Saves the app's bundle ID.
  find                  Re-detect the installed XeniOS app (run after installing a new build).
  watch [--all]         Stream the phone's system log to .phone/syslog-live.log (Ctrl+C to stop).
                        Default: only lines from the XeniOS process. --all: everything.
  tail-log [-i SECS]    Re-download xenia.log every SECS seconds (default 5) to
                        .phone/device/xenia.log, so the emulator log is near-live on the PC.
  pull                  Download xenia.log, xenios.config.toml and config/ (per-game configs)
                        into .phone/device/.
  push-config FILE [REMOTE]
                        Upload a config file to the app's Documents folder. REMOTE defaults to
                        the same relative path the file has under .phone/device/, or
                        xenios.config.toml. The current phone copy is backed up first.
  crashes               Download XeniOS crash reports into .phone/crashes/.
  mark TEXT             Write a marker line into the live syslog file ("=== MARK ... ===")
                        so test runs can be told apart.

Requires: Python 3.9+, `pip install pymobiledevice3`, and on Windows iTunes (for the
Apple Mobile Device Service). The phone must be unlocked and have trusted this PC.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PHONE = ROOT / ".phone"
DEVICE = PHONE / "device"
APP_FILE = PHONE / "app.json"
LIVE_LOG = PHONE / "syslog-live.log"
PROCESS_NAME = "XeniOS"  # CFBundleExecutable of the iOS app (src/xenia/app/CMakeLists.txt)


def pmd_cmd(*args: str) -> list[str]:
    return [sys.executable, "-m", "pymobiledevice3", *args]


def child_env() -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    env.setdefault("NO_COLOR", "1")
    return env


def run_pmd(*args: str, capture: bool = True) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        pmd_cmd(*args),
        capture_output=capture,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=child_env(),
    )
    if proc.returncode != 0:
        msg = (proc.stderr or proc.stdout or "").strip() if capture else ""
        raise SystemExit(f"pymobiledevice3 {' '.join(args)} failed (exit {proc.returncode})\n{msg}")
    return proc


def stamp() -> str:
    return _dt.datetime.now().strftime("%Y%m%d-%H%M%S")


def ensure_dirs() -> None:
    PHONE.mkdir(exist_ok=True)
    DEVICE.mkdir(exist_ok=True)
    gi = PHONE / ".gitignore"
    if not gi.exists():
        gi.write_text("*\n", encoding="utf-8")


# ---------------------------------------------------------------- app lookup

def find_app(verbose: bool = True) -> dict:
    out = run_pmd("apps", "list", "--type", "User").stdout
    try:
        apps = json.loads(out)
    except json.JSONDecodeError as e:
        raise SystemExit(f"Could not parse app list from the phone: {e}")
    hits = []
    for bundle_id, info in apps.items():
        names = {str(info.get(k, "")) for k in ("CFBundleExecutable", "CFBundleName", "CFBundleDisplayName")}
        if any("xenios" in n.lower() for n in names):
            hits.append(
                {
                    "bundle_id": bundle_id,
                    "name": info.get("CFBundleDisplayName") or info.get("CFBundleName"),
                    "version": info.get("CFBundleShortVersionString"),
                    "build": info.get("CFBundleVersion"),
                    "executable": info.get("CFBundleExecutable"),
                }
            )
    if not hits:
        raise SystemExit("No XeniOS app found on the phone. Install it with SideStore first.")
    # Prefer a dev build if both official and dev are installed.
    hits.sort(key=lambda h: (".dev" not in h["bundle_id"], h["bundle_id"]))
    chosen = hits[0]
    ensure_dirs()
    APP_FILE.write_text(json.dumps({"chosen": chosen, "all": hits}, indent=2), encoding="utf-8")
    if verbose:
        for h in hits:
            mark = "->" if h is chosen else "  "
            print(f"{mark} {h['bundle_id']}  {h['name']} {h['version']} ({h['build']})")
        if len(hits) > 1:
            print(f"Using {chosen['bundle_id']}. Edit .phone/app.json 'chosen' to switch.")
    return chosen


def app() -> dict:
    if APP_FILE.exists():
        return json.loads(APP_FILE.read_text(encoding="utf-8"))["chosen"]
    return find_app(verbose=True)


# ---------------------------------------------------------------- commands

def cmd_check(_args) -> None:
    devices = json.loads(run_pmd("usbmux", "list").stdout or "[]")
    if not devices:
        raise SystemExit(
            "No iPhone visible. Plug it in by USB, unlock it, tap 'Trust' on the phone, "
            "and make sure iTunes (Apple Mobile Device Service) is installed."
        )
    for d in devices:
        print(f"Phone: {d.get('DeviceName')}  iOS {d.get('ProductVersion')}  ({d.get('ConnectionType')})")
    find_app(verbose=True)


def cmd_find(_args) -> None:
    find_app(verbose=True)


def cmd_watch(args) -> None:
    ensure_dirs()
    if LIVE_LOG.exists() and LIVE_LOG.stat().st_size > 0:
        LIVE_LOG.rename(PHONE / f"syslog-{stamp()}.log")
    cmd = pmd_cmd("syslog", "live")
    if not args.all:
        cmd += ["--process-name", PROCESS_NAME]
    print(f"Writing phone log to {LIVE_LOG}  (Ctrl+C to stop)")
    with open(LIVE_LOG, "a", encoding="utf-8", buffering=1) as f:
        f.write(f"=== watch started {stamp()} filter={'none' if args.all else PROCESS_NAME} ===\n")
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", env=child_env(),
        )
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                f.write(line)
                sys.stdout.write(line)
        except KeyboardInterrupt:
            pass
        finally:
            proc.terminate()
            f.write(f"=== watch stopped {stamp()} ===\n")


def pull_one(bundle_id: str, remote: str, local: Path, quiet: bool = False) -> bool:
    try:
        run_pmd("apps", "pull", "--documents", bundle_id, remote, str(local))
        if not quiet:
            print(f"pulled {remote} -> {local.relative_to(ROOT)}")
        return True
    except SystemExit as e:
        if not quiet:
            print(f"skip {remote}: {str(e).splitlines()[-1] if str(e) else 'not found'}")
        return False


def cmd_pull(_args) -> None:
    ensure_dirs()
    bid = app()["bundle_id"]
    pull_one(bid, "/Documents/xenia.log", DEVICE / "xenia.log")
    pull_one(bid, "/Documents/xenios.config.toml", DEVICE / "xenios.config.toml")
    cfg = DEVICE / "config"
    if cfg.exists():
        shutil.rmtree(cfg)
    pull_one(bid, "/Documents/config", DEVICE)  # directory pull lands in DEVICE/config


def cmd_tail_log(args) -> None:
    ensure_dirs()
    bid = app()["bundle_id"]
    target = DEVICE / "xenia.log"
    tmp = DEVICE / "xenia.log.part"
    print(f"Refreshing {target.relative_to(ROOT)} every {args.interval}s (Ctrl+C to stop)")
    last_size = -1
    try:
        while True:
            if pull_one(bid, "/Documents/xenia.log", tmp, quiet=True):
                tmp.replace(target)
                size = target.stat().st_size
                if size != last_size:
                    print(f"{_dt.datetime.now():%H:%M:%S} xenia.log {size/1024:.0f} KB")
                    last_size = size
            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass


def cmd_push_config(args) -> None:
    ensure_dirs()
    local = Path(args.file).resolve()
    if not local.is_file():
        raise SystemExit(f"Not a file: {local}")
    if local.suffix != ".toml":
        raise SystemExit("Only .toml config files can be pushed with this command.")
    if args.remote:
        remote = args.remote if args.remote.startswith("/Documents/") else "/Documents/" + args.remote.lstrip("/")
    else:
        try:
            rel = local.relative_to(DEVICE).as_posix()
        except ValueError:
            rel = "xenios.config.toml"
        remote = "/Documents/" + rel
    bid = app()["bundle_id"]
    backup = PHONE / "backups" / stamp()
    backup.mkdir(parents=True, exist_ok=True)
    pull_one(bid, remote, backup / Path(remote).name, quiet=True)
    run_pmd("apps", "push", "--documents", bid, str(local), remote)
    print(f"pushed {local.name} -> {remote}  (old copy in {backup.relative_to(ROOT)})")
    print("Restart the game in XeniOS for the change to take effect.")


def cmd_crashes(_args) -> None:
    ensure_dirs()
    out = PHONE / "crashes"
    out.mkdir(exist_ok=True)
    run_pmd("crash", "pull", str(out), "--match", PROCESS_NAME, capture=False)
    files = sorted(out.rglob("*"), key=lambda p: p.stat().st_mtime, reverse=True)
    files = [p for p in files if p.is_file()]
    print(f"{len(files)} crash report(s) in {out.relative_to(ROOT)}")
    for p in files[:5]:
        print(f"  {p.relative_to(ROOT)}  {_dt.datetime.fromtimestamp(p.stat().st_mtime):%Y-%m-%d %H:%M}")


def cmd_mark(args) -> None:
    ensure_dirs()
    text = " ".join(args.text)
    with open(LIVE_LOG, "a", encoding="utf-8") as f:
        f.write(f"=== MARK {stamp()} {text} ===\n")
    print(f"marked: {text}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check").set_defaults(fn=cmd_check)
    sub.add_parser("find").set_defaults(fn=cmd_find)
    w = sub.add_parser("watch")
    w.add_argument("--all", action="store_true", help="don't filter to the XeniOS process")
    w.set_defaults(fn=cmd_watch)
    t = sub.add_parser("tail-log")
    t.add_argument("-i", "--interval", type=float, default=5.0)
    t.set_defaults(fn=cmd_tail_log)
    sub.add_parser("pull").set_defaults(fn=cmd_pull)
    pc = sub.add_parser("push-config")
    pc.add_argument("file")
    pc.add_argument("remote", nargs="?")
    pc.set_defaults(fn=cmd_push_config)
    sub.add_parser("crashes").set_defaults(fn=cmd_crashes)
    m = sub.add_parser("mark")
    m.add_argument("text", nargs="+")
    m.set_defaults(fn=cmd_mark)
    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
