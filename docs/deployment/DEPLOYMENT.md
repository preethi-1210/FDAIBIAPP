# Production deployment

Ryze Infinity deploys to **https://www.ryzeaibiapp.com** with GitHub Actions and one Oracle Cloud
Ubuntu 24.04 VM. It uses Caddy, systemd, PostgreSQL 18, Next.js and FastAPI, with no Docker or
Kubernetes. The release branch is **`feature/agentic_ai_mode`**.

```
Browser ──HTTPS──> Caddy :443 ──/api/* (prefix removed)──> FastAPI 127.0.0.1:8013 ──> PostgreSQL 127.0.0.1:5432
                              └─everything else─────────> Next.js 127.0.0.1:3000      └──> Databricks, Google AI (outbound)
```

## First deployment, in order

1. **Server.** Follow [ORACLE_CLOUD_SETUP.md](ORACLE_CLOUD_SETUP.md): create the VM, open 80/443,
   run `provision.sh` and fill in `backend.env`.
2. **DNS.** Follow [DNS_SETUP.md](DNS_SETUP.md), and wait until both names resolve to the VM.
3. **GitHub.** Follow [GITHUB_SECRETS.md](GITHUB_SECRETS.md): the `production` environment with
   you as required reviewer, five secrets and the variables.
4. **Deploy.** GitHub → Actions → **Deploy production** → **Run workflow** on
   `feature/agentic_ai_mode`. Approve it when it pauses at the `production` environment.
5. **First administrator.** No demo accounts exist in production. On the server:
   ```bash
   sudo -u ryze bash -c 'set -a; . /opt/ryze/shared/backend.env; set +a; cd /opt/ryze/current/backend && .venv/bin/python create_user.py --username <name> --domain all --display-name "<Name>"'
   sudo -u ryze bash -c 'set -a; . /opt/ryze/shared/backend.env; set +a; cd /opt/ryze/current/backend && .venv/bin/python set_user_role.py --username <name> --role admin'
   ```
   It asks for the password interactively; the password is never placed in a command or file.
6. **Verify.** On the server, run `/opt/ryze/bin/healthcheck.sh --public`. Then run the acceptance
   tests below.

## How a deployment works

Every push to `feature/agentic_ai_mode` (or a manual run) does the following:

1. **Validate.** The full CI workflow runs: backend syntax and import checks, 650+ tests, the
   offline evaluation, the frontend type check and a production build. Any failure stops the
   deployment.
2. **Build.** The frontend is compiled with `NEXT_PUBLIC_API_URL`. The release is packaged as
   application code only, with no tests, env files, local databases or backups, and the package
   is checked for env, database and dump files.
3. **Approval.** The run waits for your approval at the `production` environment.
4. **Ship.** The package goes over SSH, with a pinned host key and the deploy-only key, into a
   new folder `/opt/ryze/releases/<timestamp>-<commit>`. The running release is not touched.
5. **Install.** Python packages go into a shared environment keyed by `requirements.txt`'s hash,
   so they are reinstalled only when requirements change. `npm ci` runs for the server's CPU, and
   the backend import check runs.
6. **Switch.** `current` is atomically re-pointed and both services restart.
7. **Health.** The script waits up to 3 minutes for `/health` (database reachable) and the web
   app. **If either fails, it switches back to the previous release, restarts it and marks the
   run failed.**
8. **Public check.** The workflow confirms `https://www.ryzeaibiapp.com/login` and `/api/health`
   respond over HTTPS.

There is a lock at two levels: GitHub runs one deployment at a time, and the server holds a
`flock`. The 5 most recent releases are kept; the current and previous ones are never pruned.

**Database migrations never run during deployment.** On start, the API creates any missing tables
and does nothing else to existing ones. That is its existing behaviour. The Alembic migrations in
`backend/migrations` are run only as a separate, approved step after a verified backup.

## Rollback

- **Automatic.** A deployment that fails its health checks rolls itself back.
- **Manual.** On the server, as the deploy user:
  ```bash
  /opt/ryze/bin/rollback.sh            # the release before the current one
  ls /opt/ryze/releases                # or choose one:
  /opt/ryze/bin/rollback.sh <release-id>
  ```
- **Alternative.** Re-run an older successful **Deploy production** run in GitHub Actions.

A rollback restores code only. It does not undo database changes; use the backups for that.

## Operations

