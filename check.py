#!/usr/bin/env python3
"""Scorekeeper v2 for isit364yet.com.

Reads Microsoft's public status feed and keeps status.json (version 2):
one current incident, a list of closed ones. Rules are in CHECKER-NOTES.md.
On any error this script changes nothing, prints the reason to stderr and exits 1.
"""
import copy, json, os, sys, tempfile, urllib.request
from datetime import datetime, timedelta, timezone

FEED = "https://status.cloud.microsoft/api/posts/m365Consumer"
ROW = "M365Consumer"
UP = {"operational", "service restored", "false positive", "post-incident report published"}
PATH = "status.json"
FMT = "%Y-%m-%dT%H:%M:%SZ"
CONFIRM = 2              # checks in a row to open or close
FLAP = timedelta(minutes=60)
STALE = timedelta(minutes=55)
LOG_MAX = 200


def iso(t):
    return t.astimezone(timezone.utc).strftime(FMT)


def parse(s):
    return datetime.strptime(s, FMT).replace(tzinfo=timezone.utc)


def norm(word):
    """Collapse inner whitespace, strip, cap at 60 characters."""
    return " ".join(str(word).split())[:60]


def _fill(s):
    """Repair a version 2 state with missing or null optional fields."""
    for k, v in (("current", None), ("incidents", []), ("log", []), ("ups", 0),
                 ("downs", 0), ("up_since", None), ("down_since", None)):
        if s.get(k) is None:
            s[k] = v
    s["ups"] = int(s["ups"] or 0)
    s["downs"] = int(s["downs"] or 0)
    if s.get("status") is None:
        s["status"] = ""
    return s


def migrate(state):
    """Return a version 2 copy of state. Accepts version 1 (top-level start/end)."""
    s = copy.deepcopy(state)
    if s.get("v") == 2:
        return _fill(s)
    if "start" not in s:
        raise ValueError("status.json is neither version 1 nor version 2")
    start, end = s.get("start"), s.get("end")
    new = {
        "v": 2,
        "checked": s.get("checked"),
        "status": s.get("status", ""),
        "tracking_since": start[:10],
        "current": None,
        "incidents": [],
        "ups": int(s.get("ups") or 0),
        "downs": 0,
        "up_since": s.get("up_since"),
        "down_since": None,
        "log": s.get("log") or [],
    }
    if end:
        hours = round((parse(end) - parse(start)).total_seconds() / 3600, 1)
        new["incidents"].append({"id": start[:10], "start": start, "end": end,
                                 "hours": hours, "made364": (parse(end) - parse(start)) >= timedelta(hours=24)})
        new["ups"], new["up_since"] = 0, None
    else:
        new["current"] = {"id": start[:10], "start": start}
    return new


def _comparable(s):
    d = dict(s)
    d.pop("checked", None)
    return d


def _new_id(s, day):
    taken = {i["id"] for i in s["incidents"]}
    if s["current"]:
        taken.add(s["current"]["id"])
    if day not in taken:
        return day
    n = 2
    while "%s-%d" % (day, n) in taken:
        n += 1
    return "%s-%d" % (day, n)


def step(state, status_word, now):
    """Pure core. Returns (new_state, changed). changed = the file should be written."""
    if not isinstance(status_word, str) or not status_word.strip():
        raise ValueError("empty status")
    word = norm(status_word)
    old = migrate(state)
    s = copy.deepcopy(old)
    t = iso(now)
    up = word.lower() in UP

    same = str(old.get("status") or "").lower() == word.lower()
    if not same:
        s["status"] = word
    log = s["log"]
    if not log or str(log[-1].get("status") or "").lower() != word.lower():
        log.append({"t": t, "status": word})
    del log[:-LOG_MAX]

    if s["current"]:
        s["downs"], s["down_since"] = 0, None
        if up:
            s["ups"] = int(s.get("ups") or 0) + 1
            s["up_since"] = s.get("up_since") or t
            if s["ups"] >= CONFIRM:
                start, end = s["current"]["start"], s["up_since"]
                hours = round((parse(end) - parse(start)).total_seconds() / 3600, 1)
                s["incidents"].insert(0, {"id": s["current"]["id"], "start": start, "end": end,
                                          "hours": hours,
                                          "made364": (parse(end) - parse(start)) >= timedelta(hours=24)})
                s["current"] = None
                s["ups"], s["up_since"] = 0, None
        else:
            s["ups"], s["up_since"] = 0, None
    else:
        s["ups"], s["up_since"] = 0, None
        if up:
            s["downs"], s["down_since"] = 0, None
        else:
            s["downs"] = int(s.get("downs") or 0) + 1
            s["down_since"] = s.get("down_since") or t
            if s["downs"] >= CONFIRM:
                start = s["down_since"]
                last = s["incidents"][0] if s["incidents"] else None
                if last and parse(start) - parse(last["end"]) <= FLAP:
                    s["incidents"].pop(0)
                    s["current"] = {"id": last["id"], "start": last["start"]}
                else:
                    s["current"] = {"id": _new_id(s, start[:10]), "start": start}
                s["downs"], s["down_since"] = 0, None

    if _comparable(s) != _comparable(old):
        s["checked"] = t
        return s, True
    # only "checked" would change
    stored = old.get("checked")
    try:
        stale = now - parse(stored) > STALE
    except (TypeError, ValueError):
        stale = True
    if s["current"] or stale:
        s["checked"] = t
        return s, True
    return old, False


def read_status_word(feed):
    if not isinstance(feed, list):
        raise ValueError("feed is not a list")
    rows = [x for x in feed if isinstance(x, dict) and x.get("ServiceWorkloadName") == ROW]
    if len(rows) != 1:
        raise ValueError("feed has %d %s rows, expected 1" % (len(rows), ROW))
    st = rows[0].get("Status")
    if not isinstance(st, str) or not st.strip():
        raise ValueError("%s row has empty or missing Status" % ROW)
    return norm(st)


def write_atomic(path, data):
    d = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".status-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=1)
            f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    feed_file = None
    if argv[:1] == ["--feed-file"] and len(argv) == 2:
        feed_file = argv[1]
    elif argv:
        raise ValueError("usage: check.py [--feed-file PATH]")
    if feed_file:
        with open(feed_file) as f:
            feed = json.load(f)
    else:
        req = urllib.request.Request(FEED, headers={"User-Agent": "isit364yet-scorekeeper"})
        with urllib.request.urlopen(req, timeout=20) as r:
            feed = json.load(r)
    word = read_status_word(feed)
    with open(PATH) as f:
        state = json.load(f)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    new, changed = step(state, word, now)
    if changed:
        write_atomic(PATH, new)
        print(iso(now), word, "current:", new["current"], "closed:", len(new["incidents"]), "written")
    else:
        print("no change")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # includes SystemExit from urllib? no: only real errors
        print("check failed, nothing changed:", e, file=sys.stderr)
        sys.exit(1)
