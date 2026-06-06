# Compose Internal Expose Only Design

Date: 2026-06-06
Repo: `synctrades-be`
Branch: `staging`

## Goal

Make the Compose deployment artifacts follow an internal-only networking rule:

- Dockerfiles may keep `EXPOSE`
- Compose files should not publish container ports onto the host

## Decisions

### Networking rule

Compose services should use:

- `expose:` for internal service discovery

Compose services should not use:

- `ports:` host mappings

This matches Dokploy deployment better because external routing is handled outside the compose file.

### Scope

The current change applies to:

- `C:/Users/USER/Documents/SyncTrade/synctrades-be/docker-compose.yml`

Verified during review:

- `mt5-quant-server/docker-compose.yml` already follows the internal-only pattern
- `synctrades-fe` currently has a `Dockerfile` but no compose file to change

## Deliverable

Replace the backend API service host mapping with an internal-only `expose` declaration and keep the rest of the compose topology unchanged.

## Verification

The change is complete when:

- `docker compose config` still renders successfully for `synctrades-be`
- no unwanted `ports:` blocks remain in the touched compose file
