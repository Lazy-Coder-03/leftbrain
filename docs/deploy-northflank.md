# Deploy leftbrain on Northflank (free sandbox) at leftbrain.idlesync.in

Northflank's Developer Sandbox gives 2 always-on services, 1 database addon and custom domains with TLS for free. A card is required at signup for verification only; nothing is charged unless you upgrade. Free projects must live in a non-APAC region (Europe – West/London is fine from India: ~120 ms).

Prerequisites: the repo is on GitHub (`Lazy-Coder-03/leftbrain`), the team has GitHub linked (top banner → *Link now* → authorize → select the repo), and a project `leftbrain` exists.

---

## Step 1 — PostgreSQL addon (the key store)

Project → **Addons → Create addon** (or *Deploy database* on the welcome card).

| Section | Field | Value |
|---|---|---|
| Basic information | Addon name | `keys` |
| | Addon type | **PostgreSQL** |
| | Version | newest offered (16/17) |
| | Environment | `Default` |
| Data | | **Fresh addon** |
| Resources | Compute plan | `nf-compute-20` (0.2 vCPU / 512 MB — the default) |
| | Storage type / size | NVMe, smallest offered (6 GB) |
| | Replicas | 1 |
| Networking | Deploy with TLS | ✔ on |
| | Publicly accessible | **off** (only the service needs it; turn on temporarily if you want to run `leftbrain-keys` from your laptop) |

**Create addon** → wait for status *Running* (1–2 min).

## Step 2 — Secret group that exposes the DB to services

Project → **Secrets → Create secret group**.

| Field | Value |
|---|---|
| Name | `keys-link` |
| Environment | `Default` |
| Linked addons | expand → tick `keys` → **Configure** → choose **Suggested** variables |
| Alias | on the connection-string variable (looks like `NF_KEYS_POSTGRES_URI` or the `…_URI`/`DATABASE_URL` entry) add alias **`LEFTBRAIN_KEYS_URL`** |
| Inheritance | leave "all services and jobs in the project" |

Save. Any service in the project now receives `LEFTBRAIN_KEYS_URL=postgresql://…` at start.
(If the group already produced `DATABASE_URL`, that also works — leftbrain checks `LEFTBRAIN_KEYS_URL`, then `DATABASE_URL`.)

## GitHub OAuth App

Create one at github.com → Settings → Developer settings → **OAuth Apps → New OAuth App**: homepage URL `https://leftbrain.idlesync.in`, authorization callback URL `https://leftbrain.idlesync.in/auth/github/callback`. Note the **Client ID** and generate a **Client secret** — both go into the service in Step 3.

## Step 3 — The service

Project → **Services → Create service → Combined service** (build + deploy from Git).

| Section | Field | Value |
|---|---|---|
| Basic | Name | `leftbrain` (cannot be renamed later) |
| Repository | Repo / branch | `Lazy-Coder-03/leftbrain` · `main` |
| Build | Type | **Dockerfile** · path `/Dockerfile` · context `/` |
| Environment | Runtime variables | `PORT=8080` · `WEB_CONCURRENCY=1` · `LEFTBRAIN_SERVE_EXTERNAL=1` · `LEFTBRAIN_SERVE_FILES=0` · `LEFTBRAIN_DEFAULT_DAILY_QUOTA=1000` · `LEFTBRAIN_DEFAULT_RPM=60` · `GITHUB_CLIENT_ID=…` · `GITHUB_CLIENT_SECRET=…` · `LEFTBRAIN_SECRET=<python -c "import secrets;print(secrets.token_urlsafe(48))">` · `LEFTBRAIN_BASE_URL=https://leftbrain.idlesync.in` · `LEFTBRAIN_TRUSTED_PROXY_HOPS=1` |
| Networking | Port | `8080`, protocol **HTTP**, **Public** ✔, name `web` (auto-filled from `EXPOSE 8080`; check the *Public* toggle) |
| Resources | Plan | the sandbox default (`nf-compute-10`/`20`) · 1 instance |
| Advanced → Health checks | | HTTP · port `8080` · path `/healthz` · initial delay 20 s |
| Advanced → CMD override | | leave empty (Dockerfile runs `leftbrain-serve`) |

`LEFTBRAIN_TRUSTED_PROXY_HOPS` tells the server how many proxies append to `X-Forwarded-For` in front of it, so per-IP limits (demo throttle, signup throttle) are keyed on the entry that hop wrote rather than on the leftmost, caller-supplied one. Northflank's router is **one** hop, so `1` (the default) is right here. Put Cloudflare’s proxy in front of it later and it becomes `2`. Set `0` only if the process is reachable directly with no proxy at all — then no forwarding header is believed and the socket address is used.

