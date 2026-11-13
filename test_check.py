import importlib.util, json, os, subprocess, sys, tempfile, unittest
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
CHECK = os.path.join(HERE, "check.py")
spec = importlib.util.spec_from_file_location("check", CHECK)
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)

T0 = datetime(2026, 11, 12, 16, 17, tzinfo=timezone.utc)
DOWN, UPW = "Service degradation", "Operational"


def at(minutes, base=T0):
    return base + timedelta(minutes=minutes)


def idle(**kw):
    s = {"v": 2, "checked": c.iso(T0), "status": UPW, "tracking_since": "2026-11-12",
         "current": None, "incidents": [], "ups": 0, "downs": 0,
         "up_since": None, "down_since": None,
         "log": [{"t": c.iso(T0), "status": UPW}]}
    s.update(kw)
    return s


def cur(start=T0, **kw):
    return idle(current={"id": start.strftime("%Y-%m-%d"), "start": c.iso(start)}, status=DOWN, **kw)


def run(state, seq, base=T0, step_min=10):
    """Feed (word) list at base, base+step, ... Return final state."""
    for i, w in enumerate(seq):
        state, _ = c.step(state, w, at(i * step_min, base))
    return state


class Rules(unittest.TestCase):
    def test_down_stays_down(self):
        s = run(cur(), [DOWN] * 5, at(10))
        self.assertEqual(s["current"]["start"], c.iso(T0))
        self.assertEqual(s["incidents"], [])

    def test_one_up_blip_does_not_close(self):
        s = run(cur(), [UPW, DOWN, DOWN], at(10))
        self.assertIsNotNone(s["current"])
        self.assertEqual(s["incidents"], [])
        self.assertEqual(s["ups"], 0)

    def test_two_ups_close(self):
        s = run(cur(), [DOWN, UPW, "Service restored"], at(60))
        self.assertIsNone(s["current"])
        i = s["incidents"][0]
        self.assertEqual(i["end"], c.iso(at(70)))
        self.assertEqual(i["start"], c.iso(T0))
        self.assertEqual(i["hours"], 1.2)
        self.assertIs(i["made364"], False)
        self.assertEqual(i["id"], "2026-11-12")

    def test_25_hour_incident_made364(self):
        s = run(cur(), [UPW, UPW], at(25 * 60))
        i = s["incidents"][0]
        self.assertEqual(i["hours"], 25.0)
        self.assertIs(i["made364"], True)

    def test_exactly_24h_counts(self):
        s = run(cur(), [UPW, UPW], at(24 * 60))
        self.assertIs(s["incidents"][0]["made364"], True)

    def test_up_words_case_insensitive(self):
        for w in ["OPERATIONAL", "False Positive", "Post-incident report published"]:
            s = run(cur(), [w, w], at(60))
            self.assertIsNone(s["current"], w)

    def test_idle_stays_idle(self):
        s = run(idle(), [UPW] * 4, at(5))
        self.assertIsNone(s["current"])
        self.assertEqual(s["incidents"], [])

    def test_one_problem_blip_does_not_open(self):
        s = run(idle(), [DOWN, UPW, DOWN, UPW], at(5))
        self.assertIsNone(s["current"])

    def test_two_problems_open(self):
        s = run(idle(), [UPW, DOWN, DOWN], at(5))
        self.assertEqual(s["current"], {"id": "2026-11-12", "start": c.iso(at(15))})

    def test_second_incident_same_date_gets_suffix(self):
        s = idle(incidents=[{"id": "2026-11-12", "start": c.iso(T0), "end": c.iso(at(30)),
                             "hours": 0.5, "made364": False}])
        s = run(s, [DOWN, DOWN], at(300))
        self.assertEqual(s["current"]["id"], "2026-11-12-2")
        s["incidents"].insert(0, {"id": "2026-11-12-2", "start": "x", "end": c.iso(at(1)), "hours": 0, "made364": False})
        self.assertEqual(c._new_id(dict(s, current=None), "2026-11-12"), "2026-11-12-3")

    def test_reopen_within_60_minutes(self):
        s = run(cur(), [UPW, UPW], at(600))      # closes, end = T0+600
        end = s["incidents"][0]["end"]
        self.assertEqual(end, c.iso(at(600)))
        s = run(s, [DOWN, DOWN], at(630))         # problem starts 30 min after end
        self.assertEqual(s["current"], {"id": "2026-11-12", "start": c.iso(T0)})
        self.assertEqual(s["incidents"], [])

    def test_new_incident_after_90_minutes(self):
        s = run(cur(), [UPW, UPW], at(60))        # end = T0+60
        s = run(s, [DOWN, DOWN], at(150))          # problem starts 90 min after end
        self.assertEqual(s["current"]["start"], c.iso(at(150)))
        self.assertEqual(s["current"]["id"], "2026-11-12-2")
        self.assertEqual(len(s["incidents"]), 1)

    def test_unknown_word_is_problem(self):
        s = run(idle(), ["Something new", "Something new"], at(5))
        self.assertIsNotNone(s["current"])

    def test_empty_word_raises(self):
        with self.assertRaises(ValueError):
            c.step(idle(), "  ", T0)

    def test_status_truncated(self):
        s, _ = c.step(idle(), "x" * 100, T0)
        self.assertEqual(len(s["status"]), 60)

    def test_step_is_pure(self):
        s = cur()
        before = json.dumps(s, sort_keys=True)
        c.step(s, UPW, at(10))
        self.assertEqual(json.dumps(s, sort_keys=True), before)


