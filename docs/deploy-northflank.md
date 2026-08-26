# Deploy leftbrain on Northflank (free tier) at leftbrain.idlesync.in

Northflank's Developer Sandbox gives 2 always-on services, 1 database and custom domains with TLS for free (a card is required at signup for verification only; nothing is charged unless you upgrade). Compute per free service is small (~0.1–0.2 vCPU, ~512 MB) — leftbrain runs one uvicorn worker and fits.

## 1. Push the repo to GitHub

```bash
cd "D:\ML projects\leftbrain"
gh repo create leftbrain --public --source . --push
```

## 2. Create the project and database

1. Sign up at northflank.com → **Create project** (`leftbrain`, pick the region closest to your users; Mumbai is not offered, Singapore/Frankfurt/US are).
2. **Add addon → PostgreSQL**, name `keys`, smallest plan. Wait until it's running.

## 3. Create the service

1. **Add service → Deployment / Combined service → Git repository**, link GitHub, pick `leftbrain`, branch `main`.
2. Build: **Dockerfile**, path `/Dockerfile`, context `/`.
3. Ports: Northflank detects `8080` from `EXPOSE`; make sure it is **HTTP, public**.
4. Environment variables (Runtime):

   | Name | Value |
   |---|---|
   | `PORT` | `8080` |
   | `WEB_CONCURRENCY` | `1` |
   | `LEFTBRAIN_SERVE_EXTERNAL` | `1` |
   | `LEFTBRAIN_SERVE_FILES` | `0` |
   | `LEFTBRAIN_DEFAULT_DAILY_QUOTA` | `5000` |
   | `LEFTBRAIN_DEFAULT_RPM` | `60` |

5. **Link the addon**: on the `keys` addon → *Secrets / Link to service* → choose the service and accept the suggested variables. Northflank injects `DATABASE_URL`, which leftbrain picks up automatically as the key store.
6. Health check: HTTP, port `8080`, path `/healthz`.
7. Deploy. When it's green, open the generated `https://….northflank.app/` — you should see the service JSON with `"auth": "keys"`.

## 4. Attach leftbrain.idlesync.in

1. Northflank → **Domains → Add domain** → `leftbrain.idlesync.in`. It shows a **TXT** record for verification.
2. In your DNS (Cloudflare recommended): add the TXT record, then the **CNAME** `leftbrain` → the target Northflank shows. Keep the record **DNS-only (grey cloud)** during verification and certificate issuance; you can proxy it afterwards if you want Cloudflare's WAF.
3. Back in Northflank: **Verify**, then on the service's **Ports & DNS** page assign the domain to the public port. TLS is issued automatically within a few minutes.

## 5. Smoke test

```bash
curl https://leftbrain.idlesync.in/healthz
curl -X POST https://leftbrain.idlesync.in/keys/signup -H "content-type: application/json" -d '{"email":"you@example.com"}'
# copy the key
claude mcp add --transport http leftbrain https://leftbrain.idlesync.in/mcp --header "Authorization: Bearer lb_..."
```

Admin from your machine (uses the same Postgres):

```bash
LEFTBRAIN_KEYS_URL="postgres://…external URL from the addon…" leftbrain-keys stats
leftbrain-keys create --owner partner@example.com --daily 50000 --rpm 300
```

## Limits to know

- ~10 GB egress and ~1M HTTP requests per month on the free tier — plenty for an early public API.
- One container: per-minute rate limits are in memory (exact), daily quotas are in Postgres (exact across restarts).
- Cold starts don't exist (always-on); redeploys take ~1–2 min while the new container builds.
- If you later need more CPU, the next step is `nf-compute-20`/`50` on pay-as-you-go ($5–12/mo) — no migration.
