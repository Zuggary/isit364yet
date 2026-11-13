#!/usr/bin/env python3
"""Scorekeeper for isit364yet.com.

Reads Microsoft's public status feed and writes status.json.
Rule: the outage is over when the Microsoft 365 row on status.cloud.microsoft
stops showing a problem on 2 checks in a row. The first all-clear ends it.
On any error this script changes nothing.
"""
import json, sys, urllib.request
from datetime import datetime, timezone

FEED = "https://status.cloud.microsoft/api/posts/m365Consumer"
ROW = "M365Consumer"
UP = {"operational", "service restored", "false positive", "post-incident report published"}
PATH = "status.json"

def main():
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    req = urllib.request.Request(FEED, headers={"User-Agent": "isit364yet-scorekeeper"})
    with urllib.request.urlopen(req, timeout=20) as r:
        feed = json.load(r)
    rows = [x for x in feed if x.get("ServiceWorkloadName") == ROW]
    if len(rows) != 1 or not isinstance(rows[0].get("Status"), str) or not rows[0]["Status"].strip():
        raise SystemExit("feed has no usable %s row; nothing changed" % ROW)
    status = rows[0]["Status"].strip()[:60]

    with open(PATH) as f:
        s = json.load(f)
    s["checked"] = now
    s["status"] = status
    log = s.setdefault("log", [])
    if not log or log[-1]["status"] != status:
        log.append({"t": now, "status": status})
    if not s.get("end"):
        if status.lower() in UP:
            s["ups"] = int(s.get("ups") or 0) + 1
            s.setdefault("up_since", now)
            if s["ups"] >= 2:
                s["end"] = s["up_since"]
        else:
            s["ups"] = 0
            s.pop("up_since", None)
    with open(PATH, "w") as f:
        json.dump(s, f, indent=1)
        f.write("\n")
    print(now, status, "end:", s.get("end"))

if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        print("check failed, nothing changed:", e, file=sys.stderr)
        sys.exit(1)
