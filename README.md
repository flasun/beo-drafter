# Synthetic BEO drafter (Hacksgiving local prep)

A small, offline-first agent that turns a fictional banquet brief (JSON) into a Banquet Event Order in Markdown and JSON. This is local prep for a Fly.io Hacksgiving entry. It is not the author's separate product, does not use that repo, and ships only synthetic sample events.

Default path: a deterministic mock model client. No network and no paid model calls. An Ollama Cloud client is wired for the Sprites connector gateway; tests inject a fake transport and never open a network socket. Do not set `OLLAMA_API_KEY` on a Sprite.

## Run locally

Python 3.11+ (developed against 3.13). Standard library only; no package install. From the project root:

```bash
python3 -m unittest discover -s tests -v
python3 -m beo_drafter draft fixtures/wedding.json --out out
python3 -m beo_drafter draft fixtures/gala.json --out out --approve --receipts out/receipts.jsonl
python3 -m beo_drafter verify-receipts out/receipts.jsonl out/gala.json
python3 -m beo_drafter.server          # http://127.0.0.1:8080  (binds localhost only)
python3 -m beo_drafter.server --bind 0.0.0.0   # required on a Sprite so the URL can reach port 8080
```

Unapproved Markdown carries an **UNAPPROVED DRAFT** banner and `overview.status: draft`. `--approve` (which requires `--receipts`) sets `overview.status` to `approved` and stamps `overview.approved_at` (UTC); it does not invent a signer name. It then writes an **APPROVED** banner and hashes that BEO into the receipt log. A sibling `receipts.jsonl.anchor` file stores the tip hash and receipt count; `verify-receipts` checks both the chain and the anchor.

The receipt log is tamper-evident mid-chain and detects tail truncation when the anchor is intact. It is not a signature with a real key: anyone who can rewrite both the log and the anchor can still produce a chain that verifies.

On a Sprite, set `OLLAMA_CONNECTION_ID` and `OLLAMA_MODEL`. The app calls `https://api.sprites.dev/v1/gateway/ollama/<connection_id>/api/chat` with no Authorization header; the gateway attaches the key. Do not set `OLLAMA_API_KEY` on the Sprite, and do not put a real key in this repo. The mock client is still the default and ignores those variables.

The HTTP demo can paste a brief JSON object or a short labeled event email onto a fixture, optionally override the guarantee (for example 120 to 180) and a vegan count, and shows guesses (computed staffing and defaults) in red with a source on hover. CLI Markdown marks the same guesses with `[guess]`.

A PowerShell deploy pack for the operator's machine is in `../DEPLOY.md` and `DEPLOY.md`. The demo server binds `127.0.0.1` unless you pass `--bind 0.0.0.0` or set `BEO_BIND`. The HTTP form and `/draft` are mock-only unless `DEMO_ALLOW_OLLAMA=1`. Do not set that variable on a public Sprite URL. The `--approve` path is not offered over HTTP.

## Operator steps still outstanding

Agents must not read, log, or handle the key, and must not log in to Fly or Sprites from this environment. Follow `DEPLOY.md`. Nothing goes public under the operator's name until the operator decides to proceed.

1. Done in this tree: `OllamaCloudClient` talks to the Sprites connector gateway with a connection ID only. Mock remains the default.
2. TODO: Run the PowerShell pack in `DEPLOY.md` on the operator's machine, in the personal org (not a work org). The operator pastes the ollama.com key into the dashboard connector.
3. TODO: Create a fresh public repo for the submission. Do not open or copy the author's separate product. A public Sprite URL is not a GitHub publish.
4. TODO: Post a short demo tagging @flydotio (X, Bluesky, LinkedIn, or Reddit) and submit the code link, demo, and description before midnight PST Sun Nov 29 (2:00 AM CST Mon Nov 30).

## Entry description draft

Synthetic BEO Drafter is a Fly.io Sprite agent that turns a fictional banquet brief into a Banquet Event Order, calling Ollama Cloud through a Sprites connector so the model key never sits on the host. An optional `--approve` step writes a hash-chained receipt before the plan is treated as released.

## License and data

Eleven synthetic fixtures (the original five, patched in place, plus cocktail, day-meeting, awards-buffet, conference days 1 and 2, and brunch). Fictional venues, clients, and counts. No real guest data and no current-employer or Disney material.
