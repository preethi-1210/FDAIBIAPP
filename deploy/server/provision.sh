#!/usr/bin/env bash
# One-time (and safely repeatable) setup of an Ubuntu 24.04 server for Ryze Infinity.
#
#   sudo DEPLOY_PUBKEY_FILE=/home/ubuntu/ryze_deploy.pub bash deploy/server/provision.sh
#
# Run from a checkout of the repository. It never overwrites an existing environment file,
# database, role or table, never prints secrets, and leaves the admin SSH user working.
# Garnet is NOT installed here: it is added only after its compatibility validation passes.
set -euo pipefail

ADMIN_USER="${ADMIN_USER:-ubuntu}"          # The existing Oracle admin login; kept working.
DEPLOY_USER="${DEPLOY_USER:-ryzedeploy}"    # Used only by GitHub Actions to ship releases.
APP_USER="ryze"                             # Runs the services; cannot log in.
APP_ROOT="/opt/ryze"
TOOLS="/usr/local/lib/ryze"
BACKUP_DIR="/var/backups/ryze"
DB_NAME="${DB_NAME:-ryze_production}"
DB_ROLE="${DB_ROLE:-ryze_app}"
NODE_MAJOR=22
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

log() { printf '\n== %s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "Run with sudo."
. /etc/os-release
[[ "${ID}" == "ubuntu" && "${VERSION_ID}" == "24.04" ]] || die "Expected Ubuntu 24.04, found ${PRETTY_NAME}."
[[ -n "${DEPLOY_PUBKEY_FILE:-}" && -f "${DEPLOY_PUBKEY_FILE}" ]] || die "Set DEPLOY_PUBKEY_FILE to the deploy key's PUBLIC key file (.pub)."
grep -q "PRIVATE KEY" "${DEPLOY_PUBKEY_FILE}" && die "DEPLOY_PUBKEY_FILE contains a private key. Use the .pub file."
id "${ADMIN_USER}" >/dev/null 2>&1 || die "Admin user ${ADMIN_USER} does not exist; set ADMIN_USER."

export DEBIAN_FRONTEND=noninteractive

log "Security updates and base packages"
apt-get update -q
apt-get upgrade -yq
apt-get install -yq ca-certificates curl gnupg git rsync jq unzip build-essential \
  software-properties-common debian-keyring debian-archive-keyring apt-transport-https \
  logrotate unattended-upgrades fail2ban iptables-persistent netfilter-persistent
cat >/etc/apt/apt.conf.d/20auto-upgrades <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
EOF

log "Python 3.13 (deadsnakes; Ubuntu 24.04 ships 3.12)"
if ! command -v python3.13 >/dev/null; then
  add-apt-repository -y ppa:deadsnakes/ppa
  apt-get update -q
fi
apt-get install -yq python3.13 python3.13-venv python3.13-dev

log "Node.js ${NODE_MAJOR} LTS (NodeSource)"
install -d -m 0755 /etc/apt/keyrings
if [[ ! -f /etc/apt/keyrings/nodesource.gpg ]]; then
  curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key | gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg
fi
echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_${NODE_MAJOR}.x nodistro main" >/etc/apt/sources.list.d/nodesource.list

log "PostgreSQL 18 (PostgreSQL apt repository)"
apt-get install -yq postgresql-common
if [[ ! -f /etc/apt/sources.list.d/pgdg.list && ! -f /etc/apt/sources.list.d/pgdg.sources ]]; then
  /usr/share/postgresql-common/pgdg/apt.postgresql.org.sh -y
fi

log "Caddy (official apt repository)"
if [[ ! -f /usr/share/keyrings/caddy-stable-archive-keyring.gpg ]]; then
  curl -fsSL https://dl.cloudsmith.io/public/caddy/stable/gpg.key | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
fi
curl -fsSL https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt >/etc/apt/sources.list.d/caddy-stable.list

apt-get update -q
apt-get install -yq nodejs postgresql-18 caddy
node --version; python3.13 --version; psql --version; caddy version

log "Users"
id "${APP_USER}" >/dev/null 2>&1 || useradd --system --home-dir "${APP_ROOT}/shared/home" --shell /usr/sbin/nologin "${APP_USER}"
id "${DEPLOY_USER}" >/dev/null 2>&1 || useradd --create-home --shell /bin/bash "${DEPLOY_USER}"
install -d -m 0700 -o "${DEPLOY_USER}" -g "${DEPLOY_USER}" "/home/${DEPLOY_USER}/.ssh"
install -m 0600 -o "${DEPLOY_USER}" -g "${DEPLOY_USER}" "${DEPLOY_PUBKEY_FILE}" "/home/${DEPLOY_USER}/.ssh/authorized_keys"

log "Directories"
# The deploy user owns the release tree (code is not secret). Secrets live in root-owned files
# readable only by the service group; backups and operational scripts sit outside the tree.
install -d -m 0755 -o "${DEPLOY_USER}" -g "${DEPLOY_USER}" "${APP_ROOT}" "${APP_ROOT}/releases" "${APP_ROOT}/shared/venvs"
install -d -m 0755 -o root -g root "${APP_ROOT}/shared"
chown "${DEPLOY_USER}:${DEPLOY_USER}" "${APP_ROOT}/shared/venvs"
install -d -m 0750 -o "${APP_USER}" -g "${APP_USER}" "${APP_ROOT}/shared/home" "${APP_ROOT}/shared/data"
install -d -m 0755 -o "${APP_USER}" -g "${APP_USER}" "${APP_ROOT}/shared/next-cache"
install -d -m 2775 -o "${DEPLOY_USER}" -g "${APP_USER}" "${APP_ROOT}/shared/logs"
install -d -m 0700 -o postgres -g postgres "${BACKUP_DIR}"
ln -sfn "${BACKUP_DIR}" "${APP_ROOT}/shared/backups"
install -d -m 0755 "${TOOLS}"
for script in backup_db.sh restore_check.sh restore_db.sh healthcheck.sh rollback.sh; do
  install -m 0755 "${HERE}/../scripts/${script}" "${TOOLS}/${script}"
done
ln -sfn "${TOOLS}" "${APP_ROOT}/bin"

log "PostgreSQL role and database (created only if missing)"
ENV_FILE="${APP_ROOT}/shared/backend.env"
DB_PASSWORD=""
if ! sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='${DB_ROLE}'" | grep -q 1; then
  DB_PASSWORD="$(openssl rand -hex 24)"
  # NOSUPERUSER: production startup refuses a superuser connection.
  # Read from stdin so psql interpolates :'pw' (it does not inside -c), keeping the password
  # out of the process list.
  printf '%s\n' "CREATE ROLE ${DB_ROLE} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD :'pw';" \
    | sudo -u postgres psql -v ON_ERROR_STOP=1 -q -v pw="${DB_PASSWORD}" >/dev/null
  echo "Created role ${DB_ROLE}."
else
  echo "Role ${DB_ROLE} already exists; left unchanged."
fi
if ! sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='${DB_NAME}'" | grep -q 1; then
  sudo -u postgres createdb --owner="${DB_ROLE}" "${DB_NAME}"
  echo "Created database ${DB_NAME}."
else
  echo "Database ${DB_NAME} already exists; left unchanged."
fi
# PostgreSQL listens on localhost only (the package default); confirm rather than assume.
sudo -u postgres psql -tAc "SHOW listen_addresses" | grep -qx "localhost" || echo "WARNING: PostgreSQL listen_addresses is not 'localhost'. Review it."

log "Environment files (created only if missing; never overwritten)"
if [[ ! -f "${ENV_FILE}" ]]; then
  [[ -n "${DB_PASSWORD}" ]] || die "${ENV_FILE} is missing but role ${DB_ROLE} already existed, so its password is unknown. Create ${ENV_FILE} by hand from backend.env.example."
  umask 077
  sed -e "s|__DATABASE_URL__|postgresql+psycopg://${DB_ROLE}:${DB_PASSWORD}@127.0.0.1:5432/${DB_NAME}|" \
      -e "s|__JWT_SECRET__|$(openssl rand -hex 32)|" \
      "${HERE}/backend.env.example" >"${ENV_FILE}"
  umask 022
  echo "Created ${ENV_FILE} with a generated database password and JWT secret. Fill in the CHANGE_ME values."
else
  echo "${ENV_FILE} exists; left unchanged."
fi
[[ -f "${APP_ROOT}/shared/frontend.env" ]] || install -m 0640 "${HERE}/frontend.env.example" "${APP_ROOT}/shared/frontend.env"
chown root:"${APP_USER}" "${ENV_FILE}" "${APP_ROOT}/shared/frontend.env"
chmod 0640 "${ENV_FILE}" "${APP_ROOT}/shared/frontend.env"

log "systemd services and backup timer"
install -m 0644 "${HERE}"/systemd/*.service "${HERE}"/systemd/*.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable ryze-backend.service ryze-frontend.service >/dev/null
systemctl enable --now ryze-db-backup.timer >/dev/null

log "Deploy user may restart only the two app services"
SUDOERS=/etc/sudoers.d/ryze-deploy
cat >"${SUDOERS}.tmp" <<EOF
${DEPLOY_USER} ALL=(root) NOPASSWD: /usr/bin/systemctl restart ryze-backend.service, /usr/bin/systemctl restart ryze-frontend.service
EOF
visudo -cf "${SUDOERS}.tmp" >/dev/null && install -m 0440 "${SUDOERS}.tmp" "${SUDOERS}"
rm -f "${SUDOERS}.tmp"

log "Caddy"
install -m 0644 "${HERE}/Caddyfile" /etc/caddy/Caddyfile
install -d -m 0755 -o caddy -g caddy /var/log/caddy
caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
systemctl enable caddy >/dev/null
systemctl reload caddy || systemctl restart caddy

log "Logs"
install -m 0644 "${HERE}/logrotate-ryze" /etc/logrotate.d/ryze
install -d /etc/systemd/journald.conf.d
printf '[Journal]\nSystemMaxUse=500M\n' >/etc/systemd/journald.conf.d/ryze.conf
systemctl restart systemd-journald

log "Firewall: allow only SSH, HTTP and HTTPS from outside"
# Oracle's Ubuntu images ship iptables rules ending in a REJECT; open 80/443 just before it.
# Oracle advises against ufw on its images. Internal ports stay bound to 127.0.0.1 regardless.
for table in iptables ip6tables; do
  for port in 80 443; do
    if ! ${table} -C INPUT -p tcp --dport "${port}" -m state --state NEW -j ACCEPT 2>/dev/null; then
      reject_line="$(${table} -L INPUT --line-numbers -n | awk '$2=="REJECT"{print $1; exit}')"
      if [[ -n "${reject_line}" ]]; then
        ${table} -I INPUT "${reject_line}" -p tcp --dport "${port}" -m state --state NEW -j ACCEPT
      else
        ${table} -A INPUT -p tcp --dport "${port}" -m state --state NEW -j ACCEPT
      fi
    fi
  done
done
netfilter-persistent save >/dev/null

log "SSH: keys only, no root login, named users only"
cat >/etc/ssh/sshd_config.d/60-ryze.conf <<EOF
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
MaxAuthTries 3
AllowUsers ${ADMIN_USER} ${DEPLOY_USER}
EOF
sshd -t
systemctl reload ssh
systemctl enable --now fail2ban >/dev/null

log "Done"
cat <<EOF
Next:
  1. Edit ${ENV_FILE} (sudo nano) and replace every CHANGE_ME value. Do not paste secrets into chat.
  2. Keep this SSH session open and confirm a NEW session as ${ADMIN_USER} still works.
  3. Note this host key fingerprint; you will compare it when creating PRODUCTION_KNOWN_HOSTS:
EOF
ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub
cat <<EOF
  4. Run the "Deploy production" workflow in GitHub Actions.
EOF