| Task | Command (on the server) |
|---|---|
| Health report | `/opt/ryze/bin/healthcheck.sh` (add `--public` to include HTTPS and the redirect) |
| Service status | `systemctl status ryze-backend ryze-frontend caddy postgresql` |
| Logs | `journalctl -u ryze-backend -n 200 --no-pager` (also `-u ryze-frontend`, `-u caddy`) |
| Deployment log | `less /opt/ryze/shared/logs/deploy.log` |
| Restart the API after editing `backend.env` | `sudo systemctl restart ryze-backend` |
| Backup now | `sudo systemctl start ryze-db-backup` |
| Prove the latest backup restores | `sudo -u postgres /opt/ryze/bin/restore_check.sh` |
| Restore into a NEW database | `sudo -u postgres /opt/ryze/bin/restore_db.sh <dump> <new_db_name>` |

**Backups** run nightly at 02:30 server time. Each backup is a custom-format `pg_dump`, checked
with `pg_restore --list` before it is kept, with a SHA-256 checksum and a dump of roles. They are
kept for 14 days in `/var/backups/ryze`, which only the postgres user can read. Copy them off the
VM regularly, for example to Oracle Object Storage (Always Free includes 20 GB). A VM-only backup
does not survive losing the VM. `restore_check.sh` restores into a throwaway database and compares
every table's row count with production. `restore_db.sh` refuses to overwrite an existing
database.

## Security model

- **Public ports.** Only 22, 80 and 443 are open. The API, web app and PostgreSQL listen on
  127.0.0.1, and the health check fails if any of them listens anywhere else.
- **API docs and metrics.** `/api/docs`, `/api/redoc`, `/api/openapi.json` and `/api/metrics`
  return 404 publicly.
- **HTTPS.** Certificates are issued and renewed automatically. Responses carry HSTS, nosniff,
  frame-deny, referrer and permissions headers, and the Server header is removed.
- **Secrets.** They live only in `/opt/ryze/shared/backend.env` (root:ryze, 640). GitHub holds the
  deploy credentials only.
- **The deploy user** can write releases and restart the two app services, nothing else with sudo.
- **The services** run as `ryze` with systemd hardening, and can write only under
  `/opt/ryze/shared`.
- **PostgreSQL** uses a non-superuser application role. Production startup refuses a superuser,
  DEMO_MODE, weak JWT secrets, non-HTTPS addresses and published demo passwords.
- **SSH** accepts keys only, refuses root logins, allows only named users, and runs behind
  fail2ban.

## Five-user acceptance tests (after the first deployment)

Run these with five people, or five browser profiles, signed in at the same time, at least one per
domain:

| # | Check | Expected |
|---|---|---|
| 1 | Google sign-in, then sign-out | Signs in through Google, lands in the user's domain; sign-out returns to /login |
| 2 | Banking, Finance, Insurance, Retail, Healthcare | Each user sees only their own domain's data and labels |
| 3 | Dashboard per domain | Loads values from Databricks; no other domain's figures |
| 4 | Ask AI: "How many active accounts do we have?" (Banking) | A Verified answer from approved data, which proves Databricks connectivity |
| 5 | Ask AI: an unrelated question ("chocolate cake recipe") | Outside-your-data card; Monitor shows FAILED with "No audit files" |
| 6 | Foresight | Loads the approved metrics and rules for the domain |
| 7 | Five questions submitted at once | All complete; no errors; answers stay per user |
| 8 | A non-admin opens /admin | Refused |
| 9 | Cross-domain request (change the domain in a request) | Refused by the API |

## Known security findings (reported, not blocking)

The **Security checks** workflow reports these without blocking a deployment:

- **Python:** `python-jose` and its dependencies `pyasn1` and `ecdsa` (the JWT library chain,
  which needs an authentication change, so it is out of scope here), `chromadb`, and `pytest`
  (a test tool listed in production requirements).
- **Node:** 11 high findings from Tailwind CSS 3's build tooling and 1 moderate finding in Next.js
  15. Fixing them requires the Tailwind 4 and Next 16 upgrades.

Each needs its own approved change. The secret scan **does** block.

## Not yet in place

- **Garnet shared cache.** This waits for its compatibility validation. Until then the API runs
  one worker, which is enough for five users.
- **ESLint.** The project has no lint configuration. The type check and `next build` cover types.
- **Off-server backup copies.** These are recommended; see Operations above.
- **A staging environment.** One VM hosts production only. For a rehearsal, provision a second
  VM with the same steps and point a test subdomain at it.
