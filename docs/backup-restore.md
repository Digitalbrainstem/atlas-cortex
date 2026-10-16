# Atlas Cortex — Backup & Restore

> **Historical design, not the current command reference.** The code examples,
> automatic rollback, tiered retention, `--dry-run`, and `--latest any` described
> below are not supported release guarantees. Use [MVP recovery](mvp-recovery.md)
> for implemented backup/restore commands, offline ownership, WAL handling and
> compatible rollback. Do not copy the historical file-replacement examples into
> a running deployment.

## Overview

Atlas Cortex's entire state lives in two places: a **SQLite database** and a **ChromaDB directory**. Backups are simple, fast, and restorable with a single command. The system performs automated nightly backups and supports on-demand snapshots before risky operations.

---

## What Gets Backed Up

| Component | Path | Contains | Size Estimate |
|-----------|------|----------|---------------|
| **cortex.db** | `/data/cortex.db` | All structured data (29 tables) | 10-500 MB |
| **ChromaDB** | `/data/cortex_chroma/` | Memory + knowledge embeddings | 50-500 MB |
| **Avatar skins** | `/data/skins/` | Custom avatar assets | 1-50 MB |
| **Config** | `/data/cortex.env` | Feature flags, API keys, settings | <1 KB |

**Total backup size**: typically 100MB–1GB, compresses well (~60-70% reduction with gzip).

---

## Backup Strategy

### Automated Nightly Backup

Runs as part of the nightly evolution job (3 AM), **before** any evolution changes are applied.

```
Nightly job flow:
  1. Create backup snapshot ← FIRST, before anything else
  2. Run device discovery
  3. Run fallthrough analysis
  4. Run emotional evolution
  5. Run pattern optimization
  6. If anything in steps 2-5 fails → restore from step 1's backup
```

### Retention Policy

```
/data/backups/
├── daily/
│   ├── cortex-2026-02-27.tar.gz      ← last 7 days
│   ├── cortex-2026-02-26.tar.gz
│   ├── cortex-2026-02-25.tar.gz
│   └── ...
├── weekly/
│   ├── cortex-2026-W09.tar.gz        ← last 4 weeks
│   ├── cortex-2026-W08.tar.gz
│   └── ...
├── monthly/
│   ├── cortex-2026-02.tar.gz         ← last 12 months
│   ├── cortex-2026-01.tar.gz
│   └── ...
└── manual/
    ├── cortex-pre-upgrade-2026-02-27.tar.gz  ← on-demand snapshots
    └── ...
```

| Tier | Frequency | Retention | Trigger |
|------|-----------|-----------|---------|
| Daily | Every night (3 AM) | 7 days | Nightly cron |
| Weekly | Sundays | 4 weeks | Nightly cron (if Sunday) |
| Monthly | 1st of month | 12 months | Nightly cron (if 1st) |
| Manual | On demand | Never auto-deleted | User or pre-upgrade |

### Storage Location

Primary: `/data/backups/` on the same volume (fast, always available)
Secondary (recommended): copy to NAS share via rsync after backup completes

```bash
# Optional: sync to NAS after nightly backup
rsync -a /data/backups/ /mnt/nas/backups/atlas-cortex/
```

---

## Backup Process

### How It Works

SQLite supports **online backup** — the database doesn't need to be stopped. ChromaDB uses SQLite internally, so same approach.

```python
import sqlite3
import shutil
import tarfile
import os
from datetime import datetime

def create_backup(backup_type='daily', label=None):
    """Create a complete backup of all Cortex data."""
    
    timestamp = datetime.now().strftime('%Y-%m-%d_%H%M%S')
    name = label or f"cortex-{timestamp}"
    backup_dir = f"/data/backups/{backup_type}"
    os.makedirs(backup_dir, exist_ok=True)
    
    temp_dir = f"/tmp/cortex-backup-{timestamp}"
    os.makedirs(temp_dir)
    
    try:
        # 1. SQLite online backup (consistent snapshot, no locks)
        src = sqlite3.connect('/data/cortex.db')
        dst = sqlite3.connect(f'{temp_dir}/cortex.db')
        src.backup(dst)
        dst.close()
        src.close()
        
        # 2. ChromaDB directory copy (also SQLite-backed)
        shutil.copytree('/data/cortex_chroma', f'{temp_dir}/cortex_chroma')
        
        # 3. Config file
        shutil.copy2('/data/cortex.env', f'{temp_dir}/cortex.env')
        
        # 4. Avatar skins (if they exist)
        if os.path.exists('/data/skins'):
            shutil.copytree('/data/skins', f'{temp_dir}/skins')
        
        # 5. Compress
        archive_path = f"{backup_dir}/{name}.tar.gz"
        with tarfile.open(archive_path, 'w:gz') as tar:
            tar.add(temp_dir, arcname=name)
        
        # 6. Record in backup log
        log_backup(archive_path, backup_type, os.path.getsize(archive_path))
        
        return archive_path
    
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def enforce_retention():
    """Delete backups older than retention policy."""
    
    retention = {
        'daily': 7,        # days
        'weekly': 28,       # days (4 weeks)
        'monthly': 365,     # days (12 months)
        'manual': None,     # never auto-delete
    }
    
    for tier, max_age_days in retention.items():
        if max_age_days is None:
            continue
        backup_dir = f"/data/backups/{tier}"
        if not os.path.exists(backup_dir):
            continue
        cutoff = datetime.now().timestamp() - (max_age_days * 86400)
        for f in os.listdir(backup_dir):
            path = os.path.join(backup_dir, f)
            if os.path.getmtime(path) < cutoff:
                os.remove(path)
```

