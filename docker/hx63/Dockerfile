# One-shot HX63 migration tooling.
#
# The CRM V2 migration/backup/cutover pipeline stays TypeScript on purpose: its
# plan hash (e613693d...) and reconciliation digest (15dc9882...) are certified
# by the prototype's 416 passing tests, and after cutover it never runs again.
# Re-porting it to Python would discard that evidence to gain nothing.
#
# This is NOT a service. It has no CMD that stays up, it runs under a compose
# profile so `docker compose up` never starts it, and Plan 3 owns its actual
# execution against real data.
FROM node:22-bookworm-slim

WORKDIR /proto

# The prototype repo is bind-mounted read-only at /proto by compose, so nothing
# is copied in at build time; this image only provides the Node runtime.
ENTRYPOINT ["npx", "tsx"]
CMD ["--help"]