class Migration(unittest.TestCase):
    def test_v1_open(self):
        v1 = {"start": "2026-11-12T16:17:00Z", "end": None, "checked": "2026-11-13T03:35:30Z",
              "status": DOWN, "ups": 0, "log": [{"t": "2026-11-13T03:35:30Z", "status": DOWN}]}
        s, _ = c.step(v1, DOWN, at(700))
        self.assertEqual(s["v"], 2)
        self.assertEqual(s["current"], {"id": "2026-11-12", "start": "2026-11-12T16:17:00Z"})
        self.assertEqual(s["tracking_since"], "2026-11-12")
        self.assertEqual(s["log"], v1["log"])
        self.assertNotIn("start", s)

    def test_v1_one_up_in_progress_then_close(self):
        v1 = {"start": "2026-11-12T16:17:00Z", "end": None, "checked": c.iso(T0), "status": UPW,
              "ups": 1, "up_since": c.iso(at(700)), "log": []}
        s, _ = c.step(v1, UPW, at(710))
        self.assertIsNone(s["current"])
        self.assertEqual(s["incidents"][0]["end"], c.iso(at(700)))

    def test_v1_closed(self):
        v1 = {"start": "2026-11-12T16:17:00Z", "end": "2026-11-13T18:00:00Z", "checked": c.iso(T0),
              "status": UPW, "ups": 2, "log": []}
        s, _ = c.step(v1, UPW, at(3000))
        self.assertIsNone(s["current"])
        self.assertEqual(s["incidents"][0]["hours"], 25.7)
        self.assertIs(s["incidents"][0]["made364"], True)

    def test_shipped_status_json_is_valid_v2(self):
        with open(os.path.join(HERE, "status.json")) as f:
            s = json.load(f)
        self.assertEqual(s["v"], 2)
        self.assertEqual(s["current"], {"id": "2026-11-12", "start": "2026-11-12T16:17:00Z"})
        self.assertEqual(s["incidents"], [])


