# Is it 364 yet?

A parody countdown for the Microsoft 365 outage of 12 November 2026. Not affiliated with Microsoft.

- `index.html` is the whole site. No tracking, no cookies, no outside scripts.
- `check.py` reads Microsoft's public status feed every 10 minutes (GitHub Actions) and writes `status.json`.
- The outage counts as over when the Microsoft 365 row on status.cloud.microsoft stops showing a problem on 2 checks in a row.
