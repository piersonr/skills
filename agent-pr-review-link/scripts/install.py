#!/usr/bin/env python3
"""Install agent-pr-review-link from this repository onto PATH, repeatably.

    python3 agent-pr-review-link/scripts/install.py install   [--prefix ~/.local] [--allow-dirty] [--force]
    python3 agent-pr-review-link/scripts/install.py check     [--prefix ~/.local]
    python3 agent-pr-review-link/scripts/install.py rollback  [--prefix ~/.local]

This repository is the source of truth. The installed copy is an artifact:
<prefix>/bin/agent-pr-review-link, with its tests, an INSTALL.json manifest, and
the previous version under <prefix>/share/agent-pr-review-link/. Never edit the
installed copy; change the source here, commit, and install again.

install refuses a source with uncommitted changes (unless --allow-dirty, which
is recorded), and refuses to overwrite an installed copy that no longer matches
its manifest -- an in-place edit -- unless --force. Copies, not symlinks, so a
branch switch in this checkout cannot silently change what is installed.

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
from datetime import datetime, timezone

NAME = "agent-pr-review-link"
TEST = "test_agent_pr_review_link.py"
SOURCE_DIR = Path(__file__).resolve().parent
MANIFEST_VERSION = 1


class Refused(Exception):
    pass


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest() if Path(path).is_file() else None


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
            "manifest": share / "INSTALL.json", "previous": share / "previous"}


def read_manifest(paths):
    try:
        data = json.loads(paths["manifest"].read_text())
    except FileNotFoundError:
        return None
    except ValueError:
        raise Refused("install manifest is unreadable: %s" % paths["manifest"])
    return data if isinstance(data, dict) and data.get("version") == MANIFEST_VERSION else None


def installed_state(paths, source):
    """Describe the installed copy relative to its manifest and to this source."""
    current = sha(paths["bin"])
    manifest = read_manifest(paths)
    if current is None:
        return "not_installed", current, manifest
    if manifest and current != manifest.get("script_sha256"):
        return "edited_in_place", current, manifest
    if current == source["script_sha256"]:
        return ("current" if manifest else "unmanaged_match"), current, manifest
    return ("behind_source" if manifest else "unmanaged_differs"), current, manifest


def atomic_copy(src, dest, mode):
    dest.parent.mkdir(parents=True, exist_ok=True)
    temp = dest.with_name(".%s.tmp.%d" % (dest.name, os.getpid()))
    shutil.copyfile(src, temp)
    os.chmod(temp, mode)
    os.replace(temp, dest)


def write_manifest(paths, data):
    paths["share"].mkdir(parents=True, exist_ok=True)
    temp = paths["manifest"].with_suffix(".tmp")
    temp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    os.replace(temp, paths["manifest"])


def cmd_install(args):
    paths = layout(args.prefix)
    source = source_identity()
    if source["source_dirty"] and not args.allow_dirty:
        raise Refused("source has uncommitted changes; commit them or pass --allow-dirty")
    state, current, manifest = installed_state(paths, source)
    if state in ("edited_in_place", "unmanaged_differs") and not args.force:
        raise Refused("installed copy at %s does not match any recorded install (%s); recover its "
                      "changes into this repository first, or pass --force to replace it"
                      % (paths["bin"], state))
    if state == "current" and manifest.get("source_commit") == source["source_commit"] \
            and manifest.get("test_sha256") == source["test_sha256"]:
        print(json.dumps({"status": "up_to_date", "bin": str(paths["bin"]), **source}))
        return 0
    previous_sha = None
    if current is not None and current != source["script_sha256"]:
        paths["previous"].mkdir(parents=True, exist_ok=True)
        shutil.copy2(paths["bin"], paths["previous"] / NAME)
        if manifest:
            (paths["previous"] / "INSTALL.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        previous_sha = current
    atomic_copy(SOURCE_DIR / NAME, paths["bin"], 0o755)
    atomic_copy(SOURCE_DIR / TEST, paths["test"], 0o644)
    record = {"version": MANIFEST_VERSION, **source, "installed_at": datetime.now(timezone.utc).isoformat(),
              "bin": str(paths["bin"]), "previous_script_sha256": previous_sha, "replaced_state": state}
    write_manifest(paths, record)
    print(json.dumps({"status": "installed", **record}))
    return 0


def cmd_check(args):
    paths = layout(args.prefix)
    source = source_identity()
    state, current, manifest = installed_state(paths, source)
    print(json.dumps({"status": state, "bin": str(paths["bin"]), "installed_script_sha256": current,
                      "manifest_source_commit": (manifest or {}).get("source_commit"),
                      "manifest_source_dirty": (manifest or {}).get("source_dirty"),
                      "source_commit": source["source_commit"], "source_dirty": source["source_dirty"],
                      "source_script_sha256": source["script_sha256"]}, indent=2))
    return 0 if state == "current" else 1


def cmd_rollback(args):
    paths = layout(args.prefix)
    saved = paths["previous"] / NAME
    if not saved.is_file():
        raise Refused("no previous install is recorded under %s" % paths["previous"])
    state, current, manifest = installed_state(paths, source_identity())
    if state == "edited_in_place" and not args.force:
        raise Refused("installed copy was edited in place; recover it first, or pass --force")
    atomic_copy(saved, paths["bin"], 0o755)
    old = paths["previous"] / "INSTALL.json"
    record = json.loads(old.read_text()) if old.is_file() else {"version": MANIFEST_VERSION}
    record.update(script_sha256=sha(paths["bin"]), rolled_back_at=datetime.now(timezone.utc).isoformat(),
                  rolled_back_from_sha256=current)
    write_manifest(paths, record)
    print(json.dumps({"status": "rolled_back", "bin": str(paths["bin"]), "script_sha256": record["script_sha256"],
                      "source_commit": record.get("source_commit")}))
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("install", "check", "rollback"):
        p = sub.add_parser(name)
        p.add_argument("--prefix", default=os.environ.get("AGENT_PR_REVIEW_LINK_PREFIX", "~/.local"))
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
