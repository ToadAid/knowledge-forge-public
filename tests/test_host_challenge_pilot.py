"""P2C-P3-P2-P0: test-only challenge protocol and adversarial concurrency."""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
import ast
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from knowledge_forge.host_challenge_pilot import (
    HostChallengePilot, ChallengePilotError, KIND, PURPOSE,
)

START = datetime(2026, 10, 14, 18, 20, tzinfo=timezone.utc)

def at(seconds=0):
    return START + timedelta(seconds=seconds)

def iso(clock):
    return clock.strftime("%Y-%m-%dT%H:%M:%SZ")

def sha(c):
    return "sha256:" + c * 64

def binding():
    return {
        "request_sha256": sha("a"),
        "target_skill_id": "knowledge-forge:roof.quote@" + sha("b"),
        "target_skill_version": 1,
        "package_receipt_sha256": sha("c"),
        "validated_lifecycle_head_sha256": sha("d"),
        "expected_registry_head_sha256": sha("e"),
    }


class HostChallengePilotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="kf-challenge-pilot-")
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "pilot.sqlite3"
        self.clock = [at()]
        self.pilot = HostChallengePilot(self.path, test_clock=lambda: self.clock[0])

    def issue(self, **kwargs):
        return self.pilot.issue(bindings=binding(), request_expires_at=iso(at(600)), **kwargs)

    def step(self, challenge, **kwargs):
        return self.pilot.transition(challenge_id=challenge["challenge_id"],
                                     bindings=binding(), expected_revision=1,
                                     action="SPEND_SIMULATION", **kwargs)

    def test_issue_uses_256_bit_random_identifier(self):
        v = self.issue()
        self.assertEqual(len(v["challenge_id"]), 64)
        int(v["challenge_id"], 16)
        self.assertEqual(v["state"], "ISSUED_SIMULATION")
        self.assertEqual(v["expires_at"], iso(at(300)))
        self.assertEqual(v["revision"], 1)

    def test_two_issues_have_distinct_identifiers(self):
        a, b = self.issue(), self.issue()
        self.assertNotEqual(a["challenge_id"], b["challenge_id"])
        self.assertEqual(self.pilot.inspect(a["challenge_id"]), a)

    def test_duplicate_generated_id_refused_not_overwritten(self):
        with patch("knowledge_forge.host_challenge_pilot.secrets.token_hex", return_value="f"*64):
            first = self.issue()
            with self.assertRaises(ChallengePilotError):
                self.issue()
        self.assertEqual(first, self.pilot.inspect(first["challenge_id"]))

    def test_invalid_generated_id_refused(self):
        with patch("knowledge_forge.host_challenge_pilot.secrets.token_hex", return_value="abc"):
            with self.assertRaises(ChallengePilotError):
                self.issue()
        self.assertFalse(self.path.exists())

    def test_spend_is_simulation_and_one_use(self):
        v = self.issue()
        self.clock[0] = at(10)
        spent = self.step(v)
        self.assertEqual(spent["state"], "SPENT_SIMULATION")
        self.assertEqual(spent["revision"], 2)
        with self.assertRaises(ChallengePilotError):
            self.step(v)
        self.assertEqual(spent, self.pilot.inspect(v["challenge_id"]))

    def test_cancellation_terminal(self):
        v = self.issue()
        self.clock[0] = at(2)
        res = self.pilot.transition(challenge_id=v["challenge_id"], bindings=binding(),
                                    expected_revision=1, action="CANCEL_SIMULATION")
        self.assertEqual(res["state"], "CANCELLED_SIMULATION")
        with self.assertRaises(ChallengePilotError):
            self.step(v)

    def test_cannot_spend_at_expiry(self):
        v = self.issue()
        self.clock[0] = at(300)
        with self.assertRaises(ChallengePilotError):
            self.step(v)

    def test_expiry_is_bounded_by_request(self):
        v = self.issue(lifetime_seconds=850)
        self.assertEqual(v["expires_at"], iso(at(600)))

    def test_zero_expiry_refused(self):
        with self.assertRaises(ChallengePilotError):
            self.pilot.issue(bindings=binding(), request_expires_at=iso(at()))

    def test_long_request_expiry_refused(self):
        with self.assertRaises(ChallengePilotError):
            self.pilot.issue(bindings=binding(), request_expires_at=iso(at(901)))

    def test_max_ttl_fence(self):
        for ttl in (0, 901, -1, True, "300", None):
            with self.subTest(ttl=ttl), self.assertRaises(ChallengePilotError):
                self.issue(lifetime_seconds=ttl)

    def test_all_outputs_permanent_zero_authority(self):
        v = self.issue()
        self.clock[0] = at(2)
        for obj in (v, self.pilot.inspect(v["challenge_id"]), self.step(v)):
            self.assertEqual(obj["kind"], KIND)
            self.assertEqual(obj["execution_authority"], "NONE")
            self.assertEqual(obj["tool_grants"], [])
            self.assertEqual(obj["challenge_source"], "CALLER_PROCESS_UNTRUSTED")
            self.assertEqual(obj["time_source"], "CALLER_PROCESS_UNTRUSTED")
            for name in ("human_authenticated", "host_isolation_verified", "trusted_clock_verified",
                         "protected_storage_verified", "authorization_issued", "operator_invoked",
                         "promotion_authorized", "registry_mutated"):
                self.assertIs(obj[name], False, name)

    def test_wrong_request_binding_refused(self):
        v = self.issue()
        self.clock[0] = at(1)
        changed = binding(); changed["request_sha256"] = sha("f")
        with self.assertRaises(ChallengePilotError):
            self.pilot.transition(challenge_id=v["challenge_id"], bindings=changed,
                                  expected_revision=1, action="SPEND_SIMULATION")

    def test_wrong_registry_head_refused(self):
        v = self.issue(); self.clock[0] = at(1)
        changed = binding(); changed["expected_registry_head_sha256"] = sha("f")
        with self.assertRaises(ChallengePilotError):
            self.pilot.transition(challenge_id=v["challenge_id"], bindings=changed,
                                  expected_revision=1, action="SPEND_SIMULATION")

    def test_changed_candidate_refused(self):
        v = self.issue(); self.clock[0] = at(1)
        changed = binding(); changed["validated_lifecycle_head_sha256"] = sha("f")
        with self.assertRaises(ChallengePilotError):
            self.pilot.transition(challenge_id=v["challenge_id"], bindings=changed,
                                  expected_revision=1, action="SPEND_SIMULATION")

    def test_wrong_skill_and_version_refused(self):
        v = self.issue(); self.clock[0] = at(1)
        for key, value in (("target_skill_id", "knowledge-forge:other@"+sha("b")),
                           ("target_skill_version", 2)):
            changed = binding(); changed[key] = value
            with self.subTest(key=key), self.assertRaises(ChallengePilotError):
                self.pilot.transition(challenge_id=v["challenge_id"], bindings=changed,
                                      expected_revision=1, action="SPEND_SIMULATION")

    def test_binding_injection_refused(self):
        changed = binding(); changed["verified_by_host"] = True
        with self.assertRaises(ChallengePilotError):
            self.pilot.issue(bindings=changed, request_expires_at=iso(at(600)))

    def test_invalid_digest_and_version_refused(self):
        for key, value in (("request_sha256", "x"*64), ("target_skill_version", True),
                           ("target_skill_id", "no-skill")):
            changed = binding(); changed[key] = value
            with self.subTest(key=key), self.assertRaises(ChallengePilotError):
                self.pilot.issue(bindings=changed, request_expires_at=iso(at(600)))

    def test_unsupported_action_refused(self):
        v = self.issue()
        with self.assertRaises(ChallengePilotError):
            self.pilot.transition(challenge_id=v["challenge_id"], bindings=binding(),
                                  expected_revision=1, action="APPROVE")

    def test_noninteger_revision_refused(self):
        v = self.issue()
        for number in (True, 1.0, 2, "1"):
            with self.subTest(number=number), self.assertRaises(ChallengePilotError):
                self.pilot.transition(challenge_id=v["challenge_id"], bindings=binding(),
                                      expected_revision=number, action="SPEND_SIMULATION")

    def test_bad_challenge_id_refused(self):
        for bad in ("abc", "g"*64, "../evil", None):
            with self.subTest(bad=bad), self.assertRaises(ChallengePilotError):
                self.pilot.inspect(bad)

    def test_inspect_unknown_id_no_db_created(self):
        with self.assertRaises(ChallengePilotError):
            self.pilot.inspect("a"*64)
        self.assertFalse(self.path.exists())

    def test_bad_utc_clock_injection_refused(self):
        self.clock[0] = datetime(2026, 10, 14, 18, 20)  # naive
        with self.assertRaises(ChallengePilotError):
            self.issue()

    def test_expiry_string_noncanonical_refused(self):
        for stamp in ("2026-10-14T18:29Z", "2026-10-14T18:29:00+00:00", "not-a-time"):
            with self.subTest(stamp=stamp), self.assertRaises(ChallengePilotError):
                self.pilot.issue(bindings=binding(), request_expires_at=stamp)

    def test_clock_rollback_refused_for_transition(self):
        v = self.issue(); self.clock[0] = at(-1)
        with self.assertRaises(ChallengePilotError):
            self.step(v)

    def test_restart_persists_spent_state(self):
        v = self.issue(); self.clock[0] = at(1)
        consumed = self.step(v)
        another = HostChallengePilot(self.path, test_clock=lambda: at(2))
        self.assertEqual(another.inspect(v["challenge_id"]), consumed)
        with self.assertRaises(ChallengePilotError):
            another.transition(challenge_id=v["challenge_id"], bindings=binding(),
                               expected_revision=1, action="SPEND_SIMULATION")

    def test_stored_binding_tamper_refused(self):
        v = self.issue()
        with closing(sqlite3.connect(self.path)) as con:
            con.execute("UPDATE simulated_challenges SET expected_registry_head_sha256=?", (sha("f"),))
            con.commit()
        with self.assertRaises(ChallengePilotError):
            self.pilot.inspect(v["challenge_id"])

    def test_stored_event_tamper_refused(self):
        v = self.issue()
        with closing(sqlite3.connect(self.path)) as con:
            con.execute("UPDATE simulated_challenge_events SET action='APPROVE'")
            con.commit()
        with self.assertRaises(ChallengePilotError):
            self.pilot.inspect(v["challenge_id"])

    def test_stored_expiry_tamper_refused(self):
        v = self.issue()
        with closing(sqlite3.connect(self.path)) as con:
            con.execute("UPDATE simulated_challenges SET expires_at=?", (iso(at(550)),))
            con.commit()
        with self.assertRaises(ChallengePilotError):
            self.pilot.inspect(v["challenge_id"])

    def test_symlink_db_refused(self):
        target = Path(self.temp.name) / "target.sqlite3"
        try:
            self.path.symlink_to(target)
        except (OSError, NotImplementedError):
            self.skipTest("host cannot create symlinks")
        with self.assertRaises(ChallengePilotError):
            HostChallengePilot(self.path)

    def test_symlink_parent_refused(self):
        linked = Path(self.temp.name) / "link"
        try:
            linked.symlink_to(self.temp.name, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("host cannot create symlinks")
        with self.assertRaises(ChallengePilotError):
            HostChallengePilot(linked / "demo.sqlite3")

    def test_system_and_relative_paths_refused(self):
        for name in ("local.sqlite3", "/var/lib/toadaid-kf-operator/test.sqlite3",
                     "/opt/toadaid-kf-operator/test.sqlite3", "/root/test.sqlite3"):
            with self.subTest(name=name), self.assertRaises(ChallengePilotError):
                HostChallengePilot(name)

    def test_duplicate_process_spend_exactly_one_winner(self):
        v = self.issue()
        # Separate actual processes on one SQLite file, not two in-memory snapshots.
        src = str(Path(__file__).resolve().parents[1] / "src")
        program = '''import sys
from datetime import datetime,timezone
sys.path.insert(0,sys.argv[1])
from knowledge_forge.host_challenge_pilot import HostChallengePilot,ChallengePilotError
p=HostChallengePilot(sys.argv[2],test_clock=lambda: datetime(2026,10,14,18,20,10,tzinfo=timezone.utc))
print("READY",flush=True)
sys.stdin.readline()
b={"request_sha256":"sha256:"+"a"*64,"target_skill_id":"knowledge-forge:roof.quote@sha256:"+"b"*64,"target_skill_version":1,"package_receipt_sha256":"sha256:"+"c"*64,"validated_lifecycle_head_sha256":"sha256:"+"d"*64,"expected_registry_head_sha256":"sha256:"+"e"*64}
try:
 p.transition(challenge_id=sys.argv[3],bindings=b,expected_revision=1,action="SPEND_SIMULATION")
 print("SPENT",flush=True)
except ChallengePilotError:
 print("REFUSED",flush=True)
'''
        args = [sys.executable, "-c", program, src, str(self.path), v["challenge_id"]]
        children = [subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, text=True) for _ in range(2)]
        try:
            for proc in children:
                self.assertEqual(proc.stdout.readline().strip(), "READY")
            for proc in children:
                proc.stdin.write("GO\n"); proc.stdin.flush()
            outcome = []
            for proc in children:
                stdout, stderr = proc.communicate(timeout=15)
                self.assertEqual(proc.returncode, 0, stderr)
                outcome.append(stdout.strip())
            self.assertCountEqual(outcome, ["SPENT", "REFUSED"])
            self.assertEqual(self.pilot.inspect(v["challenge_id"])["state"], "SPENT_SIMULATION")
        finally:
            for proc in children:
                if proc.poll() is None:
                    proc.kill(); proc.communicate()

    def test_crash_after_commit_is_replay_refused(self):
        v = self.issue()
        src = str(Path(__file__).resolve().parents[1] / "src")
        program = '''import os,sys
from datetime import datetime,timezone
sys.path.insert(0,sys.argv[1])
from knowledge_forge.host_challenge_pilot import HostChallengePilot
b={"request_sha256":"sha256:"+"a"*64,"target_skill_id":"knowledge-forge:roof.quote@sha256:"+"b"*64,"target_skill_version":1,"package_receipt_sha256":"sha256:"+"c"*64,"validated_lifecycle_head_sha256":"sha256:"+"d"*64,"expected_registry_head_sha256":"sha256:"+"e"*64}
p=HostChallengePilot(sys.argv[2],test_clock=lambda: datetime(2026,10,14,18,20,10,tzinfo=timezone.utc))
p.transition(challenge_id=sys.argv[3],bindings=b,expected_revision=1,action="SPEND_SIMULATION")
os._exit(23)
'''
        child = subprocess.run([sys.executable, "-c", program, src, str(self.path), v["challenge_id"]],
                               capture_output=True, text=True, timeout=15)
        self.assertEqual(child.returncode, 23, child.stderr)
        self.clock[0] = at(20)
        with self.assertRaises(ChallengePilotError):
            self.step(v)
        self.assertEqual(self.pilot.inspect(v["challenge_id"])["state"], "SPENT_SIMULATION")

    def test_no_privileged_or_operator_imports(self):
        path = Path(__file__).resolve().parents[1] / "src" / "knowledge_forge" / "host_challenge_pilot.py"
        names = set()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                names.update(x.name.split(".")[0] for x in node.names)
            if isinstance(node, ast.ImportFrom) and node.module:
                names.add(node.module.split(".")[0])
        self.assertFalse(names & {"operator_cli", "operator_promotion", "subprocess", "os", "pwd", "grp", "ctypes", "socket", "winreg"})

if __name__ == "__main__":
    unittest.main()