`POST /feedback` and the `/report` form are **off** until two more are set: `LEFTBRAIN_FEEDBACK_REPO=Lazy-Coder-03/leftbrain` and `LEFTBRAIN_FEEDBACK_TOKEN=<a fine-grained PAT with *Issues: write* on that repository>`. Without them both answer that reporting is not configured and point at the tracker, so nothing is lost, but nothing is filed either.

`GITHUB_CLIENT_SECRET`, `LEFTBRAIN_SECRET` and `LEFTBRAIN_FEEDBACK_TOKEN` are secrets, not plain config — add them to the `keys-link` secret group from Step 2 (alongside `LEFTBRAIN_KEYS_URL`) so they're inherited the same way, or set them directly on this service's **Environment** tab if you'd rather not share them project-wide.

**Do not rotate `LEFTBRAIN_SECRET` casually.** It signs session and CSRF cookies, and it also derives the key that encrypts the retrievable copy of every API key — what the dashboard's *Show* button reads, and what fills a signed-in reader's own key into the docs examples. Changing it signs everyone out and leaves every key issued before the change unrevealable: those keys keep authenticating normally, they just can no longer be shown again, so their owners have to create new ones. Treat it like a database credential and keep it stable. Leave it unset and the store keeps nothing but the SHA-256 hash — sign-in is off in that case anyway.

**Create service**. The first build takes ~2–3 min (BuildKit). Watch **Builds** then **Logs**; the startup line is a JSON object containing `"auth": "keys"` — that confirms the DB link worked. If it says `"auth": "none"`, the secret group isn't attached: Service → *Environment* → check inherited secret groups, then *Restart*.

Open the public URL shown on the service header (`https://web--leftbrain--….code.run` or `….northflank.app`):

- `/healthz` → `{"ok": true, "version": "0.1.0"}`
- `/` → service description with `"auth": "keys"`, `"login": "/login"` (anonymous `"signup"` stays `null` unless you also set `LEFTBRAIN_OPEN_SIGNUP=1`)

## Step 4 — First key and MCP test

Open `https://<public-url>/`, click *Sign in with GitHub*, create a key on the dashboard, then:

```bash
curl https://<public-url>/keys/me -H "Authorization: Bearer lblz_…"
claude mcp add --transport http leftbrain https://<public-url>/mcp --header "Authorization: Bearer lblz_…"
```

## Step 5 — Custom domain leftbrain.idlesync.in

Team level (top-left team menu) → **Domains → Add domain** → `leftbrain.idlesync.in`.

1. Northflank shows a **TXT** record: add it at your DNS provider (Cloudflare: *DNS → Add record → TXT*, name/value exactly as shown). Click **Verify** (propagation 1–10 min).
2. It then shows a **CNAME** target: add `CNAME leftbrain → <target>`; in Cloudflare set **Proxy status = DNS only (grey cloud)** until the certificate is issued.
3. Go to the service → **Ports & DNS** → the `web` port → **Assign domain** → pick `leftbrain.idlesync.in`. Let's Encrypt issues the cert automatically (a few minutes).
4. `curl https://leftbrain.idlesync.in/healthz` → ok. You may switch the Cloudflare record to proxied afterwards if you want its WAF; keep "Full (strict)" SSL mode.

## Step 6 — Admin

Turn on *Publicly accessible* on the `keys` addon only while you need it, copy the external connection string, then:

```bash
pip install "leftbrain[postgres]"
LEFTBRAIN_KEYS_URL="postgresql://…external…" leftbrain-keys stats
# set LEFTBRAIN_SECRET to the service's value too, so keys made here stay revealable
leftbrain-keys create --owner partner@example.com --daily 50000 --rpm 300 --expires 90d --note "partner"
leftbrain-keys list | disable <prefix> | revoke <prefix> | set <prefix> --expires 30d | usage --days 7
```

## Limits and next steps

- Free tier: ~10 GB egress and ~1M requests/month; always-on, no cold starts; redeploys on every push to `main` (~2 min).
- Per-minute rate limits are in memory (one container = exact); daily quotas live in Postgres.
- More CPU later: change the service plan to `nf-compute-50`+ on pay-as-you-go — no migration.
- To expose file tools set `LEFTBRAIN_SERVE_FILES=1` and mount a volume at the path you put in `LEFTBRAIN_FILE_ROOTS` (Advanced → Volumes).
