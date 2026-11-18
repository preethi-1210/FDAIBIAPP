# Oracle Cloud server setup

Target: one Always Free **Ampere A1 (Arm)** VM running Ubuntu 24.04, hosting Caddy, the Next.js
web app, the FastAPI API and PostgreSQL 18. No Docker or Kubernetes.

## 1. Create the VM

Oracle console → Compute → Instances → **Create instance**:

| Setting | Value |
|---|---|
| Image | Canonical **Ubuntu 24.04** (not "Minimal") |
| Shape | **VM.Standard.A1.Flex**: 2 OCPU / 12 GB RAM or more (Always Free allows up to 4 OCPU / 24 GB in total) |
| Boot volume | 100 GB (Always Free allows 200 GB in total) |
| Networking | A public subnet; **assign a public IPv4 address** |
| SSH keys | Upload **your own admin** public key (not the deploy key) |

The 1 GB AMD micro shape is too small for this application (PyTorch/TabPFN, pandas, PostgreSQL
and Next.js together). If A1 capacity is unavailable in your region, retry later or pick another
availability domain.

Then make the IP permanent: Instance → Attached VNICs → IPv4 addresses → **Reserved public IP**.

## 2. Open ports 80 and 443 in Oracle's network

Networking → Virtual cloud networks → your VCN → the subnet's **Security list** → Add ingress rules:

| Source CIDR | Protocol | Destination port | Purpose |
|---|---|---|---|
| `0.0.0.0/0` | TCP | 80 | HTTPS certificate issuance and redirect |
| `0.0.0.0/0` | TCP | 443 | The site |

Keep the existing port 22 rule. If you have a fixed office/home IP, narrow port 22's source to it.
**Do not** add 3000, 8013, 5432 or any cache port: those stay on the server's localhost.

## 3. Provision

From your PC:

```powershell
scp $HOME\.ssh\ryze_deploy.pub ubuntu@<PUBLIC_IP>:/home/ubuntu/
ssh ubuntu@<PUBLIC_IP>
```

On the server:

```bash
git clone --branch feature/agentic_ai_mode https://github.com/jayaprakash1920/FDAIBIAPP.git ~/ryze-setup
cd ~/ryze-setup
sudo DEPLOY_PUBKEY_FILE=/home/ubuntu/ryze_deploy.pub bash deploy/server/provision.sh
```

(If the repository is private, clone with a read-only GitHub token or copy the `deploy/` folder
with `scp -r`.)

What it does: security updates and automatic security patches; Python 3.13, Node.js 22,
PostgreSQL 18, Caddy; users `ryze` (runs the services, no login) and `ryzedeploy` (GitHub
deployments); `/opt/ryze` layout; a restricted non-superuser database role and empty database;
systemd units; nightly verified backups; log rotation; firewall rules for 80/443; SSH keys-only
with root login disabled; fail2ban. It is safe to run again and never overwrites existing env
files, databases or roles.

**Before closing your SSH session**, open a second terminal and confirm `ssh ubuntu@<PUBLIC_IP>`
still works.

## 4. Fill in the server settings

```bash
sudo nano /opt/ryze/shared/backend.env
```

Replace every `CHANGE_ME`. Copy the Databricks catalog/schema values exactly from your current
`backend/.env`; do not alter them. `DATABASE_URL` and `JWT_SECRET` were generated for you.
Then in **Google Cloud Console → APIs & Services → Credentials → your OAuth client**, add the
authorized redirect URI `https://www.ryzeaibiapp.com/api/auth/oidc/callback`.

## 5. Server layout

```
/opt/ryze/
  releases/<timestamp>-<commit>/   one folder per deployment (frontend/, backend/, deploy/)
  current -> releases/<...>        switched atomically after a release installs
  shared/
    backend.env, frontend.env      root:ryze, mode 640
    venvs/<hash>/                  Python environments, reused across releases
    data/, home/                   the only paths the services may write
    logs/deploy.log
    backups -> /var/backups/ryze   postgres-only; outside the deploy user's reach
  bin -> /usr/local/lib/ryze       healthcheck, rollback, backup and restore scripts
```

## 6. Garnet

Not installed. It is added as its own systemd service only after the cache compatibility tests
pass. Until then `CACHE_URL` is empty and the API runs a single worker.
