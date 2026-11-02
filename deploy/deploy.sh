#!/usr/bin/env bash
# Run from an unpacked release on the Linux VM, with a registry image@sha256 digest.
set -Eeuo pipefail
umask 077

ROOT=${INTEL_DEPLOY_ROOT:-/opt/intel-sourcerer}
PROJECT=${INTEL_COMPOSE_PROJECT:-intel-sourcerer-prod}
RELEASE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
ARG=${1:?Usage: deploy.sh registry/owner/image@sha256:digest | --backup}
[[ $PROJECT =~ ^[a-z0-9][a-z0-9_-]*$ ]] || { echo 'Invalid Compose project name' >&2; exit 2; }
[[ -f "$ROOT/.env" ]] || { echo "Configure $ROOT/.env first" >&2; exit 2; }
for tool in docker flock curl python3; do command -v "$tool" >/dev/null; done
# The two env files below are authoritative for the image, including during rollback.
unset INTEL_IMAGE
mkdir -p "$ROOT/backups"
exec 9>"$ROOT/deploy.lock"
flock -w 600 9

dc() {
    local release=$1
    shift
    docker compose --project-name "$PROJECT" --env-file "$ROOT/.env" \
        --env-file "$release/image.env" -f "$release/compose.yaml" "$@"
}

previous=
if [[ -L "$ROOT/current" ]]; then previous=$(readlink -f "$ROOT/current"); fi
snapshot=
stopped=0
attempted=0

snapshot_data() {
    snapshot=$(mktemp -d "$ROOT/backups/$(date -u +%Y%m%dT%H%M%SZ)-XXXXXX")
    stopped=1
    dc "$previous" stop --timeout 30 backend
    dc "$previous" cp backend:/app/data "$snapshot/data"
    cp "$previous/image.env" "$snapshot/image.env"
    printf '%s\n' "$previous" > "$snapshot/release"
    echo "Consistent data snapshot: $snapshot"
}

rollback() {
    echo 'Deployment failed; recovering the previous release.' >&2
    if [[ -z $previous ]]; then
        dc "$RELEASE" stop || true
        echo 'First deployment failed. Data was retained; inspect server logs privately.' >&2
        return
    fi
    if (( attempted )); then
        dc "$RELEASE" stop --timeout 30 backend || return 1
        # Preserve anything written during the failed rollout before restoring the snapshot.
        dc "$RELEASE" cp backend:/app/data "$snapshot/failed-data" || return 1
        local old_image
        old_image=$(sed -n 's/^INTEL_IMAGE=//p' "$previous/image.env")
        docker run --rm --network none --user 0 \
            --mount "type=volume,src=${PROJECT}_intel-data,dst=/restore" \
            --mount "type=bind,src=$snapshot/data,dst=/snapshot,readonly" \
            --entrypoint python "$old_image" -c '
import os, shutil
from pathlib import Path
target = Path("/restore")
source = Path("/snapshot")
assert (source / "accounts.sqlite3").is_file(), "Incomplete snapshot; refusing restore"
for item in target.iterdir():
    if item.is_dir() and not item.is_symlink():
        shutil.rmtree(item)
    else:
        item.unlink()
shutil.copytree(source, target, dirs_exist_ok=True)
for directory, folders, files in os.walk(target):
    os.chown(directory, 10001, 10001)
    for name in folders + files:
        os.chown(Path(directory) / name, 10001, 10001, follow_symlinks=False)
' || return 1
    fi
    dc "$previous" up -d --wait --wait-timeout 180 || return 1
    echo 'Previous release recovered. Failed-rollout files remain beside the snapshot.' >&2
}

finish() {
    local result=$?
    trap - EXIT HUP INT TERM
    if (( result != 0 && stopped )); then
        if ! rollback; then
            echo "Automatic recovery failed. Preserve $snapshot and recover manually." >&2
        fi
    fi
    exit "$result"
}
trap finish EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

if [[ $ARG == --backup ]]; then
    [[ -n $previous ]] || { echo 'No successful deployment to back up' >&2; exit 2; }
    snapshot_data
    dc "$previous" up -d --wait --wait-timeout 180
    stopped=0
    exit 0
fi

[[ $ARG =~ ^[a-z0-9.-]+(:[0-9]+)?/[a-z0-9._/-]+@sha256:[a-f0-9]{64}$ ]] || {
    echo 'Deploy an immutable registry/image@sha256:digest' >&2; exit 2;
}
[[ $RELEASE != "$previous" ]] || { echo 'Use a new release directory for each deployment' >&2; exit 2; }
printf 'INTEL_IMAGE=%s\n' "$ARG" > "$RELEASE/image.env"
dc "$RELEASE" config --quiet
# Pull and validate before stopping the existing application.
dc "$RELEASE" pull
read -r domain https_port caddy_image acme_email <<< "$(dc "$RELEASE" config --format json | python3 -c '
import json, sys
caddy = json.load(sys.stdin)["services"]["caddy"]
port = next(p["published"] for p in caddy["ports"] if p["target"] == 443)
print(caddy["environment"]["DOMAIN"], port, caddy["image"], caddy["environment"]["ACME_EMAIL"])
')"
[[ $domain =~ ^[a-zA-Z0-9][a-zA-Z0-9.-]*[a-zA-Z0-9]$ && $https_port =~ ^[0-9]+$ ]] || {
    echo 'DOMAIN must be a hostname, without a URL scheme or path' >&2; exit 2;
}
# Compose run would reuse Caddy's fixed IP while the old proxy is still running.
docker run --rm --network none --env "DOMAIN=$domain" --env "ACME_EMAIL=$acme_email" \
    --mount "type=bind,src=$RELEASE/Caddyfile,dst=/etc/caddy/Caddyfile,readonly" \
    "$caddy_image" caddy validate --config /etc/caddy/Caddyfile
if [[ -n $previous ]]; then snapshot_data; else stopped=1; fi
attempted=1
dc "$RELEASE" up -d --wait --wait-timeout 180
curl --fail --silent --show-error --retry 6 --retry-all-errors --retry-delay 2 \
    --connect-timeout 3 --max-time 10 --resolve "$domain:$https_port:127.0.0.1" \
    "https://$domain:$https_port/health" | python3 -c '
import json, sys
health = json.load(sys.stdin)
assert health.get("status") == "ok" and health.get("service") == "combined"
'
if [[ -n $previous ]]; then
    ln -sfn "$previous" "$ROOT/previous.next"
    mv -Tf "$ROOT/previous.next" "$ROOT/previous"
fi
ln -sfn "$RELEASE" "$ROOT/current.next"
mv -Tf "$ROOT/current.next" "$ROOT/current"
stopped=0
echo "Healthy deployment: $ARG"
