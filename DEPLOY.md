# Deploy the synthetic BEO drafter to a Fly.io Sprite

Drafted 23 Nov 2026 from public Sprites docs. This is a copy-paste pack for Windows PowerShell 5.1. It does not create a public GitHub repo and does not post anything.

The operator pastes the Ollama key, and the Sprite goes in the operator's **personal** org. A public repo, a public post, and a **public Sprite URL** (step 7) are later, separate steps and are not part of this pack's default path.

Agents must not log in to Fly or Sprites from this environment, and must not see, read, log, or handle the Ollama key.

This pack assumes the `sprite` CLI is already installed and that you have already run `sprite login` in a browser. If a check below fails, use the fallback in that step. You do **not** need `flyctl` for a Sprite. flyctl's documented Windows install uses pwsh (PowerShell 7), which this machine may not have.

Sources: https://fly.io/sprites/agent-ready.md, https://docs.fly.io/sprites/quickstart, https://docs.fly.io/sprites/cli/installation, https://docs.fly.io/sprites/cli/authentication, https://docs.fly.io/sprites/cli/commands, https://docs.fly.io/sprites/concepts/services, https://docs.fly.io/sprites/working-with-sprites, https://fly.io/sprites/, https://fly.io/sprites/ollama/, https://docs.fly.io/sprites/concepts/connectors.

---

## 0. Put the project on this PC

Copy the `beo-drafter` project folder onto this PC first (not via a public git remote). Then, in PowerShell, go to it. Adjust the path if you put it somewhere else:

```powershell
cd $env:USERPROFILE\hacksgiving\beo-drafter
Get-ChildItem
```

Expected: you see `beo_drafter`, `fixtures`, `tests`, `README.md`, and `DEPLOY.md`.

---

## 1. Check that `sprite` is installed and that you are in the personal org

```powershell
sprite --version
sprite --help
sprite org list
```

Expected:

