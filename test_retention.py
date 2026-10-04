"""Synthetic retention contract tests. Never use a real memory or network."""
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

MEMO = Path(__file__).with_name("memo")
loader = importlib.machinery.SourceFileLoader("retention_cli", str(MEMO))
spec = importlib.util.spec_from_loader(loader.name, loader)
cli = importlib.util.module_from_spec(spec)
loader.exec_module(cli)


class RetentionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="optmem-retention-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = self.root / "memory"
        self.env = dict(os.environ, MEMORY_DIR=str(self.store),
                        HOME=str(self.root), USERPROFILE=str(self.root))
        self.run_cli("init")
        self.serial = 0

    def run_cli(self, *args, code=0):
        r = subprocess.run([sys.executable, str(MEMO), *args], env=self.env,
                           capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(r.returncode, code, r.stdout + r.stderr)
        return r.stdout + r.stderr

    def candidate(self, **changes):
        p = dict(lifetime="durable", key="atlas/database", kind="decision",
                 certainty="explicit", source="design:7", future_use="next deploy",
                 text="Use SQLite", replaces=None)
        p.update(changes)
        return p

    def candidate_path(self, p):
        self.serial += 1
        path = self.root / ("candidate-%d.json" % self.serial)
        path.write_text(json.dumps(p, ensure_ascii=False), encoding="utf-8")
        return str(path)

    def retain(self, code=0, **changes):
        return self.run_cli("retain", self.candidate_path(self.candidate(**changes)), code=code)

    def log(self):
        return (self.store / "LOG.txt").read_bytes()

    def test_admission_and_no_write_for_ephemeral_or_sensitive(self):
        for lifetime in ("task", "transient"):
            self.assertIn("Not retained", self.retain(lifetime=lifetime))
        self.assertIn("Not retained", self.retain(sensitive=True))
        self.assertEqual(self.log(), b"")
        self.assertIn("Retained as #0", self.retain())
        self.assertEqual(len(self.log()), cli.LOG_REC)
        out = self.run_cli("wake", "--current")
        for value in ("Use SQLite", "atlas/database", "explicit", "design:7", "next deploy"):
            self.assertIn(value, out)

    def test_update_suppresses_old_claim_before_search_and_preserves_history(self):
        self.retain()
        before = self.log()
        self.assertIn("supersedes #0", self.retain(text="Use PostgreSQL", replaces=0))
        self.assertTrue(self.log().startswith(before))
        current = self.run_cli("wake", "--current")
        self.assertNotIn("SQLite", current)
        self.assertIn("PostgreSQL", current)
        self.assertIn("No match", self.run_cli("recall", "--current", "SQLite"))
        self.assertIn("SQLite", self.run_cli("recall", "SQLite"))

    def test_duplicate_retries_and_scoped_identity(self):
        self.retain()
        before = self.log()
        self.assertIn("Already retained", self.retain())
        self.assertEqual(self.log(), before)
        self.retain(text="Use PostgreSQL", replaces=0)
        before = self.log()
        self.assertIn("Already retained", self.retain(text="Use PostgreSQL", replaces=0))
        self.assertEqual(self.log(), before)
        self.retain(key="other/database")
        self.assertIn("SQLite", self.run_cli("wake", "--current"))

    def test_missing_and_stale_previous_ids_reject_without_writes(self):
        self.retain()
        before = self.log()
        for previous in (None, 20):
            self.assertIn("Conflict", self.retain(code=1, text="Use Redis", replaces=previous))
            self.assertEqual(self.log(), before)
        self.retain(text="Use PostgreSQL", replaces=0)
        before = self.log()
        self.assertIn("Conflict", self.retain(code=1, text="Use Redis", replaces=0))
        self.assertEqual(self.log(), before)
        self.assertIn("Conflict", self.retain(code=1, key="new/key", replaces=0))

    def test_evidence_and_uncertainty_changes_are_real_updates(self):
        self.retain(certainty="tentative")
        self.retain(certainty="observed", replaces=0)
        self.retain(certainty="observed", source="test:9", replaces=1)
        self.retain(certainty="observed", source="test:9", future_use="next release", replaces=2)
        self.assertEqual(len(self.log()), 4 * cli.LOG_REC)
        out = self.run_cli("wake", "--current")
        self.assertIn("observed", out)
        self.assertIn("test:9", out)
        self.assertIn("next release", out)
        self.assertNotIn("tentative", out)

    def test_invalid_candidates_and_utf8_budget_do_not_append(self):
        bad = [dict(future_use=" "), dict(source=""), dict(text="one\ntwo"),
               dict(key="../bad key"), dict(key="k" * 65), dict(kind="chat"),
               dict(certainty="certain"), dict(replaces=True), dict(replaces=-1),
               dict(lifetime="unknown"), dict(sensitive="false"), dict(surprise=1),
               dict(kind="preference", certainty="tentative"), dict(text="é" * 140),
               dict(text="x" * 280)]
        for changes in bad:
            with self.subTest(changes=changes):
                self.retain(code=1, **changes)
                self.assertEqual(self.log(), b"")
        self.retain(text="Café → mañana")
        self.assertIn("Café → mañana", self.run_cli("wake", "--current"))

    def test_non_json_input_is_reported_without_traceback(self):
        for content in (b"{oops", b"\xff"):
            path = self.root / "bad.json"
            path.write_bytes(content)
            out = self.run_cli("retain", str(path), code=1)
            self.assertNotIn("Traceback", out)
            self.assertEqual(self.log(), b"")

    def test_escaped_lone_surrogate_rejects_without_traceback(self):
        path = self.root / "surrogate.json"
        path.write_text(json.dumps(self.candidate(text="\ud800")), encoding="utf-8")
        out = self.run_cli("retain", str(path), code=1)
        self.assertIn("Invalid Unicode", out)
        self.assertNotIn("Traceback", out)
        self.assertEqual(self.log(), b"")
        # An old escaped malformed envelope is displayed literally as legacy.
        p = ["atlas/database", "fact", "explicit", "ref:1", "later", "\ud800", None]
        cli.log_append(str(self.store), [("2026-01-01", cli.RETAIN_PREFIX + json.dumps(p))])
        self.assertIn("[legacy/unreviewed]", self.run_cli("wake", "--current"))

    def test_open_issue_and_resolution_survive_current_reads(self):
        self.retain(kind="open_issue", certainty="tentative", text="Deploy may fail: investigate")
        self.assertIn("open_issue; tentative", self.run_cli("wake", "--current"))
        self.retain(kind="lesson", certainty="observed", text="Fixed deploy by pinning version", replaces=0)
        out = self.run_cli("wake", "--current")
        self.assertIn("Fixed deploy", out)
        self.assertNotIn("may fail", out)

    def test_legacy_and_malformed_envelopes_are_preserved(self):
        self.run_cli("note", "Legacy open decision")
        # Simulate data written by a previous tool, not allowed via new note/import.
        cli.log_append(str(self.store), [("2026-01-01", "@optmem/1 malformed")])
        self.retain()
        out = self.run_cli("wake", "--current")
        self.assertIn("[legacy/unreviewed] Legacy open decision", out)
        self.assertIn("[legacy/unreviewed] @optmem/1 malformed", out)
        before = self.log()
        self.run_cli("note", "@optmem/1 malformed", code=1)
        path = self.root / "import.txt"
        path.write_text("2099-01-01 @optmem/1 malformed\n", encoding="utf-8")
        self.run_cli("import", str(path), code=1)
        self.assertEqual(self.log(), before)
        self.run_cli("init")
        self.assertEqual(self.log(), before)

    def test_bad_chain_never_overwrites_current_fact(self):
        self.retain()
        p = ["atlas/database", "decision", "explicit", "bad:1", "next deploy", "wrong", 90]
        cli.log_append(str(self.store), [("2026-01-01", cli.RETAIN_PREFIX + json.dumps(p))])
        out = self.run_cli("wake", "--current")
        self.assertIn("[legacy/unreviewed]", out)
        self.assertIn("Use SQLite | source", out)
        self.retain(text="Use PostgreSQL", replaces=0)

    def test_snapshot_paging_does_not_shift_when_a_key_is_updated(self):
        for i in range(5):
            self.retain(key="project/key%d" % i, text="original%d" % i)
        self.run_cli("config", "PART_LINES=2", "WAKE_LINES=1")
        expected = [self.run_cli("wake", "--current", str(p), "5") for p in (1, 2, 3)]
        self.retain(key="project/key0", text="replacement", replaces=0)
        actual = [self.run_cli("wake", "--current", str(p), "5") for p in (1, 2, 3)]
        self.assertEqual(expected, actual)
        self.assertIn("wake --current 2 5", actual[0])
        self.assertIn("You are awake.", actual[-1])
        self.assertIn("replacement", self.run_cli("recall", "--current", "project/key0"))
        self.run_cli("wake", "--current", "0", code=1)
        self.run_cli("wake", "--current", "1", "999", code=1)

    def test_current_view_never_reads_stale_tree_summaries(self):
        self.retain()
        self.run_cli("note", "Keep a legacy blocker")
        self.run_cli("nap", "0-1", "Always use SQLite")
        self.retain(text="Use PostgreSQL", replaces=0)
        self.run_cli("config", "WAKE_LINES=1")
        out = self.run_cli("wake", "--current")
        self.assertNotIn("SQLite", out)
        self.assertIn("PostgreSQL", out)
        self.assertIn("legacy blocker", out)

    def test_parallel_same_key_and_stale_updates(self):
        def race(candidates):
            procs = [subprocess.Popen([sys.executable, str(MEMO), "retain", self.candidate_path(p)],
                                      env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                     for p in candidates]
            output = [p.communicate(timeout=30) for p in procs]
            self.assertTrue(all(p.returncode in (0, 1) for p in procs), output)
            return [p.returncode for p in procs]
        codes = race([self.candidate(text="option%d" % i) for i in range(8)])
        self.assertEqual(codes.count(0), 1)
        self.assertEqual(len(self.log()), cli.LOG_REC)
        codes = race([self.candidate(text="update%d" % i, replaces=0) for i in range(8)])
        self.assertEqual(codes.count(0), 1)
        self.assertEqual(len(self.log()), 2 * cli.LOG_REC)

    def test_parallel_identical_retries_append_once(self):
        path = self.candidate_path(self.candidate())
        procs = [subprocess.Popen([sys.executable, str(MEMO), "retain", path], env=self.env,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(8)]
        output = [p.communicate(timeout=30) for p in procs]
        self.assertTrue(all(p.returncode == 0 for p in procs), output)
        self.assertEqual(len(self.log()), cli.LOG_REC)

    def test_torn_tail_does_not_shift_update_ids(self):
        self.retain()
        with (self.store / "LOG.txt").open("ab") as f:
            f.write(b"unfinished record")
        self.retain(text="Use PostgreSQL", replaces=0)
        self.assertEqual(len(self.log()), 2 * cli.LOG_REC)
        self.assertIn("#1", self.run_cli("wake", "--current"))

    def test_powershell_utf8_bom_candidate(self):
        path = self.root / "candidate-bom.json"
        path.write_text(json.dumps(self.candidate()), encoding="utf-8-sig")
        self.assertIn("Retained as #0", self.run_cli("retain", str(path)))
        self.assertIn("SQLite", self.run_cli("wake", "--current"))

    def test_printed_commands_execute_with_spaces_quotes_and_dollar_in_path(self):
        directory = self.root / "O'Brien $cash [tool directory]"
        directory.mkdir()
        copied = directory / "memo"
        shutil.copyfile(MEMO, copied)
        copied.chmod(0o755)

        def execute(args):
            result = subprocess.run(args, env=self.env, capture_output=True,
                                    text=True, encoding="utf-8")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            return result.stdout

        def obey(command):
            if os.name == "nt":
                shell = shutil.which("pwsh") or shutil.which("powershell")
                self.assertIsNotNone(shell, "PowerShell needed for native Windows command test")
                return execute([shell, "-NoProfile", "-NonInteractive", "-Command", command])
            return execute(["/bin/sh", "-c", command])

        command = [sys.executable, str(copied)]
        execute(command + ["note", "first legacy item"])
        out = execute(command + ["note", "second legacy item"])
        offered = next(line[5:] for line in out.splitlines() if line.startswith("Run: "))
        self.assertIn("saved", obey(offered.replace('"<your line>"', '"both legacy items"')))
        execute(command + ["config", "PART_LINES=1"])
        out = execute(command + ["wake", "--current"])
        offered = next(line.split("Run: ", 1)[1] for line in out.splitlines()
                       if line.startswith("Not awake yet."))
        out = obey(offered)
        self.assertIn("second legacy item", out)
        self.assertIn("You are awake.", out)


if __name__ == "__main__":
    unittest.main()