class WritePolicy(unittest.TestCase):
    def test_idle_fresh_no_write(self):
        s = idle(checked=c.iso(T0))
        new, changed = c.step(s, UPW, at(10))
        self.assertFalse(changed)
        self.assertEqual(new["checked"], c.iso(T0))

    def test_idle_55_min_boundary_no_write(self):
        _, changed = c.step(idle(checked=c.iso(T0)), UPW, at(55))
        self.assertFalse(changed)

    def test_idle_56_min_writes(self):
        new, changed = c.step(idle(checked=c.iso(T0)), UPW, at(56))
        self.assertTrue(changed)
        self.assertEqual(new["checked"], c.iso(at(56)))

    def test_current_always_writes(self):
        s = cur(checked=c.iso(T0))
        new, changed = c.step(s, DOWN, at(1))
        self.assertTrue(changed)
        self.assertEqual(new["checked"], c.iso(at(1)))

    def test_other_change_writes(self):
        _, changed = c.step(idle(checked=c.iso(T0)), DOWN, at(1))
        self.assertTrue(changed)

    def test_log_status_changes_only(self):
        s = run(idle(), [UPW, UPW, DOWN, DOWN, DOWN, UPW], T0)
        self.assertEqual([e["status"] for e in s["log"]], [UPW, DOWN, UPW])

    def test_log_capped_at_200(self):
        s = idle()
        for i in range(250):
            s, _ = c.step(s, "W%d" % i, at(i))
        self.assertEqual(len(s["log"]), 200)
        self.assertEqual(s["log"][-1]["status"], "W249")
        self.assertEqual(s["log"][0]["status"], "W50")


def feed(status="Service degradation"):
    return [{"ServiceWorkloadName": "Other", "Status": "Operational"},
            {"ServiceWorkloadName": "M365Consumer", "Status": status}]


class EndToEnd(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.d, ignore_errors=True))

    def put(self, name, obj, raw=None):
        p = os.path.join(self.d, name)
        with open(p, "w") as f:
            f.write(raw if raw is not None else json.dumps(obj))
        return p

    def run_check(self, feedpath, *extra):
        return subprocess.run([sys.executable, "-I", CHECK, "--feed-file", feedpath, *extra],
                              cwd=self.d, capture_output=True, text=True)

    def read(self):
        with open(os.path.join(self.d, "status.json"), "rb") as f:
            return f.read()

    def test_feed_file_end_to_end(self):
        with open(os.path.join(HERE, "status.json")) as f:
            self.put("status.json", json.load(f))
        fp = self.put("feed.json", feed())
        r = self.run_check(fp)
        self.assertEqual(r.returncode, 0, r.stderr)
        s = json.loads(self.read())
        self.assertEqual(s["v"], 2)
        self.assertIsNotNone(s["current"])
        self.assertNotEqual(s["checked"], "2026-11-13T03:35:30Z")
        self.assertEqual([x for x in os.listdir(self.d) if x.endswith(".tmp")], [])

    def test_idle_prints_no_change(self):
        now = datetime.now(timezone.utc).replace(microsecond=0)
        self.put("status.json", idle(checked=c.iso(now), status=UPW))
        before = self.read()
        r = self.run_check(self.put("feed.json", feed(UPW)))
        self.assertEqual(r.returncode, 0)
        self.assertIn("no change", r.stdout)
        self.assertEqual(self.read(), before)

    def bad(self, obj=None, raw=None):
        self.put("status.json", cur())
        before = self.read()
        r = self.run_check(self.put("feed.json", obj, raw))
        self.assertEqual(r.returncode, 1)
        self.assertTrue(r.stderr.strip())
        self.assertEqual(self.read(), before)

    def test_row_missing(self):
        self.bad([{"ServiceWorkloadName": "Other", "Status": "Operational"}])

    def test_empty_status(self):
        self.bad(feed("  "))

    def test_not_a_list(self):
        self.bad({"ServiceWorkloadName": "M365Consumer", "Status": "x"})

    def test_bad_json(self):
        self.bad(raw="{not json")

    def test_unreadable_status_json(self):
        self.put("status.json", None, raw="garbage")
        before = self.read()
        r = self.run_check(self.put("feed.json", feed()))
        self.assertEqual(r.returncode, 1)
        self.assertEqual(self.read(), before)

    def test_read_status_word_raises(self):
        for bad in ([], {}, [{"ServiceWorkloadName": "M365Consumer", "Status": 5}]):
            with self.assertRaises(ValueError):
                c.read_status_word(bad)


if __name__ == "__main__":
    unittest.main()
