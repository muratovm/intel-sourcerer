#!/usr/bin/env bash
# GitHub runner side. The VM is bootstrapped once; this only uploads a release.
set -Eeuo pipefail
umask 077
: "${DEPLOY_HOST:?Set the production DEPLOY_HOST secret}"
: "${DEPLOY_USER:?Set the production DEPLOY_USER secret}"
: "${DEPLOY_SSH_KEY:?Set the production DEPLOY_SSH_KEY secret}"
: "${DEPLOY_KNOWN_HOSTS:?Set verified SSH host keys in DEPLOY_KNOWN_HOSTS}"
: "${INTEL_IMAGE:?Missing published image digest}"
[[ $DEPLOY_HOST =~ ^[a-zA-Z0-9][a-zA-Z0-9.-]*$ && $DEPLOY_USER =~ ^[a-z_][a-z0-9_-]*$ ]] || {
    echo 'Use a hostname or IPv4 address and a Linux username for deployment' >&2; exit 2;
}
[[ $INTEL_IMAGE =~ ^ghcr\.io/[a-z0-9._/-]+@sha256:[a-f0-9]{64}$ ]] || exit 2
release="${GITHUB_SHA:?}-${GITHUB_RUN_ID:?}-${GITHUB_RUN_ATTEMPT:?}"
[[ $release =~ ^[a-f0-9]{40}-[0-9]+-[0-9]+$ ]] || exit 2
scratch=$(mktemp -d "${RUNNER_TEMP:-/tmp}/intel-deploy-XXXXXX")
trap 'rm -rf -- "$scratch"' EXIT
printf '%s\n' "$DEPLOY_SSH_KEY" > "$scratch/key"
printf '%s\n' "$DEPLOY_KNOWN_HOSTS" > "$scratch/known_hosts"
options=(-i "$scratch/key" -o BatchMode=yes -o IdentitiesOnly=yes
    -o StrictHostKeyChecking=yes -o "UserKnownHostsFile=$scratch/known_hosts"
    -o ConnectTimeout=15 -o ServerAliveInterval=15 -o ServerAliveCountMax=4)
target="$DEPLOY_USER@$DEPLOY_HOST"
tar -C deploy -czf "$scratch/release.tgz" compose.yaml Caddyfile deploy.sh
scp "${options[@]}" "$scratch/release.tgz" "$target:/opt/intel-sourcerer/incoming/$release.tgz"
ssh "${options[@]}" "$target" bash -s -- "$release" "$INTEL_IMAGE" <<'REMOTE'
set -Eeuo pipefail
umask 077
root=/opt/intel-sourcerer
release="$root/releases/$1"
mkdir "$release"
tar -xzf "$root/incoming/$1.tgz" -C "$release"
rm -- "$root/incoming/$1.tgz"
bash "$release/deploy.sh" "$2"
REMOTE
