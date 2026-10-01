#!/usr/bin/env python3
"""Installer tests. Each test copies this skill into a disposable Git repository and
installs into a temporary prefix, so the real ~/.local is never touched."""

import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent


class InstallTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="aprl-install-test-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.repo = self.root / "repo"
        self.scripts = self.repo / "agent-pr-review-link" / "scripts"
        self.scripts.mkdir(parents=True)
        for name in ("agent-pr-review-link", "test_agent_pr_review_link.py", "install.py"):
            shutil.copy2(HERE / name, self.scripts / name)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "test@example.invalid")
        self.git("config", "user.name", "Test")
        self.git("config", "commit.gpgsign", "false")
        self.git("add", "-A")
        self.git("commit", "-qm", "v1")
        self.prefix = self.root / "prefix"
        self.bin = self.prefix / "bin" / "agent-pr-review-link"
        self.share = self.prefix / "share" / "agent-pr-review-link"

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.repo), *args], text=True).strip()

    def run_installer(self, *args, prefix=True, **extra_env):
        env = {k: v for k, v in os.environ.items() if k != "AGENT_PR_REVIEW_LINK_PREFIX"}
        env.update(extra_env)
        tail = ["--prefix", str(self.prefix)] if prefix else []
        done = subprocess.run([sys.executable, str(self.scripts / "install.py"), *args, *tail],
                              capture_output=True, text=True, env=env)
        return done.returncode, (json.loads(done.stdout) if done.stdout.strip() else None), done.stderr

    def commit_change(self, marker, name="agent-pr-review-link"):
        path = self.scripts / name
        path.write_text(path.read_text() + "\n# %s\n" % marker)
        self.git("commit", "-qam", marker)

    def test_install_records_provenance_and_is_idempotent(self):
        code, out, err = self.run_installer("install")
        self.assertEqual(code, 0, err)
        self.assertEqual(out["status"], "installed")
        self.assertEqual(out["source_commit"], self.git("rev-parse", "HEAD"))
        self.assertFalse(out["source_dirty"])
        self.assertTrue(self.bin.stat().st_mode & stat.S_IXUSR)
        self.assertFalse(self.bin.is_symlink())
        self.assertEqual(self.bin.read_bytes(), (self.scripts / "agent-pr-review-link").read_bytes())
        manifest = json.loads((self.share / "INSTALL.json").read_text())
        self.assertEqual(manifest["script_sha256"], out["script_sha256"])
        self.assertEqual(self.run_installer("check")[0], 0)
        code, out, _ = self.run_installer("install")
        self.assertEqual((code, out["status"]), (0, "up_to_date"))

    def test_installed_tests_exercise_the_installed_copy(self):
        self.run_installer("install")
        env = {k: v for k, v in os.environ.items() if k != "AGENT_PR_REVIEW_LINK"}
        # The whole recovered suite, run from the install location with no override.
        done = subprocess.run([sys.executable, str(self.share / "test_agent_pr_review_link.py")],
                              capture_output=True, text=True, env=env)
        self.assertNotIn("No such file", done.stderr)
        self.assertEqual(done.returncode, 0, done.stderr[-800:])
        self.assertRegex(done.stderr, r"Ran [1-9]\d* tests")

    def test_dirty_source_refused_unless_recorded(self):
        (self.scripts / "agent-pr-review-link").write_text("#!/usr/bin/env python3\n# uncommitted\n")
        code, _, err = self.run_installer("install")
        self.assertEqual(code, 2)
        self.assertIn("uncommitted changes", err)
        self.assertFalse(self.bin.exists())
        code, out, _ = self.run_installer("install", "--allow-dirty")
        self.assertEqual(code, 0)
        self.assertTrue(out["source_dirty"])

    def test_in_place_edit_is_detected_and_protected(self):
        self.run_installer("install")
        self.bin.write_text(self.bin.read_text() + "\n# hot fix typed into the installed copy\n")
        code, out, _ = self.run_installer("check")
        self.assertEqual((code, out["status"]), (1, "edited_in_place"))
        self.commit_change("v2")
        code, _, err = self.run_installer("install")
        self.assertEqual(code, 2)
        self.assertIn("recover its changes", err)
        self.assertIn("hot fix", self.bin.read_text())
        self.assertEqual(self.run_installer("install", "--force")[0], 0)
        self.assertEqual(self.run_installer("check")[1]["status"], "current")

    def test_unmanaged_existing_copy(self):
        self.bin.parent.mkdir(parents=True)
        shutil.copy2(self.scripts / "agent-pr-review-link", self.bin)
        self.assertEqual(self.run_installer("check")[1]["status"], "unmanaged_match")
        self.assertEqual(self.run_installer("install")[1]["replaced_state"], "unmanaged_match")
        self.bin.unlink()
        self.bin.write_text("#!/bin/sh\n# something else\n")
        (self.share / "INSTALL.json").unlink()
        code, _, err = self.run_installer("install")
        self.assertEqual(code, 2)
        self.assertIn("unmanaged_differs", err)

    def test_upgrade_keeps_previous_for_rollback(self):
        self.run_installer("install")
        first = self.bin.read_bytes()
        self.commit_change("v2")
        self.assertEqual(self.run_installer("check")[1]["status"], "behind_source")
        code, out, _ = self.run_installer("install")
        self.assertEqual(code, 0)
        self.assertEqual(out["replaced_state"], "behind_source")
        self.assertNotEqual(self.bin.read_bytes(), first)
        code, out, _ = self.run_installer("rollback")
        self.assertEqual((code, out["status"]), (0, "rolled_back"))
        self.assertEqual(self.bin.read_bytes(), first)
        self.assertEqual(self.run_installer("check")[1]["status"], "behind_source")

    def test_rollback_restores_tests_and_can_be_undone(self):
        self.run_installer("install")
        v1 = (self.bin.read_bytes(), (self.share / "test_agent_pr_review_link.py").read_bytes())
        self.commit_change("v2")
        self.commit_change("v2 tests", name="test_agent_pr_review_link.py")
        self.run_installer("install")
        v2 = (self.bin.read_bytes(), (self.share / "test_agent_pr_review_link.py").read_bytes())
        self.assertNotEqual(v1, v2)
        self.assertEqual(self.run_installer("rollback")[0], 0)
        self.assertEqual((self.bin.read_bytes(), (self.share / "test_agent_pr_review_link.py").read_bytes()), v1)
        self.assertEqual(self.run_installer("rollback")[0], 0)  # the swap is reversible
        self.assertEqual((self.bin.read_bytes(), (self.share / "test_agent_pr_review_link.py").read_bytes()), v2)
        self.assertEqual(self.run_installer("check")[1]["status"], "current")

    def test_test_only_update_is_installed_and_kept_for_rollback(self):
        self.run_installer("install")
        old_test = (self.share / "test_agent_pr_review_link.py").read_bytes()
        self.commit_change("tests only", name="test_agent_pr_review_link.py")
        self.assertEqual(self.run_installer("check")[1]["status"], "behind_source")
        self.assertEqual(self.run_installer("install")[1]["status"], "installed")
        self.assertEqual((self.share / "previous" / "test_agent_pr_review_link.py").read_bytes(), old_test)

    def test_missing_installed_tests_are_detected_and_repaired(self):
        self.run_installer("install")
        (self.share / "test_agent_pr_review_link.py").unlink()
        code, out, _ = self.run_installer("check")
        self.assertEqual((code, out["status"]), (1, "tests_differ"))
        code, out, _ = self.run_installer("install")
        self.assertEqual((code, out["status"]), (0, "installed"))
        self.assertEqual(self.run_installer("check")[1]["status"], "current")

    def test_rollback_never_silently_discards_an_unrecorded_copy(self):
        self.run_installer("install")
        self.commit_change("v2")
        self.run_installer("install")
        (self.share / "INSTALL.json").unlink()
        self.bin.write_text("#!/bin/sh\n# mine\n")
        code, _, err = self.run_installer("rollback")
        self.assertEqual(code, 2)
        self.assertIn("not a recorded install", err)
        self.assertIn("# mine", self.bin.read_text())
        code, out, _ = self.run_installer("rollback", "--force")
        self.assertEqual(code, 0)
        self.assertIn("# mine", (self.share / "previous" / "agent-pr-review-link").read_text())
        kept = json.loads((self.share / "previous" / "INSTALL.json").read_text())
        self.assertTrue(kept["unmanaged"])
        self.assertIsNone(kept["source_commit"])

    def test_forced_replacement_records_unmanaged_provenance(self):
        self.run_installer("install")
        (self.share / "INSTALL.json").unlink()
        self.bin.write_text("#!/bin/sh\n# mine2\n")
        self.assertEqual(self.run_installer("install", "--force")[0], 0)
        code, out, _ = self.run_installer("rollback")
        self.assertEqual(code, 0)
        self.assertTrue(out["unmanaged"])
        self.assertIsNone(out["source_commit"])
        self.assertIn("# mine2", self.bin.read_text())

    def test_interrupted_install_is_not_mistaken_for_a_hand_edit(self):
        self.run_installer("install")
        self.commit_change("v2")
        shutil.copy2(self.scripts / "agent-pr-review-link", self.bin)  # crashed before the manifest
        self.assertEqual(self.run_installer("check")[1]["status"], "interrupted_install")
        code, out, err = self.run_installer("install")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.run_installer("check")[1]["status"], "current")

    def test_prefix_from_environment_and_home(self):
        code, out, err = self.run_installer("install", prefix=False,
                                            AGENT_PR_REVIEW_LINK_PREFIX=str(self.prefix))
        self.assertEqual(code, 0, err)
        self.assertTrue(self.bin.is_file())
        home = self.root / "home"
        home.mkdir()
        code, out, err = self.run_installer("install", prefix=False, HOME=str(home))
        self.assertEqual(code, 0, err)
        self.assertTrue((home / ".local" / "bin" / "agent-pr-review-link").is_file())

    def test_repairing_an_interrupted_install_keeps_the_real_previous_version(self):
        self.run_installer("install")
        v1_helper = self.bin.read_bytes()
        self.commit_change("v2")
        self.commit_change("v2 tests", name="test_agent_pr_review_link.py")
        self.run_installer("install")  # previous/ = v1
        self.commit_change("v3")
        self.commit_change("v3 tests", name="test_agent_pr_review_link.py")
        shutil.copy2(self.scripts / "agent-pr-review-link", self.bin)  # v3 helper landed, tests did not
        self.assertEqual(self.run_installer("check")[1]["status"], "interrupted_install")
        self.assertEqual(self.run_installer("install")[0], 0)
        self.assertEqual(self.run_installer("check")[1]["status"], "current")
        previous = self.share / "previous"
        self.assertEqual((previous / "agent-pr-review-link").read_bytes(), v1_helper)
        self.assertFalse(json.loads((previous / "INSTALL.json").read_text()).get("unmanaged"))

    def test_rollback_to_a_recorded_unmanaged_copy_can_be_undone(self):
        self.run_installer("install")
        (self.share / "INSTALL.json").unlink()
        self.bin.write_text("#!/bin/sh\n# mine\n")
        self.run_installer("install", "--force")
        self.assertEqual(self.run_installer("rollback")[0], 0)  # back to "# mine", recorded unmanaged
        code, out, err = self.run_installer("rollback")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.bin.read_bytes(), (self.scripts / "agent-pr-review-link").read_bytes())

    def test_failed_rollback_keeps_the_rollback_target(self):
        self.run_installer("install")
        v1 = self.bin.read_bytes()
        self.commit_change("v2")
        self.run_installer("install")
        self.bin.unlink()
        self.bin.mkdir()  # the restore cannot be written over a directory
        (self.bin / "x").write_text("x")
        self.assertEqual(self.run_installer("rollback", "--force")[0], 2)
        self.assertEqual((self.share / "previous" / "agent-pr-review-link").read_bytes(), v1)

    def test_rollback_does_not_launder_an_edited_installed_test(self):
        self.run_installer("install")
        self.commit_change("v2")
        self.run_installer("install")
        test = self.share / "test_agent_pr_review_link.py"
        test.write_text(test.read_text() + "\n# edited in place\n")
        self.assertEqual(self.run_installer("rollback")[0], 0)
        kept = json.loads((self.share / "previous" / "INSTALL.json").read_text())
        self.assertTrue(kept["unmanaged"])
        self.run_installer("rollback")
        self.assertNotEqual(self.run_installer("check")[1]["status"], "current")

    def test_rollback_without_previous_refused(self):
        self.run_installer("install")
        self.assertEqual(self.run_installer("rollback")[0], 2)

    def test_source_outside_git_refused(self):
        shutil.rmtree(self.repo / ".git")
        code, _, err = self.run_installer("install")
        self.assertEqual(code, 2)
        self.assertIn("not inside a Git checkout", err)


if __name__ == "__main__":
    unittest.main()
