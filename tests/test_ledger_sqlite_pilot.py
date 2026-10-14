"""P2C-P3-P1: SQLite CAS rehearsal on disposable local database files only."""
from __future__ import annotations

from contextlib import closing
from copy import deepcopy
from pathlib import Path
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from knowledge_forge.ledger_sqlite_pilot import (
    KIND, SQLiteLedgerPilot, SQLitePilotError,
)

T0 = "2026-10-14T17:00:00Z"
T1 = "2026-10-14T17:01:00Z"
T2 = "2026-10-14T17:05:00Z"
T3 = "2026-10-14T17:10:00Z"
BINDING = "sha256:" + "a" * 64
WRONG = "sha256:" + "b" * 64
ID = "synthetic.pilot.0001"


class SQLiteLedgerPilotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / "pilot.sqlite3"
        self.pilot = SQLiteLedgerPilot(self.db)

    def prepared(self, *, id=ID):
        return self.pilot.prepare(simulation_id=id, binding_sha256=BINDING,
                                  created_at=T0, expires_at=T3)

    def move(self, *, action="CONSUME_SIMULATION", id=ID, binding=BINDING,
             revision=1, at=T1):
        return self.pilot.transition(simulation_id=id, binding_sha256=binding,
                                     expected_revision=revision, action=action,
                                     occurred_at=at)

    def test_new_file_only_when_prepared(self):
        self.assertFalse(self.db.exists())
        self.prepared()
        self.assertTrue(self.db.is_file())

    def test_prepared_untrusted_state(self):
        value = self.prepared()
        self.assertEqual(value["kind"], KIND)
        self.assertEqual(value["state"], "PREPARED_SIMULATION")
        self.assertEqual(value["revision"], 1)

    def test_all_outputs_zero_authority(self):
        self.prepared()
        for value in (self.pilot.inspect(ID), self.move()):
            self.assertEqual(value["execution_authority"], "NONE")
            self.assertEqual(value["tool_grants"], [])
            for key in ("human_authenticated", "authorization_issued", "operator_invoked",
                        "promotion_authorized", "registry_mutated", "protected_host_verified"):
                self.assertIs(value[key], False)

    def test_consume_once(self):
        self.prepared()
        done = self.move()
        self.assertEqual(done["state"], "CONSUMED_SIMULATION")
        self.assertEqual(done["revision"], 2)
        with self.assertRaises(SQLitePilotError):
            self.move()
        self.assertEqual(self.pilot.inspect(ID), done)

    def test_duplicate_id_refused(self):
        original = self.prepared()
        with self.assertRaises(SQLitePilotError):
            self.prepared()
        self.assertEqual(self.pilot.inspect(ID), original)

    def test_stale_revision_refused(self):
        self.prepared()
        with self.assertRaises(SQLitePilotError):
            self.move(revision=2)

    def test_revision_boolean_refused(self):
        self.prepared()
        with self.assertRaises(SQLitePilotError):
            self.move(revision=True)

    def test_wrong_binding_refused(self):
        self.prepared()
        with self.assertRaises(SQLitePilotError):
            self.move(binding=WRONG)

    def test_expiry_refused(self):
        self.prepared()
        with self.assertRaises(SQLitePilotError):
            self.move(at=T3)

    def test_expired_cannot_cancel(self):
        self.prepared()
        with self.assertRaises(SQLitePilotError):
            self.move(action="CANCEL_SIMULATION", at=T3)

    def test_cancellation_is_terminal(self):
        self.prepared()
        cancelled = self.move(action="CANCEL_SIMULATION")
        self.assertEqual(cancelled["state"], "CANCELLED_SIMULATION")
        with self.assertRaises(SQLitePilotError):
            self.move()

    def test_recovery_requires_reconciliation(self):
        self.prepared()
        recovery = self.move(action="RECOVER_UNCERTAIN")
        self.assertEqual(recovery["state"], "RECONCILIATION_REQUIRED")
        with self.assertRaises(SQLitePilotError):
            self.move(action="CONSUME_SIMULATION")

    def test_recovery_after_expiry_is_uncertain(self):
        self.prepared()
        result = self.move(action="RECOVER_UNCERTAIN", at=T3)
        self.assertEqual(result["state"], "RECONCILIATION_REQUIRED")

    def test_restart_persists_consumption(self):
        self.prepared()
        consumed = self.move()
        other = SQLiteLedgerPilot(self.db)
        self.assertEqual(other.inspect(ID), consumed)
        with self.assertRaises(SQLitePilotError):
            other.transition(simulation_id=ID, binding_sha256=BINDING,
                             expected_revision=1, action="CONSUME_SIMULATION", occurred_at=T2)

    def test_duplicate_simulation_does_not_clobber_other(self):
        first = self.prepared()
        second = self.prepared(id="synthetic.pilot.0002")
        self.assertEqual(first["revision"], second["revision"])
        self.move(id="synthetic.pilot.0002")
        self.assertEqual(self.pilot.inspect(ID), first)

    def test_bad_timestamp_refused(self):
        for bad in ("2026-10-14T17:00Z", "2026-10-14T17:00:00+00:00", "bad", None):
            with self.subTest(bad=bad), self.assertRaises(SQLitePilotError):
                self.pilot.prepare(simulation_id=ID, binding_sha256=BINDING,
                                   created_at=bad, expires_at=T3)

    def test_overlong_expiry_refused(self):
        with self.assertRaises(SQLitePilotError):
            self.pilot.prepare(simulation_id=ID, binding_sha256=BINDING,
                               created_at=T0, expires_at="2026-10-14T17:16:00Z")

    def test_backwards_expiry_refused(self):
        with self.assertRaises(SQLitePilotError):
            self.pilot.prepare(simulation_id=ID, binding_sha256=BINDING,
                               created_at=T3, expires_at=T0)

    def test_backwards_transition_refused(self):
        self.prepared()
        with self.assertRaises(SQLitePilotError):
            self.move(at="2026-10-14T16:59:59Z")

    def test_invalid_id_and_digest_refused(self):
        for id in ("X", "../../evil", None, "bad id"):
            with self.subTest(id=id), self.assertRaises(SQLitePilotError):
                self.pilot.prepare(simulation_id=id, binding_sha256=BINDING,
                                   created_at=T0, expires_at=T3)
        with self.assertRaises(SQLitePilotError):
            self.pilot.prepare(simulation_id=ID, binding_sha256="a" * 64,
                               created_at=T0, expires_at=T3)

    def test_unsupported_action_refused(self):
        self.prepared()
        with self.assertRaises(SQLitePilotError):
            self.move(action="AUTHORIZE")

    def test_stored_event_tamper_refused(self):
        self.prepared()
        with closing(sqlite3.connect(self.db)) as db:
            db.execute("UPDATE rehearsal_events SET action='PROMOTE' WHERE id=?", (ID,)); db.commit()
        with self.assertRaises(SQLitePilotError):
            self.pilot.inspect(ID)

    def test_stored_state_tamper_refused(self):
        self.prepared()
        with closing(sqlite3.connect(self.db)) as db:
            db.execute("UPDATE rehearsal_decisions SET state='HOST_APPROVED' WHERE id=?", (ID,)); db.commit()
        with self.assertRaises(SQLitePilotError):
            self.pilot.inspect(ID)

    def test_stored_digest_tamper_refused(self):
        self.prepared()
        with closing(sqlite3.connect(self.db)) as db:
            db.execute("UPDATE rehearsal_decisions SET last_event_sha256=? WHERE id=?", (WRONG, ID)); db.commit()
        with self.assertRaises(SQLitePilotError):
            self.pilot.inspect(ID)

    def test_stored_binding_tamper_refused(self):
        self.prepared()
        with closing(sqlite3.connect(self.db)) as db:
            db.execute("UPDATE rehearsal_decisions SET binding_sha256=? WHERE id=?", (WRONG, ID))
            db.commit()
        with self.assertRaises(SQLitePilotError):
            self.pilot.inspect(ID)

    def test_stored_expiry_tamper_refused(self):
        self.prepared()
        with closing(sqlite3.connect(self.db)) as db:
            db.execute("UPDATE rehearsal_decisions SET expires_at=? WHERE id=?", (T2, ID))
            db.commit()
        with self.assertRaises(SQLitePilotError):
            self.pilot.inspect(ID)

    def test_db_file_symlink_refused(self):
        try:
            self.db.symlink_to(Path(self.temp.name) / "elsewhere")
        except (OSError, NotImplementedError):
            self.skipTest("symlink creation unavailable on this test host")
        with self.assertRaises(SQLitePilotError):
            SQLiteLedgerPilot(self.db)

    def test_db_parent_symlink_refused(self):
        link = Path(self.temp.name) / "link"
        try:
            link.symlink_to(self.temp.name, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlink creation unavailable on this test host")
        with self.assertRaises(SQLitePilotError):
            SQLiteLedgerPilot(link / "pilot.sqlite3")

    def test_relative_path_refused(self):
        with self.assertRaises(SQLitePilotError):
            SQLiteLedgerPilot("pilot.sqlite3")

    def test_protected_paths_refused(self):
        for name in ("/var/lib/toadaid-kf-operator/pilot.sqlite3",
                     "/opt/toadaid-kf-operator/pilot.sqlite3"):
            with self.subTest(path=name), self.assertRaises(SQLitePilotError):
                SQLiteLedgerPilot(name)

    def test_two_processes_exactly_one_consume(self):
        self.prepared()
        src = Path(__file__).resolve().parents[1] / "src"
        program = """import sys
sys.path.insert(0, sys.argv[1])
from knowledge_forge.ledger_sqlite_pilot import SQLiteLedgerPilot, SQLitePilotError
p=SQLiteLedgerPilot(sys.argv[2])
print('READY', flush=True)
sys.stdin.readline()
try:
    p.transition(simulation_id=sys.argv[3], binding_sha256=sys.argv[4], expected_revision=1, action='CONSUME_SIMULATION', occurred_at=sys.argv[5])
    print('CONSUMED', flush=True)
except SQLitePilotError:
    print('REFUSED', flush=True)
"""
        args = [sys.executable, "-c", program, str(src), str(self.db), ID, BINDING, T1]
        children = [subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, text=True) for _ in range(2)]
        try:
            for child in children:
                self.assertEqual(child.stdout.readline().strip(), "READY")
            for child in children:
                child.stdin.write("GO\n"); child.stdin.flush()
            values = []
            for child in children:
                out, err = child.communicate(timeout=15)
                self.assertEqual(child.returncode, 0, err)
                values.append(out.strip())
            self.assertCountEqual(values, ["CONSUMED", "REFUSED"])
            self.assertEqual(self.pilot.inspect(ID)["state"], "CONSUMED_SIMULATION")
        finally:
            for child in children:
                if child.poll() is None:
                    child.kill(); child.communicate()

    def test_committed_before_response_crash_refuses_replay(self):
        self.prepared()
        src = Path(__file__).resolve().parents[1] / "src"
        program = """import os,sys
sys.path.insert(0,sys.argv[1])
from knowledge_forge.ledger_sqlite_pilot import SQLiteLedgerPilot
p=SQLiteLedgerPilot(sys.argv[2])
p.transition(simulation_id=sys.argv[3],binding_sha256=sys.argv[4],expected_revision=1,action='CONSUME_SIMULATION',occurred_at=sys.argv[5])
os._exit(23)
"""
        child = subprocess.run([sys.executable, "-c", program, str(src),
                                str(self.db), ID, BINDING, T1], capture_output=True,
                               text=True, timeout=15)
        self.assertEqual(child.returncode, 23, child.stderr)
        self.assertEqual(SQLiteLedgerPilot(self.db).inspect(ID)["state"], "CONSUMED_SIMULATION")
        with self.assertRaises(SQLitePilotError):
            self.move()

    def test_no_privileged_or_operator_imports(self):
        import ast
        source = Path(__file__).resolve().parents[1] / "src" / "knowledge_forge" / "ledger_sqlite_pilot.py"
        import_names = set()
        for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                import_names.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                import_names.add(node.module.split(".")[0])
        self.assertFalse(import_names & {"operator_cli", "operator_promotion", "sudo", "pwd", "grp", "subprocess", "socket", "ctypes", "winreg"})


if __name__ == "__main__":
    unittest.main()
