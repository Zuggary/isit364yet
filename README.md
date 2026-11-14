# Is it 364 yet?

A parody site that tracks Microsoft 365 outages. Not affiliated with Microsoft.

- `index.html` is the whole site. No cookies and no ads. One outside script: Cloudflare Web Analytics, a cookie-free visit counter. It also totals anonymous step counts (credit link, side, boost, postcard, game, ad name) by swapping the address to a fixed `/step/<name>` path and straight back with `history.replaceState`, live host only; names come from the `STEPS` whitelist in `index.html`, never from anything typed.
- `past/index.html` lists every closed outage from `status.json`, newest first (its built-in entry shows if the file does not load). The home page links it as "See past outages".
- `check.py` reads Microsoft's public status feed every 10 minutes and writes `status.json`.
- An outage opens after 2 problem readings in a row and closes after 2 clear readings in a row. Closed outages stay in the archive.
- `test_check.py` and `test_review.py` test the scorekeeper with no network: `python3 -I test_check.py`.
- `v1.html` is the first version of the page.