### Backup Log Table

```sql
CREATE TABLE backup_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    archive_path TEXT NOT NULL,
    backup_type TEXT NOT NULL,          -- 'daily' | 'weekly' | 'monthly' | 'manual'
    size_bytes INTEGER,
    db_row_count INTEGER,              -- total rows across all tables at backup time
    chroma_doc_count INTEGER,          -- total docs in ChromaDB at backup time
    duration_ms INTEGER,
    success BOOLEAN DEFAULT TRUE,
    error_message TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

---

## Restore Process

### One-Command Restore

```bash
# Restore from a specific backup
python -m cortex.backup restore /data/backups/daily/cortex-2026-02-27_030000.tar.gz

# Restore from the latest daily backup
python -m cortex.backup restore --latest daily

# Restore from the latest backup of any type
python -m cortex.backup restore --latest any

# Dry run (show what would be restored without doing it)
python -m cortex.backup restore --dry-run /data/backups/daily/cortex-2026-02-27_030000.tar.gz
```

### Restore Flow

```python
def restore_backup(archive_path, dry_run=False):
    """Restore Cortex from a backup archive."""
    
    print(f"Restoring from: {archive_path}")
    
    # 1. Verify archive integrity
    with tarfile.open(archive_path, 'r:gz') as tar:
        members = tar.getnames()
        print(f"  Archive contains: {len(members)} files")
        
        # Verify expected files exist
        has_db = any('cortex.db' in m for m in members)
        has_chroma = any('cortex_chroma' in m for m in members)
        has_config = any('cortex.env' in m for m in members)
        print(f"  Database: {'✓' if has_db else '✗'}")
        print(f"  ChromaDB: {'✓' if has_chroma else '✗'}")
        print(f"  Config:   {'✓' if has_config else '✗'}")
    
    if dry_run:
        print("  Dry run — no changes made.")
        return
    
    # 2. Create a safety backup of current state BEFORE restoring
    print("  Creating safety backup of current state...")
    safety = create_backup('manual', label='pre-restore-safety')
    print(f"  Safety backup: {safety}")
    
    # 3. Extract to temp directory
    temp_dir = f"/tmp/cortex-restore-{datetime.now().strftime('%s')}"
    with tarfile.open(archive_path, 'r:gz') as tar:
        tar.extractall(temp_dir)
    
    # 4. Find the extracted content (one level deep)
    content_dir = os.path.join(temp_dir, os.listdir(temp_dir)[0])
    
    # 5. Replace current data
    #    a. Database
    shutil.copy2(f'{content_dir}/cortex.db', '/data/cortex.db')
    
    #    b. ChromaDB
    if os.path.exists('/data/cortex_chroma'):
        shutil.rmtree('/data/cortex_chroma')
    shutil.copytree(f'{content_dir}/cortex_chroma', '/data/cortex_chroma')
    
    #    c. Config
    if os.path.exists(f'{content_dir}/cortex.env'):
        shutil.copy2(f'{content_dir}/cortex.env', '/data/cortex.env')
    
    #    d. Skins
    if os.path.exists(f'{content_dir}/skins'):
        if os.path.exists('/data/skins'):
            shutil.rmtree('/data/skins')
        shutil.copytree(f'{content_dir}/skins', '/data/skins')
    
    # 6. Cleanup
    shutil.rmtree(temp_dir)
    
    print("  ✓ Restore complete. Restart Cortex to apply.")
    print(f"  Safety backup at: {safety} (delete when satisfied)")
```

---

## Voice Commands

Atlas can manage its own backups via voice or chat:

```
"Atlas, back yourself up"
→ create_backup('manual', label='user-requested')
→ "Done — backup saved. 247MB, 142,000 records."

"Atlas, restore from yesterday's backup"
→ restore_backup(latest daily from yesterday)
→ "Restoring from yesterday's backup. I'll need to restart — 
   give me 10 seconds... ✓ All good, I'm back."