- `sprite --version` prints a version. Exact text is not quoted in the docs.
- `sprite --help` prints command help (https://docs.fly.io/sprites/cli/installation).
- `sprite org list` looks like this, from https://docs.fly.io/sprites/cli/authentication:

```
Organizations:
  personal (current)
  my-team
  another-org
```

Your list will differ. **The line marked `(current)` must be your personal org, not a work org.** This account may default to a work org. The Sprite must be created in the personal org.

If the current org is wrong, switch it, then list again and confirm:

```powershell
sprite org list
sprite org set-default personal
sprite org list
```

Uncertain: the personal org's slug is `personal` in the docs, but your slug might be different. Use the slug that `sprite org list` shows for the personal org. If you have not added a token for that org yet:

```powershell
sprite org auth --org personal
```

That may open a browser. Approve it yourself.

Optional, on every later command, pass `-o personal` (or your real personal slug) so a default cannot silently target a work org.

### Fallback if `sprite` is not installed

From https://fly.io/sprites/agent-ready.md and https://fly.io/sprites/, this works in PowerShell 5.1. Run it as its own command, then open a new PowerShell window so PATH updates:

```powershell
iwr https://sprites.dev/install.ps1 -useb | iex
```

That line is remote script execution. If execution policy blocks it, do **not** set `Unrestricted` and do **not** change `LocalMachine`. Use a process-scoped bypass for this window only, then retry the installer:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
iwr https://sprites.dev/install.ps1 -useb | iex
```

Or skip the script and use the documented zip (https://docs.fly.io/sprites/cli/installation currently pins `v0.0.1-rc48`; after a manual install, `sprite upgrade` gets the latest). This is x86-64; for ARM64, replace `amd64` with `arm64`. Add the extract folder to **this process** PATH only, then confirm `sprite --version`:

```powershell
Invoke-WebRequest -Uri "https://sprites-binaries.t3.storage.dev/client/v0.0.1-rc48/sprite-windows-amd64.zip" -OutFile "sprite-windows-amd64.zip"
Expand-Archive sprite-windows-amd64.zip -DestinationPath $env:USERPROFILE\bin
$env:Path = "$env:USERPROFILE\bin;$env:Path"
sprite --version
```

The installer puts `sprite` in `~/.local/bin` on Unix. The exact Windows install path is not quoted in agent-ready.md. Then run `sprite login`, approve it in the browser, and go back to `sprite org list`.

---

## 2. Create the Sprite in the personal org

```powershell
sprite org list
sprite create -o personal --skip-console beo-drafter
sprite use -o personal beo-drafter
sprite info
sprite url
sprite list
```

`--skip-console` is from https://fly.io/sprites/agent-ready.md and https://docs.fly.io/sprites/cli/commands, so the command returns instead of opening a shell.

Expected:

- Create finishes without opening a console. Exact success text is not quoted in the docs. The Sprite should appear in `sprite list`.
- `sprite info` and `sprite url` print the Sprite URL and its auth setting. The URL shape, from https://docs.fly.io/sprites/working-with-sprites and agent-ready.md, is `https://<sprite>-<orgid>.sprites.app`, where `<orgid>` is a short generated id, not the org name. Exact field layout is not quoted.
- URL auth is `sprite` (org / token only) by default. Leave it that way until you separately say yes to step 7.

If create complains that the name is taken, pick another name and use that name in every later `-s` flag.

---

## 3. Checkpoint, then copy the code (no public repo)

```powershell
sprite checkpoint create --comment "empty sprite before upload"
sprite checkpoint list
sprite file push -o personal -s beo-drafter -r -p .\ /home/sprite/beo-drafter/
sprite exec -o personal -s beo-drafter -- ls -la /home/sprite/beo-drafter
sprite exec -o personal -s beo-drafter -- python3 --version
sprite exec -o personal -s beo-drafter --dir /home/sprite/beo-drafter -- python3 -m unittest discover -s tests -v
```

`sprite file push` is the documented copy path (https://docs.fly.io/sprites/cli/commands). Do not use a public git remote. `sprite exec --dir` is the documented working-directory flag.

Expected:

- Checkpoint create prints `Checkpoint created: v1` (or the next `vN`) per https://docs.fly.io/sprites/concepts/checkpoints. `sprite checkpoint list` then shows that id. IDs are sequential (`v0`, `v1`, …).
- `ls` shows the project files under `/home/sprite/beo-drafter`.
- `python3 --version` prints a 3.x version. Sprites ship with Python already installed (https://docs.fly.io/sprites/working-with-sprites, https://fly.io/sprites/agent-ready.md). The exact version is not quoted; 3.11+ is assumed.
- The unittest command prints `OK`.

Uncertain: how PowerShell passes a local `.\` path into `sprite file push` alongside a Linux remote path. If push fails, try from the project folder:

```powershell
sprite file push -o personal -s beo-drafter -r -p (Get-Location).Path /home/sprite/beo-drafter/
```

or push a named subfolder you copied as `beo-drafter\` from its parent directory:

```powershell
cd $env:USERPROFILE\hacksgiving
sprite file push -o personal -s beo-drafter -r -p .\beo-drafter\ /home/sprite/beo-drafter/
```

---

## 4. Attach the Ollama Cloud connector (you paste the key)

There is no `sprite connector` command in the public CLI reference. This step is the dashboard, from https://fly.io/sprites/ollama/ and https://fly.io/sprites/agent-ready.md.

1. Open the Fly.io dashboard, select your **personal** org (not a work org), then **Sprites → Connectors → Add connector → Ollama**.
2. Paste the ollama.com API key yourself and click Connect. Do not paste the key into PowerShell, chat, or any file in this project. Fly checks the key against ollama.com before saving. The key is stored in the control plane and never enters the Sprite.
3. If Ollama is not listed, it is not available for that org yet (same page). Stop and do not put a key on the Sprite as a workaround.
4. Grant this Sprite access. A new connector denies every Sprite until you do (otherwise calls return 403). Use a name prefix such as `beo-`, a label, or a tightly scoped allow list. Avoid "Allow all sprites" unless you mean it.
5. Use **Test reachability** on the authorized Sprite row. That checks gateway connectivity, not model access.
6. Copy the **connection ID** from the connector page (the examples call it `YOUR_CONNECTION_ID`). This is not the API key. You will put only that ID, and a model name, into the app environment.

The app calls:

```
https://api.sprites.dev/v1/gateway/ollama/<connection_id>/api/chat
```

with `Content-Type: application/json` and **no Authorization header**. That only works from inside an authorized Sprite. A call from this PC returns 401.

Pick a cloud model your ollama.com account can use and remember its name for the next step (the docs use the placeholder `YOUR_OLLAMA_CLOUD_MODEL`; which models you have is not something this pack can see).

---

## 5. Checkpoint, then start the app as a service

A process started with `sprite exec` stops when the Sprite sleeps. A service starts again on wake and can own the HTTP port (https://docs.fly.io/sprites/concepts/services, https://fly.io/sprites/agent-ready.md).

The demo server binds `127.0.0.1` by default. Sprites route URL traffic to port 8080 from the proxy, not from localhost, so the service must bind `0.0.0.0` explicitly (`--bind 0.0.0.0` and/or `BEO_BIND=0.0.0.0`). Do not set `DEMO_ALLOW_OLLAMA=1` on this service; the HTTP form and `/draft` stay mock-only. You can still smoke the Ollama client from `sprite exec` in step 6 (CLI, not the public form).

PowerShell 5.1 quoting of `--args` and `--env` is unreliable. Prefer creating the service from an inside bash shell. `--%` (stop-parsing) is the fallback if you stay in PowerShell. Do not write these values with `Set-Content` or `Out-File` from 5.1 (UTF-16 / BOM risk on Linux-side files).

Preferred:

```powershell
sprite checkpoint create --comment "code uploaded, before service and URL changes"
sprite exec -o personal -s beo-drafter --tty -- bash
```

Then, inside the Sprite (Linux shell, not PowerShell):

```sh
sprite-env services create beo-web --cmd python3 --args "-m,beo_drafter.server,--bind,0.0.0.0" --dir /home/sprite/beo-drafter --env "BEO_BIND=0.0.0.0,OLLAMA_CONNECTION_ID=YOUR_CONNECTION_ID,OLLAMA_MODEL=YOUR_OLLAMA_CLOUD_MODEL" --http-port 8080
exit
```

PowerShell 5.1 `--%` fallback (no nested quotes after `--%`):

```powershell
sprite exec -o personal -s beo-drafter --% -- sprite-env services create beo-web --cmd python3 --args -m,beo_drafter.server,--bind,0.0.0.0 --dir /home/sprite/beo-drafter --env BEO_BIND=0.0.0.0,OLLAMA_CONNECTION_ID=YOUR_CONNECTION_ID,OLLAMA_MODEL=YOUR_OLLAMA_CLOUD_MODEL --http-port 8080
```

Replace `YOUR_CONNECTION_ID` and `YOUR_OLLAMA_CLOUD_MODEL` with the connection ID and model name only. Do not put the API key in `--env`. Then list services:

```powershell
sprite exec -o personal -s beo-drafter -- sprite-env services list
```

Expected, from the services doc: a short NDJSON stream with `started` then `complete`, and a log path under `/.sprite/logs/services/`. `services list` / `get` should show `cmd`, `http_port: 8080`, and a running state. Exact field names for `list` are not fully quoted; `get` is shown as JSON with `state.status`.

If the service exits, read the log:

```powershell
sprite exec -o personal -s beo-drafter -- tail -n 50 /.sprite/logs/services/beo-web.log
```

`--cmd` is the binary only; arguments go in `--args`, comma-separated. Only one service can own the HTTP port. The host bind is an app flag (`--bind`) plus `BEO_BIND`; `--http-port 8080` only tells the Sprite proxy where to send traffic.

---

## 6. Smoke check (keep the URL private)

```powershell
sprite exec -o personal -s beo-drafter -- curl -sS http://127.0.0.1:8080/health
sprite exec -o personal -s beo-drafter --dir /home/sprite/beo-drafter -- python3 -m beo_drafter draft fixtures/wedding.json --out /tmp/beo-out
```

`--dir` is a `sprite exec` flag (https://docs.fly.io/sprites/cli/commands), not a flag on the Python CLI. If an older CLI rejects it, use:

```powershell
sprite exec -o personal -s beo-drafter -- bash -c "cd /home/sprite/beo-drafter && python3 -m beo_drafter draft fixtures/wedding.json --out /tmp/beo-out"
```

Expected:

- Health: `{"ok": true}`
- Draft: two paths printed for `/tmp/beo-out/wedding.json` and `wedding.md`

Optional Ollama smoke, still from inside the Sprite (this is billable model use):

```powershell
sprite exec -o personal -s beo-drafter --dir /home/sprite/beo-drafter -- python3 -m beo_drafter draft fixtures/offsite-lunch.json --client ollama --out /tmp/beo-out-ollama
```

Expected: JSON and Markdown written, or a clear HTTP error such as 403 if the connector access policy is wrong. Do not run this from the PC against the gateway; that returns 401.

To try the form in a browser while the URL is still private, use a local proxy (https://docs.fly.io/sprites/working-with-sprites):

```powershell
sprite proxy -o personal -s beo-drafter 8080
```

Then open http://127.0.0.1:8080 on this PC. Stop the proxy with Ctrl+C when done.

---

## 7. Public demo URL (a later step — do not run until the operator is ready)

**Stop here unless the operator has decided to make this Sprite URL public.** Earlier steps in this pack cover deploying a Sprite in the personal org and attaching a connector (key pasted in the dashboard only). They do not cover a public demo. A public repo and a public post are also later, separate steps.

```powershell
sprite url
sprite info
```

The URL is private to the org by default. Visiting it in a browser should ask you to log in with Fly.io, or you can call it with an org token (https://docs.fly.io/sprites/cli/commands).

Spend warning: `sprite config update --url-auth public` puts the demo on the open internet. Anyone with the URL can wake the Sprite and use the form, which costs a little compute even while the HTTP path stays mock-only (the default). Do **not** set `DEMO_ALLOW_OLLAMA=1` on a public URL. If that variable is set, anyone who can reach `/draft` can trigger billable Ollama Cloud calls from inside the authorized Sprite. Leave it unset so the public demo runs mock only.

If the operator is ready to make the URL public, checkpoint first, then flip auth:

```powershell
sprite checkpoint create --comment "before making URL public"
sprite config update --url-auth public
sprite url
```

`sprite url update --auth public` is the same change (also documented). Expected: `sprite url` / `sprite info` now show auth `public`. Anyone with the URL can reach whatever the service serves. Do not put secrets in the app; this project does not need the Ollama key in the Sprite.

Flip it back when the demo is over (also part of the spend cap in step 8):

```powershell
sprite config update --url-auth sprite
sprite url
```

---

## 8. Stop things to cap spend

Sprite compute is billed only while the Sprite is active; idle / warm / cold compute is $0. Storage still accrues a little while the Sprite exists. Ollama model usage is billed by Ollama, separately (https://fly.io/sprites/, https://fly.io/sprites/ollama/).

```powershell
sprite exec -o personal -s beo-drafter -- sprite-env services stop beo-web
sprite config update --url-auth sprite
```

`services stop` is sticky: the runtime will not restart a stopped service on its own (https://docs.fly.io/sprites/concepts/services). The same page also says a service with `--http-port` is auto-started when a request arrives if it is not running. Those two statements may conflict. To be safe, stop the service **and** set URL auth back to `sprite` so casual public traffic cannot wake and start it. Do not leave a `sprite exec` or `sprite proxy` session running; those keep the Sprite awake.

To remove the Sprite entirely (irreversible; files and checkpoints are deleted):

```powershell
sprite destroy -o personal -s beo-drafter
```

The docs warn there is no undo (https://docs.fly.io/sprites/working-with-sprites). Only do this if you mean to throw the environment away.

---

## If something goes wrong

```powershell
sprite checkpoint list
sprite restore v1
```

Use the checkpoint id you actually created. Restore replaces the filesystem with that snapshot (https://fly.io/sprites/agent-ready.md).
