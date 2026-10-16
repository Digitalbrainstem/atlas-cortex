# Household MVP recovery

This is the implemented command reference. Restore is an **offline operator
operation**, not a live voice/admin action. Backups contain household information
and potentially credentials: keep them private and encrypt offsite copies.

## Capture

```bash
export CORTEX_DATA_DIR=/absolute/path/to/data
export CORTEX_CONFIG_DIR=/absolute/path/to/config
python3 -m cortex.backup create
python3 -m cortex.backup list
```

The CLI publishes uniquely named, mode-0600 archives in `data/backups/`.
SQLite's online backup API includes committed WAL pages and every table,
including durable turns. `manifest.json` records format version 1, the effective
turn-resource policy, and `CORTEX_BUILD_REVISION` (or `unknown` if unset).
Set that variable to the deployed revision before capture.

Configuration is read from `CORTEX_CONFIG_DIR/cortex.env`; legacy data-directory
and parent-directory locations are recognized when no explicit config directory
is set. Environment variables supplied only by a shell/orchestrator are **not**
all captured: save the private deployment env file, image digest/source revision,
model identifiers and provider settings alongside the archive.

The manual archive also includes `skins/` and `cortex_chroma/` if present.
**Stop their writers for a consistent file-level copy.** SQLite online safety
does not make Chroma's vector/index files collectively atomic.
Links and special files are rejected rather than producing unrestorable archives.

Periodic state snapshots capture SQLite, config, resource-policy manifest and
system-state JSON, not the large external asset/index directories. Snapshot
retention is count-based (`SNAPSHOT_KEEP_COUNT`, default 24); manual CLI archives
are not automatically pruned. `list` displays database log history, which can
be older after restore; also inventory the archive directory.

## Validate in isolation first

```bash
python3 -m cortex.backup restore /absolute/backup.tar.gz \
  --target /absolute/new-isolated-data
```

Use a new private directory on persistent storage. The target's `cortex.env` and
`restored-manifest.json` are written there, never over the active deployment's
configuration. Both CLI backups and periodic snapshots use the same root layout
and can be restored this way.

Before mutation the restorer stages all members, rejects traversal/links/special
files, validates SQLite integrity, and rejects unknown manifest formats. Existing
target databases receive a pre-restore safety archive. Database restoration uses
SQLite backup/checkpoint operations, not a raw overwrite beneath a live WAL.
Old external directories are retained with `.pre_restore-<id>` suffixes rather
than merged with a potentially incompatible restored index.

The server owns `data/.cortex.lock` throughout startup, operation and shutdown.
Restore refuses that data directory while the server owns it. The admin restore
endpoint returns HTTP 409 with offline instructions. Stop **all** other SQLite,
Chroma and asset writers too: this advisory lock cannot control external tools.

## Switch or roll back

1. Record the current code/image revision, private environment and models.
2. Make and inspect a backup; prove isolated restore before changing production.
3. Stop Cortex and all writers. Never deploy a destructive restore remotely
   without operator authorization.
4. Prefer switching the stopped deployment's data mount to the isolated restored
   directory. Load its matching private env file; set the container's internal
   data path back to `/data`. Compare all turn-policy values with the manifest.
5. Start **the matching code/image and configuration** first. For a forward
   upgrade, startup runs the idempotent schema initializer after recovery.
   Restored pending work requires explicit confirmation; uncertain external
   effects require manual verification, not automatic retry.
6. Check `/live`, `/ready`, admin recovery/queue status and user/profile isolation;
   send a harmless text request before reconnecting satellites.
7. Retain the previous directory, config, image and safety archive until sign-off.

For an in-place stopped deployment, the CLI also supports:

```bash
python3 -m cortex.backup restore /absolute/backup.tar.gz
python3 -m cortex.backup restore --latest daily
```

Do not use in-place config restoration through a read-only Docker bind mount;
restore to an isolated target and switch mounts/env files instead. Multi-file
restoration is not one transaction across SQLite, config and external assets.
If any step fails, leave the service stopped, inspect the error, and restore the
safety archive into another isolated directory. Never treat a partial failure as
a successful recovery.

There is no supported arbitrary schema downgrade. Rollback means the **old
code + its pre-upgrade database + its config/models**, not old code against a
newer live database. Format validation is not a promise of cross-version model
or schema compatibility. Physical disk failure, NAS transfer and power-loss
qualification remain operator gates.
