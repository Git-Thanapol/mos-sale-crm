# Deployment

Step-by-step guide for deploying this app to a remote server. First-time
server setup is manual (once); every deploy after that is `scripts/deploy.sh`.

## Prerequisites (remote server)

- Docker Engine + Docker Compose v2 (`docker compose version`)
- Git
- An SSH key with access to the git remote (for `git pull` on the server)
  and to the server itself (for this script to connect)
- Port 8001 (and 443 if you put TLS in front) reachable from wherever users
  connect — nginx listens on 80 inside the container, mapped to host 8001
  since 80 and 8000 are already taken on the deploy target

## 1. First-time server setup (once)

```bash
# On the remote server
git clone https://github.com/Git-Thanapol/mos-sale-crm.git /srv/crm_django
cd /srv/crm_django/crm_django

cp .env.example .env
# Edit .env:
#   DJANGO_SETTINGS_MODULE=config.settings.prod
#   DJANGO_DEBUG=0
#   DJANGO_SECRET_KEY=<generate a real random 50+ char value, never reuse the dev default>
#   DJANGO_ALLOWED_HOSTS=<your real domain/IP>
#   POSTGRES_PASSWORD=<real password, not "change-me">

docker compose -f compose.yaml -f compose.prod.yaml up -d
# web runs migrate + collectstatic automatically on boot (RUN_MIGRATIONS=1,
# see docker/entrypoint.sh) — no extra step needed here.

docker compose -f compose.yaml -f compose.prod.yaml exec web \
  python manage.py seed_users --csv seed/users.csv
docker compose -f compose.yaml -f compose.prod.yaml exec web \
  python manage.py issue_initial_passwords --out /app/seed/reset.csv
# hand-deliver the passwords out of band, then delete the csv (see RUNBOOK.md)
```

## 2. Local machine: point deploy.sh at the server (once)

```bash
cd crm_django
cp scripts/deploy.env.example scripts/deploy.env
```

Edit `scripts/deploy.env` (gitignored — never commit real values):

```bash
DEPLOY_HOST=<server hostname or IP>
DEPLOY_USER=<ssh user>
DEPLOY_PATH=/srv/crm_django/crm_django
DEPLOY_PORT=22
# DEPLOY_SSH_KEY=/path/to/private/key   # optional, if not using ssh-agent/default key
```

## 3. Every deploy after that

```bash
git push                # push your local commits to the git remote first
scripts/deploy.sh
```

`scripts/deploy.sh` does, over SSH, in order:

1. **Backup** — runs `scripts/backup.sh` on the remote (dumps Postgres to
   `backups/` before touching anything).
2. **Pull** — `git fetch --all --prune && git pull --ff-only` on the remote.
   Fails loudly instead of merge-conflicting if the remote tree has diverged.
3. **Build** — `docker compose -f compose.yaml -f compose.prod.yaml build`.
4. **Up** — `... up -d`. Migrations and `collectstatic` run automatically
   inside `web`'s entrypoint on boot — not a separate step.
5. **Health check** — confirms every container is `running`/`healthy`, then
   `curl http://localhost:8001/` on the remote and expects `200` or `302`.

The script refuses to run if your **local** branch has commits not yet
pushed upstream, since the remote server only ever deploys what's on the git
remote — an unpushed local commit would otherwise deploy stale code while
looking successful.

## 4. If something goes wrong

```bash
ssh <user>@<host>
cd /srv/crm_django/crm_django
docker compose -f compose.yaml -f compose.prod.yaml logs -f web
docker compose -f compose.yaml -f compose.prod.yaml ps
```

To roll back code: `git log --oneline`, `git checkout <previous-commit>`,
re-run `docker compose ... build && up -d`.

To restore the database from the pre-deploy backup:
`scripts/restore.sh backups/crm_<timestamp>.sql.gz` — see `RUNBOOK.md`
("Restore") for the full destructive-action warning and confirmation gate.

## Reference

See `docs/RUNBOOK.md` for day-2 operations (backup/restore, adding users,
resetting passwords, re-importing data, reading perf numbers) — this file
covers only the deploy path itself.
