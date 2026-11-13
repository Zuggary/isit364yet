"""Independent review tests for check.py. Standard library only, no network."""
import importlib.util, json, os, subprocess, sys, tempfile, unittest
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
CHECK = os.path.join(HERE, "check.py")
spec = importlib.util.spec_from_file_location("check_r", CHECK)
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)

T0 = datetime(2026, 11, 12, 16, 17, tzinfo=timezone.utc)
DOWN, UPW = "Service degradation", "Operational"


def at(m, base=T0):
    return base + timedelta(minutes=m)


def idle(**kw):
    s = {"v": 2, "checked": c.iso(T0), "status": UPW, "tracking_since": "2026-11-12",
         "current": None, "incidents": [], "ups": 0, "downs": 0,
         "up_since": None, "down_since": None, "log": []}
    s.update(kw)
    return s


def run(state, seq, base=T0, step=10):
    for i, w in enumerate(seq):
        state, _ = c.step(state, w, at(i * step, base))
    return state


class Review(unittest.TestCase):
    def test_flap_up_problem_up_does_not_close(self):
        s = run(idle(current={"id": "2026-11-12", "start": c.iso(T0)}),
                [UPW, DOWN, UPW])
        self.assertIsNotNone(s["current"])
        self.assertEqual(s["ups"], 1)
        self.assertEqual(s["up_since"], c.iso(at(20)))  # first of the new pair

    def test_reopen_then_quick_close_keeps_original_start(self):
        base = datetime(2026, 11, 1, 0, 0, tzinfo=timezone.utc)
        s = run(idle(), [DOWN, DOWN, UPW, UPW, DOWN, DOWN, UPW, UPW], base)
        self.assertEqual(len(s["incidents"]), 1)
        i = s["incidents"][0]
        self.assertEqual(i["start"], c.iso(base))
        self.assertEqual(i["end"], c.iso(at(60, base)))
        self.assertIsNone(s["current"])

    def test_year_boundary(self):
        base = datetime(2026, 12, 31, 23, 50, tzinfo=timezone.utc)
        s = run(idle(), [DOWN, DOWN], base)
        self.assertEqual(s["current"]["id"], "2026-12-31")
        s = run(s, [UPW, UPW], at(60 * 24, base))
        i = s["incidents"][0]
        self.assertEqual(i["id"], "2026-12-31")
        self.assertTrue(i["end"].startswith("2027-01-01"))
        self.assertTrue(i["made364"])

    def test_non_utc_aware_now_is_converted(self):
        la = timezone(timedelta(hours=-8))
        now = datetime(2026, 11, 12, 8, 17, tzinfo=la)  # 16:17Z
        s, _ = c.step(idle(), DOWN, now)
        self.assertEqual(s["down_since"], "2026-11-12T16:17:00Z")

    def test_odd_case_and_whitespace(self):
        for w in ["  OPERATIONAL\n", "Service Restored", "FALSE POSITIVE", "\tPost-Incident Report Published "]:
            s, _ = c.step(idle(current={"id": "x", "start": c.iso(T0)}), w, at(10))
            self.assertEqual(s["ups"], 1, w)

    def test_internal_whitespace_runs_still_up(self):
        # real feeds can carry double spaces / non-breaking spaces
        s, _ = c.step(idle(current={"id": "x", "start": c.iso(T0)}), "Service  restored", at(10))
        self.assertEqual(s["ups"], 1)

    def test_nbsp_is_stripped(self):
        s, _ = c.step(idle(current={"id": "x", "start": c.iso(T0)}), " Operational ", at(10))
        self.assertEqual(s["ups"], 1)

    def test_v2_missing_optional_fields(self):
        # minimal v2 file: only the required structure fields a hand edit might drop
        st = {"v": 2, "checked": c.iso(T0), "status": UPW}
        s, ch = c.step(st, DOWN, at(10))
        self.assertTrue(ch)
        self.assertEqual(s["downs"], 1)

    def test_v2_log_null(self):
        st = idle(log=None)
        s, ch = c.step(st, DOWN, at(10))
        self.assertTrue(ch)

    def test_hours_rounding_23_96(self):
        start = T0
        end = T0 + timedelta(hours=23, minutes=57, seconds=36)  # 23.96 h
        st = idle(current={"id": "2026-11-12", "start": c.iso(start)}, ups=1, up_since=c.iso(end))
        s, _ = c.step(st, UPW, end + timedelta(minutes=10))
        i = s["incidents"][0]
        self.assertFalse(i["made364"])

    def test_migration_rounding_23_96(self):
        end = T0 + timedelta(hours=23, minutes=57, seconds=36)
        v1 = {"start": c.iso(T0), "end": c.iso(end), "status": UPW, "checked": c.iso(end)}
        m = c.migrate(v1)
        self.assertFalse(m["incidents"][0]["made364"])

    def test_exactly_24h_is_364(self):
        end = T0 + timedelta(hours=24)
        st = idle(current={"id": "2026-11-12", "start": c.iso(T0)}, ups=1, up_since=c.iso(end))
        s, _ = c.step(st, UPW, end + timedelta(minutes=10))
        self.assertTrue(s["incidents"][0]["made364"])

    def test_three_incidents_same_date_ids(self):
        base = datetime(2026, 11, 1, 0, 0, tzinfo=timezone.utc)
        s = idle()
        t = 0
        for _ in range(3):
            for w in [DOWN, DOWN, UPW, UPW] + [UPW] * 7:  # 70+ minutes quiet
                s, _ = c.step(s, w, at(t, base)); t += 10
        ids = [i["id"] for i in s["incidents"]]
        self.assertEqual(ids, ["2026-11-01-3", "2026-11-01-2", "2026-11-01"])

    def test_reopen_boundary_60_vs_61(self):
        base = datetime(2026, 11, 1, 0, 0, tzinfo=timezone.utc)
        s = run(idle(), [DOWN, DOWN, UPW, UPW], base)  # end at +20
        a = run(s, [DOWN, DOWN], at(20 + 60, base))
        self.assertEqual(len(a["incidents"]), 0)  # reopened
        b = run(s, [DOWN, DOWN], at(20 + 61, base))
        self.assertEqual(len(b["incidents"]), 1)

    def test_one_problem_blip_when_idle_opens_nothing(self):
        s = run(idle(), [DOWN, UPW, DOWN, UPW])
        self.assertIsNone(s["current"])
        self.assertEqual(s["downs"], 0)

    def test_empty_status_raises_and_no_change(self):
        st = idle()
        with self.assertRaises(ValueError):
            c.step(st, "   ", at(10))

    def test_input_not_mutated(self):
        st = idle(current={"id": "x", "start": c.iso(T0)})
        import copy
        before = copy.deepcopy(st)
        c.step(st, UPW, at(10))
        self.assertEqual(st, before)

    def test_atomic_write_same_dir_and_no_leftover(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "status.json")
            c.write_atomic(p, {"a": 1})
            self.assertEqual(os.listdir(d), ["status.json"])
            self.assertEqual(json.load(open(p)), {"a": 1})

    def test_atomic_write_failure_keeps_old_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "status.json")
            c.write_atomic(p, {"a": 1})
            with self.assertRaises(TypeError):
                c.write_atomic(p, {"a": object()})
            self.assertEqual(json.load(open(p)), {"a": 1})
            self.assertEqual(os.listdir(d), ["status.json"])

    def test_cli_error_changes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            sp = os.path.join(d, "status.json")
            json.dump(idle(), open(sp, "w"))
            before = open(sp).read()
            fp = os.path.join(d, "feed.json")
            json.dump([{"ServiceWorkloadName": "Other", "Status": "x"}], open(fp, "w"))
            r = subprocess.run([sys.executable, "-I", CHECK, "--feed-file", fp], cwd=d, capture_output=True, text=True)
            self.assertEqual(r.returncode, 1)
            self.assertEqual(open(sp).read(), before)

    def test_cli_duplicate_row_is_error(self):
        with tempfile.TemporaryDirectory() as d:
            json.dump(idle(), open(os.path.join(d, "status.json"), "w"))
            fp = os.path.join(d, "feed.json")
            row = {"ServiceWorkloadName": "M365Consumer", "Status": "Operational"}
            json.dump([row, row], open(fp, "w"))
            r = subprocess.run([sys.executable, "-I", CHECK, "--feed-file", fp], cwd=d, capture_output=True, text=True)
            self.assertEqual(r.returncode, 1)

    def test_v1_idle_file_not_migrated_on_quiet_check(self):
        # v1 file, no change in meaning: does the run persist the v2 shape?
        v1 = {"start": "2026-11-12T16:17:00Z", "end": "2026-11-12T20:00:00Z", "status": UPW,
              "checked": c.iso(T0)}
        s, ch = c.step(v1, UPW, at(5))
        self.assertTrue(ch, "v1 file is never rewritten as v2 until a real change")

    def test_log_unique_per_case_change(self):
        s = run(idle(), ["Operational", "operational"])
        self.assertEqual(len(s["log"]), 1, "case-only change should not log")


if __name__ == "__main__":
    unittest.main()
