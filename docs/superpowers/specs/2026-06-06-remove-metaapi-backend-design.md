# Remove MetaAPI From Backend Design

Date: 2026-06-06
Repo: `synctrades-be`
Branch: `staging`

## Goal

Remove MetaAPI as a supported backend integration so the backend reflects the current MT5-core-only system contract.

The result should:

- remove `METAAPI_*` environment variables from `.env.example`
- remove MetaAPI configuration and runtime code paths
- make MT5-core the only broker-account sync path
- keep CSV import and manual journal flows intact

## Current Problem

The backend still carries a second sync model based on MetaAPI even though the intended system now runs through the headless MT5 service.

That causes three problems:

- `.env.example` contains provider settings that are no longer wanted in production
- the codebase still branches across two different sync worlds
- comments, defaults, and fallbacks no longer match the real system

## Decisions

### Integration boundary

Broker-connected account sync will be MT5-core-only.

The backend will continue to support:

- `headless_mt5`
- `csv_import`

It will no longer support:

- `metaapi`

### Removal scope

The cleanup will remove both behavior and configuration, not just environment variables.

Expected change areas:

- `src/app/core/config.py`
- `src/app/domains/accounts/metaapi.py`
- `src/app/domains/accounts/sync.py`
- `src/app/domains/accounts/service.py`
- `src/app/domains/accounts/models.py`
- `src/app/domains/accounts/repository.py`
- `src/app/domains/journal/service.py`
- `.env.example`

### Sync behavior after cleanup

Manual and bootstrap broker-account sync will go only through the MT5-core path.

There should be no runtime branch that:

- calls MetaAPI for deal history
- calls MetaAPI for account info
- classifies sync failures as transient MetaAPI failures

### Model and defaults

`headless_mt5` becomes the real default provider in backend models and repository creation paths.

Stale comments referring to MetaAPI-backed trades or MetaAPI-only fallback behavior should be updated or removed where they no longer describe reality.

## Verification

The cleanup is complete when:

- backend tests pass
- `.env.example` contains no `METAAPI_*` variables
- there are no remaining imports or runtime usages of `metaapi.py`
- account connect, manual sync, and journal analytics remain aligned with MT5-core-only behavior

## Non-Goals

This change does not:

- redesign the MT5-core contract
- change frontend behavior
- remove CSV import support
- alter the Dokploy deployment topology beyond env cleanup
