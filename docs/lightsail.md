# Lightsail, GitHub Actions, and GHCR

The repository is prepared for one Ubuntu Lightsail VM running Docker Compose.
GitHub builds the image; the VM only pulls and runs it. SQLite, accounts, uploads,
and source settings stay in the production `intel-data` volume at `/app/data`.
Caddy runs as a second container and provides HTTPS. There is no database server.

## Delivery behavior

`.github/workflows/delivery.yaml` runs on pull requests, pushes to `main`, and
manual runs. Pull requests run Python tests, browser-script tests, a Docker build,
and an isolated production deployment test. The Linux deployment test covers real
HTTPS using a local CA, consistent backups, persistence, and failed-release recovery.

After successful main-branch checks, Actions publishes
`ghcr.io/muratovm/intel-sourcerer:sha-<commit>` and deploys its **immutable digest**.
Publishing uses the workflow's `GITHUB_TOKEN`. Deployment stays disabled until the
repository variable `DEPLOY_ENABLED` equals `true`.

Main runs are serialized. The production environment can add an approval gate.
The deploy job uploads the reviewed deployment files over SSH with strict host-key
verification. It never uploads your local `.env` or data directory. The production
Compose file is separate from the local stack and publishes only ports 80 and 443.

## 1. Create the server and domain

1. Create an **Ubuntu 24.04 LTS, x86-64 Lightsail instance with 2 GB RAM**. Images
   published by this workflow target `linux/amd64`.
2. Attach a Lightsail static public IPv4 address. Point your hostname's DNS A record
   at it. Remove any AAAA record unless you also configure working IPv6 access.
3. Allow TCP 80 and 443 in the Lightsail firewall. SSH on TCP 22 must be reachable
   by your deployment runner and administrator. Standard GitHub-hosted runners have
   changing source IPs; use a runner with fixed egress if you want an SSH IP allowlist.
