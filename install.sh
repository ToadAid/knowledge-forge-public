#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

usage() {
  cat <<'TXT'
Usage:
  ./install.sh [--check-only] [--prefix PATH] [--state-root PATH] [--manifest PATH]
  ./install.sh --create-protected-accounts --plan
  sudo ./install.sh --create-protected-accounts
  ./install.sh --deploy-protected --reviewer USER --commit SHA --plan
  sudo ./install.sh --deploy-protected --reviewer USER --commit SHA --enable-services
  ./install.sh --migrate-codex-native --reviewer USER --commit SHA --plan
  ./install.sh --migrate-codex-native --reviewer USER --commit SHA --preflight --workspace-root PATH

Default: install ordinary Personal Forge commands and run the read-only helper.
Run default installation as your normal user. No settings or services change.

Account mode: Linux administrator only, from a separate agent-inaccessible
console after reviewing this script. Creates locked, non-login kf-agent and
kf-operator accounts and an empty kf-reviewers group. No passwords are collected.
This mode does not install tools, enable services, add a reviewer, migrate the
agent, approve packages or establish a verified protected deployment.

Deployment mode: independent Linux/systemd administrator only. Creates or
verifies the service accounts, installs pinned code/state/configuration and
system/user units, and adds the explicitly selected human to kf-reviewers.
--enable-services enables the two system sockets. The human starts the desktop
broker after a fresh login. No agent settings or MCP registration are changed.
Service installation is not certification of protected approval.

Native migration: --plan/--check is read-only; --apply requires an independent
interactive root administrator. Installs absent pinned native artifacts only;
never resets existing state. --preflight reports evidence without approval.
--enable-socket refuses: safe Desktop kf-agent transport/provider are unresolved.
TXT
}

if [[ "${1:-}" == -h || "${1:-}" == --help ]]; then
  usage
  exit 0
fi

if [[ "${1:-}" == --deploy-protected ]]; then
  export PATH=/usr/sbin:/usr/bin:/sbin:/bin
  ROOT="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
  shift
  exec /usr/bin/python3 -I -B "$ROOT/scripts/install_protected.py" --source-root "$ROOT" "$@"
fi

if [[ "${1:-}" == --migrate-codex-native ]]; then
  export PATH=/usr/sbin:/usr/bin:/sbin:/bin
  ROOT="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
  shift
  exec /usr/bin/python3 -I -B "$ROOT/scripts/migrate_codex_native.py" --source-root "$ROOT" "$@"
fi

if [[ "${1:-}" != --create-protected-accounts ]]; then
  if [[ "$EUID" == 0 ]]; then
    echo 'Run ordinary installation as your normal user, without sudo.' >&2
    exit 2
  fi
  ROOT="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
  # Delegate argument validation and preserve the existing installer contract.
  "$ROOT/bin/knowledge-forge-install-agent" "$@"
  exec "$ROOT/bin/knowledge-forge-setup" --check-only
fi

shift
plan=false
if [[ "${1:-}" == --plan ]]; then plan=true; shift; fi
if [[ "$#" != 0 ]]; then usage >&2; exit 2; fi
# Resolve administrator commands only through system directories, including uname.
export PATH=/usr/sbin:/usr/bin:/sbin:/bin
if [[ "$(uname -s)" != Linux ]]; then
  echo 'Protected account preparation supports Linux only.' >&2
  exit 2
fi

for tool in getent groupadd useradd id; do
  command -v "$tool" >/dev/null || { echo "Missing prerequisite: $tool" >&2; exit 2; }
done
login_shell=/usr/sbin/nologin
if [[ ! -x "$login_shell" ]]; then login_shell=/sbin/nologin; fi
[[ -x "$login_shell" ]] || { echo 'Missing nologin shell.' >&2; exit 2; }

# Refuse ambiguous existing identities instead of changing their privileges.
for name in kf-agent kf-operator kf-reviewers; do
  for database in passwd group; do
    if getent "$database" "$name" >/dev/null; then
      echo "Existing $database identity $name: independent administrator review required; nothing changed." >&2
      exit 2
    else
      lookup_status=$?
      if [[ "$lookup_status" != 2 ]]; then
        echo "Cannot establish whether $name exists in $database (getent=$lookup_status); nothing changed." >&2
        exit 2
      fi
    fi
  done
done

cat <<TXT
Account preparation plan (no service activation):
  groupadd --system kf-agent
  groupadd --system kf-operator
  groupadd --system kf-reviewers
  useradd --system --gid kf-agent --groups '' --no-create-home --home-dir /nonexistent --shell $login_shell --password '!' kf-agent
  useradd --system --gid kf-operator --groups '' --no-create-home --home-dir /nonexistent --shell $login_shell --password '!' kf-operator

Both passwords are locked; neither account can log in interactively.
The reviewer group starts empty. Select the separate human reviewer manually.
Continue with docs/GENERIC_DAEMON_APPROVAL.md and docs/LINUX_PROTECTED_PROMOTION.md.
Account names alone do not isolate this agent or make approval ready.
TXT
if [[ "$plan" == true ]]; then exit 0; fi
if [[ "$EUID" != 0 ]]; then
  echo 'Administrator privileges required. Review, then run from an independent administrator console.' >&2
  exit 2
fi
if [[ ! -t 0 || ! -t 1 ]]; then
  echo 'An interactive independent administrator console is required.' >&2
  exit 2
fi
echo 'Run only after reviewing this script outside the agent session.'
read -r -p 'Type CREATE FORGE ACCOUNTS to proceed: ' confirmation
if [[ "$confirmation" != 'CREATE FORGE ACCOUNTS' ]]; then
  echo 'Cancelled; nothing changed.'
  exit 1
fi
trap 'echo "Account preparation interrupted or failed. Review existing accounts/groups manually before retrying; no automatic rollback was attempted." >&2' ERR
groupadd --system kf-agent
groupadd --system kf-operator
groupadd --system kf-reviewers
useradd --system --gid kf-agent --groups '' --no-create-home --home-dir /nonexistent --shell "$login_shell" --password '!' kf-agent
useradd --system --gid kf-operator --groups '' --no-create-home --home-dir /nonexistent --shell "$login_shell" --password '!' kf-operator
agent_uid="$(id -u kf-agent)"
operator_uid="$(id -u kf-operator)"
[[ "$agent_uid" != 0 && "$operator_uid" != 0 && "$agent_uid" != "$operator_uid" ]]
echo "Accounts created: kf-agent UID $agent_uid; kf-operator UID $operator_uid."
echo 'PROTECTED SETUP INCOMPLETE. No service enabled, agent migrated, or package approved.'
