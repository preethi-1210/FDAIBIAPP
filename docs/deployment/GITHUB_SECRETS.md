# GitHub secrets and variables

Only **deployment credentials** go to GitHub. Application secrets (database password, JWT secret,
Google OAuth client secret, Databricks token, Google AI key) stay on the server in
`/opt/ryze/shared/backend.env` and are never stored in GitHub or in the repository.

Add these under **GitHub → repository → Settings → Environments → `production`**. Using the
environment (not repository-wide secrets) means only the deploy job can read them, and you can
require your approval before each deployment.

## 1. Create the `production` environment

1. Settings → Environments → **New environment** → name it `production`.
2. Tick **Required reviewers** and add yourself. Every deployment then waits for your approval.
3. Under **Deployment branches and tags**, choose **Selected branches** and add `feature/agentic_ai_mode`.

## 2. Environment secrets

| Secret | Value | How to get it |
|---|---|---|
| `PRODUCTION_HOST` | The VM's reserved public IP address | Oracle console → Instance → Primary VNIC |
| `PRODUCTION_USER` | `ryzedeploy` | Created by `provision.sh` |
| `PRODUCTION_PORT` | `22` (optional; defaults to 22) | |
| `PRODUCTION_SSH_KEY` | The **private** deploy key, whole file including the `BEGIN`/`END` lines | Step 4 below |
| `PRODUCTION_KNOWN_HOSTS` | The server's host-key line | Step 5 below |

Never paste any of these into a chat, an issue or a commit. GitHub masks secrets in logs.

## 3. Repository variables (not secret)

Settings → Secrets and variables → Actions → **Variables**:

| Variable | Value |
|---|---|
| `NEXT_PUBLIC_API_URL` | `https://www.ryzeaibiapp.com/api` |
| `PUBLIC_SITE_URL` | `https://www.ryzeaibiapp.com` |
| `PRODUCTION_APP_PATH` | `/opt/ryze` (optional; this is the default) |

## 4. Create the deploy key (on your PC, PowerShell)

```powershell
ssh-keygen -t ed25519 -C "ryze-github-deploy" -f $HOME\.ssh\ryze_deploy -N '""'
```

- `ryze_deploy.pub` (public) → copy to the server and pass to `provision.sh` as `DEPLOY_PUBKEY_FILE`.
- `ryze_deploy` (private) → open it in Notepad, copy everything into the `PRODUCTION_SSH_KEY` secret,
  then keep the file only as long as you need it. This key can deploy and restart the two app
  services; it cannot use sudo for anything else.

## 5. Pin the server's host key

After provisioning, from your PC:

```powershell
ssh-keyscan -t ed25519 <PRODUCTION_HOST>
```

Compare its fingerprint with the one `provision.sh` printed (or run
`ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub` on the server). If they match, paste the
`ssh-keyscan` output line into `PRODUCTION_KNOWN_HOSTS`. If you use a port other than 22, run
`ssh-keyscan -p <port> ...`; the line then starts with `[ip]:port`.

## Rotating

- Deploy key: generate a new pair, replace `/home/ryzedeploy/.ssh/authorized_keys` on the server,
  update `PRODUCTION_SSH_KEY`, delete the old private key.
- Application secrets: edit `/opt/ryze/shared/backend.env` on the server, then
  `sudo systemctl restart ryze-backend`.