4. Install Docker Engine and the Compose plugin using the
   [official Ubuntu instructions](https://docs.docker.com/engine/install/ubuntu/).
   Install `python3`, `curl`, and `util-linux` (for `flock`) if absent.
5. Give the deployment user Docker access and prepare the private deployment folder:

```sh
# On the VM; replace ubuntu if you use a different deployment account.
sudo usermod -aG docker ubuntu
sudo install -d -m 700 -o ubuntu -g ubuntu /opt/intel-sourcerer
```

Sign out and back in, then verify `docker version` and `docker compose version`.
As the deployment user:

```sh
mkdir -p /opt/intel-sourcerer/incoming /opt/intel-sourcerer/releases /opt/intel-sourcerer/backups
```

Copy `deploy/.env.example` to `/opt/intel-sourcerer/.env` on the VM, replace the
hostname and certificate email, and set its permissions to `600`. Guest reading
defaults to off. You can set a strong `INTEL_SETUP_TOKEN`, or leave it empty to
generate a code in the application log. Never put the real `.env` in GitHub.

The production network reserves `172.30.87.0/24`. Caddy uses `.2`, which is the
only trusted forwarding peer; the backend uses `.3`. If this subnet conflicts
with another network, change all three addresses together in `deploy/compose.yaml`
and run the integration test before deploying.

Docker access is equivalent to root access. Treat the deployment key and everyone
allowed to change the deployment workflow as trusted server administrators.

## 2. Configure GitHub and registry access

Create a GitHub environment named **production**. Restrict it to `main`, and add
required reviewers if supported by your GitHub plan and desired. Private-repository
environment support depends on your GitHub plan; see
[GitHub environment availability](https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/manage-environments).

Add these **production environment secrets**:

| Secret | Value |
| --- | --- |
| `DEPLOY_HOST` | Static IPv4 address or SSH hostname, without a scheme or port |
| `DEPLOY_USER` | Linux deployment username, usually `ubuntu` |
| `DEPLOY_SSH_KEY` | Dedicated SSH private key whose public key is authorized on the VM |
| `DEPLOY_KNOWN_HOSTS` | Verified OpenSSH known-hosts line for exactly `DEPLOY_HOST` |

Use a dedicated deployment key. Its authorized-keys entry can begin with `restrict`
to disable forwarding and PTY access while retaining the command/SFTP access the
workflow needs. Store the private key only in GitHub's environment secret.

Obtain the server's public host key through the trusted Lightsail console, not by
accepting a key from the network during CI. For example, on the VM console:

```sh
awk -v host=203.0.113.10 '{print host, $1, $2}' /etc/ssh/ssh_host_ed25519_key.pub
```

Replace the example IP with `DEPLOY_HOST` and save that output as
`DEPLOY_KNOWN_HOSTS`.

Commit and push the preparation files while `DEPLOY_ENABLED` remains unset. This
runs CI and publishes the first main-branch image without contacting the VM.
If an existing GHCR package rejects publication, grant this repository Actions
access to that package.

For a private GHCR image, authenticate **on the VM as the deployment user** using
a GitHub personal access token (classic) with `read:packages` and access to the
package. Send it through `docker login ghcr.io --username YOUR_GITHUB_USERNAME
--password-stdin`. Do not put the token in a command argument or the deployment
bundle. The VM retains its registry login independently of CI. For a public image,
anonymous pulls work and no server-side token is needed. See
[GHCR authentication](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry).

Finally add a **repository Actions variable** `DEPLOY_ENABLED=true` and manually
run **CI and delivery** on `main`. Later merges to `main` deploy automatically.
Setting the variable back to `false` disables deployment while preserving CI and
image publication.

## 3. First deployment and normal operation

The deployer validates Compose and Caddy, pulls images, starts the stack, waits for
the backend health check, and requests the public hostname over HTTPS with full
certificate validation. DNS and ports 80/443 must already work for certificate
issuance. Certificate state persists in dedicated Caddy volumes.

Open your HTTPS hostname. Find the first-account code privately on the VM:

```sh
cd /opt/intel-sourcerer
docker compose -p intel-sourcerer-prod --env-file .env --env-file current/image.env \
  -f current/compose.yaml logs backend
```

Create the administrator account before sharing the site. CI deliberately avoids
dumping application logs because they can contain this one-time code. Additional
users are created by the administrator; public account self-registration is not
implemented.

Each release is stored under `/opt/intel-sourcerer/releases/`. `current` points to
the most recent healthy release, and `previous` to the release before it. The data
volume is always `intel-sourcerer-prod_intel-data`; keep the project name and
`/app/data` mount unchanged. Local Docker data is not migrated automatically.

## Backups and recovery

Every update pulls and validates before stopping the old backend, then copies its
**entire stopped data directory** into a private timestamped directory under
`backups/`. This includes all users and SQLite sidecars. Container replacement
causes a brief interruption; backups of large media libraries take longer.

If startup or the HTTPS health check fails, the deployer stops the candidate,
preserves its data as `failed-data`, restores the pre-deploy snapshot and previous
image/configuration, and leaves the GitHub job failed. Restoring the data also
reverses schema migrations. Any writes accepted during a failed rollout are kept
in `failed-data` for manual recovery, not merged into the restored library.

The first deployment has no previous release to restore: failures stop the stack
and retain its volume. If automatic recovery itself fails, the log names the
snapshot to preserve. Do not delete the volume; stop the backend and restore that
snapshot using the Docker recovery steps in [hosting.md](hosting.md).

Run a consistent backup between deployments:

```sh
bash /opt/intel-sourcerer/current/deploy.sh --backup
```

For a daily backup, add this to the deployment user's crontab (server-local time):

```cron
15 3 * * * /bin/bash /opt/intel-sourcerer/current/deploy.sh --backup >> /opt/intel-sourcerer/backup.log 2>&1
```

Backups and deployments use the same host lock. Configure off-server copying of
`backups/` to storage you control, and periodically test a restore. Deployment and
daily backups remain on the VM until you copy them elsewhere. Snapshots and old
releases are not pruned automatically; monitor disk space and rotate `backup.log`.

To deliberately deploy an earlier image, prepare a **new** release directory with
the corresponding reviewed deployment files, then run its `deploy.sh` with the
earlier immutable image digest. This makes a fresh snapshot first. An intentional
downgrade still needs a compatible database; use a matching data snapshot when
migrations are not backward compatible.

## Local validation

```sh
uv run --locked pytest -q
node tests/js/temporary-session.cjs
node tests/js/math-rendering.cjs
bash -n deploy/deploy.sh deploy/ci-deploy.sh
docker build -t intel-sourcerer:ci .
python3 scripts/test_deployment.py intel-sourcerer:ci
```

The integration test uses random localhost ports, a local certificate authority,
and disposable volumes. On Linux it also starts a temporary local image registry
to test digest deployments, backups, and actual image/data rollback. It never
uses GHCR credentials, public ACME certificates, or your normal Docker library.