"Atlas, how big are your backups?"
→ SELECT backup_type, COUNT(*), SUM(size_bytes) FROM backup_log GROUP BY backup_type
→ "I've got 7 daily backups (1.2GB total), 4 weekly (680MB), 
   and 3 monthly (510MB). Storage looks fine."

"Atlas, when was the last backup?"
→ SELECT * FROM backup_log ORDER BY created_at DESC LIMIT 1
→ "Last backup was this morning at 3:01 AM. 
   Took 4 seconds, 253MB, everything clean."
```

---

## Pre-Operation Safety Backups

Automatic safety snapshots before risky operations:

| Operation | Backup Label | Trigger |
|-----------|-------------|---------|
| Nightly evolution job | `pre-evolution-YYYY-MM-DD` | Start of nightly job |
| Schema migration | `pre-migration-vX-to-vY` | Before any ALTER TABLE |
| Bulk pattern import | `pre-pattern-import` | Before batch insert |
| User profile merge | `pre-profile-merge` | Before linking profiles |
| Container upgrade | `pre-upgrade-VERSION` | Before Open WebUI update |

These go in `/data/backups/manual/` and are never auto-deleted.

---

## Disaster Recovery Scenarios

### Scenario 1: Corrupted Database
```
Symptom: Atlas throws SQLite errors, can't respond
Fix: python -m cortex.backup restore --latest daily
Time: ~10 seconds
Data loss: up to 24 hours of interactions
```

### Scenario 2: Bad Evolution Job
```
Symptom: Nightly job introduced broken patterns, Atlas misroutes commands
Fix: python -m cortex.backup restore /data/backups/manual/pre-evolution-2026-02-27.tar.gz
Time: ~10 seconds
Data loss: zero (restoring to just before the bad job)
```

### Scenario 3: Container Destroyed
```
Symptom: Docker container deleted, need to rebuild
Fix:
  1. Deploy new container with /data volume mounted
  2. python -m cortex.backup restore --latest any
  3. Restart
Time: ~2 minutes
Data loss: depends on backup freshness
```

### Scenario 4: Full Server Failure
```
Symptom: Overwatch server is dead, rebuilding from scratch
Fix:
  1. Rebuild server, deploy Ollama + Open WebUI + Cortex
  2. Copy backups from NAS: rsync from /mnt/nas/backups/atlas-cortex/
  3. python -m cortex.backup restore --latest any
  4. Pull Ollama models (qwen3:30b-a3b, qwen2.5:14b, nomic-embed-text)
  5. Restart everything
Time: ~30 minutes (mostly model downloads)
Data loss: depends on NAS backup freshness
```

---

## Backup Health Monitoring

The nightly job checks backup health and alerts if something is wrong:

```python
def check_backup_health():
    """Run during nightly job — returns warnings."""
    warnings = []
    
    # Last successful backup age
    latest = query("SELECT created_at FROM backup_log WHERE success=TRUE ORDER BY created_at DESC LIMIT 1")
    if not latest or (now() - latest) > timedelta(hours=36):
        warnings.append("⚠️ No successful backup in the last 36 hours!")
    
    # Backup size trend (sudden drop might mean corruption)
    sizes = query("SELECT size_bytes FROM backup_log WHERE backup_type='daily' ORDER BY created_at DESC LIMIT 3")
    if len(sizes) >= 2 and sizes[0] < sizes[1] * 0.5:
        warnings.append("⚠️ Latest backup is less than half the size of the previous one.")
    
    # Disk space check
    usage = shutil.disk_usage('/data/backups')
    if usage.free < 1_000_000_000:  # less than 1GB free
        warnings.append("⚠️ Backup volume has less than 1GB free.")
    
    # Verify latest backup is readable
    latest_path = query("SELECT archive_path FROM backup_log WHERE success=TRUE ORDER BY created_at DESC LIMIT 1")
    if latest_path and not tarfile.is_tarfile(latest_path):
        warnings.append("⚠️ Latest backup archive is corrupted!")
    
    return warnings
```

If warnings exist, Atlas mentions them proactively:
```
"Hey Derek, heads up — my last backup is 2 days old. 
 Something might be wrong with the nightly job. Want me to 
 run a backup now?"
```

---

## Integration

### Phase C2 (Nightly Evolution)
- Backup runs as step 1 of the nightly job
- Retention enforcement runs after backup
- Backup health check runs at end of nightly job

### Phase C1 (Core Pipe)
- Manual backup/restore commands handled as Layer 2 (direct execution)
- Backup status queries handled as Layer 1 (instant, from backup_log)

### Infrastructure
- Backup volume should be on a separate disk from the main database if possible
- NAS rsync recommended for off-server copies
- Monitor backup_log for failures via evolution_log notes
