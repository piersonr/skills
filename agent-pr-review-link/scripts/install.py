#!/usr/bin/env python3
"""Install agent-pr-review-link from this repository onto PATH, repeatably.

    python3 agent-pr-review-link/scripts/install.py install   [--prefix DIR] [--allow-dirty] [--force]
    python3 agent-pr-review-link/scripts/install.py check     [--prefix DIR]
    python3 agent-pr-review-link/scripts/install.py rollback  [--prefix DIR] [--force]

The prefix defaults to $AGENT_PR_REVIEW_LINK_PREFIX, else ~/.local.

This repository is the source of truth. The installed copy is an artifact:
<prefix>/bin/agent-pr-review-link, with its tests and an INSTALL.json manifest
in <prefix>/share/agent-pr-review-link/. Never edit the installed copy; change
the source here, commit, and install again.

install refuses a source with uncommitted changes (unless --allow-dirty, which
is recorded), and refuses to overwrite an installed helper that no longer matches
its manifest -- an in-place edit -- or an unrecorded one that differs from the
source, unless --force. Whatever it replaces moves to previous/, and rollback
swaps previous/ with the current install, so a rollback can itself be undone.
Copies, never symlinks: a branch switch in this checkout cannot change what is
installed.

Exit status: 0 success or up to date; 1 check found a difference; 2 refused.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

NAME = "agent-pr-review-link"
TEST = "test_agent_pr_review_link.py"
MANIFEST = "INSTALL.json"
SOURCE_DIR = Path(__file__).resolve().parent
MANIFEST_VERSION = 1
# States that mean the installed helper holds bytes nothing recorded; replacing them needs --force.
UNRECORDED = ("edited_in_place", "unmanaged_differs")


class Refused(Exception):
    pass


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest() if Path(path).is_file() else None


def now():
    return datetime.now(timezone.utc).isoformat()


def git(*args):
    done = subprocess.run(["git", "-C", str(SOURCE_DIR), *args], stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL, text=True)
    return done.stdout.strip() if done.returncode == 0 else None


def source_identity():
    commit = git("rev-parse", "HEAD")
    if not commit:
        raise Refused("source is not inside a Git checkout; install from a clone of the repository")
    dirty = bool(git("status", "--porcelain", "--untracked-files=all", "--", "."))
    return {"source_commit": commit, "source_dirty": dirty,
            "source_remote": git("remote", "get-url", "origin"),
            "script_sha256": sha(SOURCE_DIR / NAME), "test_sha256": sha(SOURCE_DIR / TEST)}


def layout(prefix):
    prefix = Path(prefix).expanduser().resolve()
    share = prefix / "share" / NAME
    return {"bin": prefix / "bin" / NAME, "share": share, "test": share / TEST,
            "manifest": share / MANIFEST, "previous": share / "previous"}


def read_manifest(path):
    try:
        data = json.loads(Path(path).read_text())
    except FileNotFoundError:
        return None
    except ValueError:
        raise Refused("install manifest is unreadable: %s" % path)
    return data if isinstance(data, dict) and data.get("version") == MANIFEST_VERSION else None


def installed_state(paths, source=None):
    """Describe the installed helper against its manifest and, when given, this source."""
    current = sha(paths["bin"])
    manifest = read_manifest(paths["manifest"])
    if current is None:
        return "not_installed", manifest
    recorded = manifest and not manifest.get("unmanaged")
    if recorded and current != manifest.get("script_sha256"):
        # An install interrupted after the helper was copied, before its manifest was written,
        # leaves exactly the source's bytes; that is not a hand edit.
        if source and current == source["script_sha256"]:
            return "interrupted_install", manifest
        return "edited_in_place", manifest
    if not recorded:
        if source is None:
            return "unmanaged", manifest
        return ("unmanaged_match" if current == source["script_sha256"] else "unmanaged_differs"), manifest
    if sha(paths["test"]) != manifest.get("test_sha256"):
        return "tests_differ", manifest
    if source is None or (current == source["script_sha256"]
                          and manifest.get("test_sha256") == source["test_sha256"]):
        return "current", manifest
    return "behind_source", manifest


def write_bytes(dest, data, mode):
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".%s." % dest.name, dir=dest.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.chmod(temp, mode)
        os.replace(temp, dest)
    except BaseException:
        if os.path.exists(temp):
            os.unlink(temp)
        raise


def write_json(dest, data):
    write_bytes(dest, (json.dumps(data, indent=2, sort_keys=True) + "\n").encode(), 0o644)


def snapshot_install(paths):
    """The installed set as bytes, with a manifest that honestly describes it."""
    manifest = read_manifest(paths["manifest"])
    helper = paths["bin"].read_bytes() if paths["bin"].is_file() else None
    if helper is None:
        return None
    if not manifest or manifest.get("script_sha256") != hashlib.sha256(helper).hexdigest():
        manifest = {"version": MANIFEST_VERSION, "unmanaged": True, "source_commit": None,
                    "script_sha256": hashlib.sha256(helper).hexdigest(), "test_sha256": sha(paths["test"]),
                    "recorded_at": now()}
    return {"helper": helper, "test": paths["test"].read_bytes() if paths["test"].is_file() else None,
            "manifest": manifest}


def store_previous(paths, saved):
    """Replace previous/ with one complete set, never a mix of two installs."""
    previous = paths["previous"]
    if previous.exists():
        shutil.rmtree(previous)
    previous.mkdir(parents=True)
    write_bytes(previous / NAME, saved["helper"], 0o755)
    if saved["test"] is not None:
        write_bytes(previous / TEST, saved["test"], 0o644)
    write_json(previous / MANIFEST, saved["manifest"])


def cmd_install(args):
    paths = layout(args.prefix)
    source = source_identity()
    if source["source_dirty"] and not args.allow_dirty:
        raise Refused("source has uncommitted changes; commit them or pass --allow-dirty")
    state, manifest = installed_state(paths, source)
    if state in UNRECORDED and not args.force:
        raise Refused("installed copy at %s does not match any recorded install (%s); recover its "
                      "changes into this repository first, or pass --force to replace it"
                      % (paths["bin"], state))
    if state == "current" and manifest.get("source_commit") == source["source_commit"]:
        print(json.dumps({"status": "up_to_date", "bin": str(paths["bin"]), **source}))
        return 0
    saved = snapshot_install(paths)
    if saved and (saved["helper"] != (SOURCE_DIR / NAME).read_bytes()
                  or saved["test"] != (SOURCE_DIR / TEST).read_bytes()):
        store_previous(paths, saved)
    # Helper, then tests, then the manifest last: a crash before the manifest is detected
    # next time as interrupted_install and repaired without --force.
    write_bytes(paths["bin"], (SOURCE_DIR / NAME).read_bytes(), 0o755)
    write_bytes(paths["test"], (SOURCE_DIR / TEST).read_bytes(), 0o644)
    record = {"version": MANIFEST_VERSION, **source, "installed_at": now(), "bin": str(paths["bin"]),
              "replaced_state": state}
    write_json(paths["manifest"], record)
    print(json.dumps({"status": "installed", **record}))
    return 0


def cmd_check(args):
    paths = layout(args.prefix)
    source = source_identity()
    state, manifest = installed_state(paths, source)
    print(json.dumps({"status": state, "bin": str(paths["bin"]),
                      "installed_script_sha256": sha(paths["bin"]),
                      "installed_test_sha256": sha(paths["test"]),
                      "manifest_source_commit": (manifest or {}).get("source_commit"),
                      "manifest_source_dirty": (manifest or {}).get("source_dirty"),
                      "source_commit": source["source_commit"], "source_dirty": source["source_dirty"],
                      "source_script_sha256": source["script_sha256"]}, indent=2))
    return 0 if state == "current" else 1


def cmd_rollback(args):
    """Swap previous/ with the current install, so running rollback again undoes it."""
    paths = layout(args.prefix)
    previous = paths["previous"]
    if not (previous / NAME).is_file():
        raise Refused("no previous install is recorded under %s" % previous)
    state, _ = installed_state(paths)
    if state in ("edited_in_place", "unmanaged") and not args.force:
        raise Refused("installed copy is not a recorded install (%s); recover it first, or pass --force "
                      "(it will be kept in previous/)" % state)
    restoring = {"helper": (previous / NAME).read_bytes(),
                 "test": (previous / TEST).read_bytes() if (previous / TEST).is_file() else None,
                 "manifest": read_manifest(previous / MANIFEST)}
    displaced = snapshot_install(paths)
    if displaced:
        store_previous(paths, displaced)
    else:
        shutil.rmtree(previous)
    write_bytes(paths["bin"], restoring["helper"], 0o755)
    if restoring["test"] is None:
        if paths["test"].exists():
            paths["test"].unlink()
    else:
        write_bytes(paths["test"], restoring["test"], 0o644)
    manifest = restoring["manifest"] or {"version": MANIFEST_VERSION, "unmanaged": True, "source_commit": None}
    manifest.update(script_sha256=sha(paths["bin"]), test_sha256=sha(paths["test"]), rolled_back_at=now())
    write_json(paths["manifest"], manifest)
    print(json.dumps({"status": "rolled_back", "bin": str(paths["bin"]), "script_sha256": manifest["script_sha256"],
                      "source_commit": manifest.get("source_commit"), "unmanaged": bool(manifest.get("unmanaged"))}))
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("install", "check", "rollback"):
        p = sub.add_parser(name)
        p.add_argument("--prefix", default=os.environ.get("AGENT_PR_REVIEW_LINK_PREFIX") or "~/.local")
        if name != "check":
            p.add_argument("--force", action="store_true")
        if name == "install":
            p.add_argument("--allow-dirty", action="store_true")
    args = parser.parse_args()
    try:
        return {"install": cmd_install, "check": cmd_check, "rollback": cmd_rollback}[args.command](args)
    except (Refused, OSError) as exc:
        print("install: %s" % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
