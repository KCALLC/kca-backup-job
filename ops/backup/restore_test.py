#!/usr/bin/env python3
"""Restore drill: download the newest B2 backup of each database, decrypt with the
age private key, pg_restore into a throwaway database on the same cluster, compare
table and row counts with the live database, then drop the throwaway database.

Run from an operator workstation (needs psql/pg_restore >= server major, age, b2sdk):
  B2 key file: ~/.openclaw/credentials/kca-platform/b2-key-backups-writer.json
  PG admin:    ~/.openclaw/credentials/kca-platform/pg-admin.json
  age key:     ~/.openclaw/credentials/kca-platform/age-backup.key
Exit non-zero on any mismatch.
"""
import json, os, subprocess, sys, tempfile, time
from datetime import date
from b2sdk.v2 import B2Api, InMemoryAccountInfo

H = os.path.expanduser
B2KEY = json.load(open(H('~/.openclaw/credentials/kca-platform/b2-key-backups-writer.json')))
PG = json.load(open(H('~/.openclaw/credentials/kca-platform/pg-admin.json')))
AGEKEY = H('~/.openclaw/credentials/kca-platform/age-backup.key')
ENV = dict(os.environ, PGPASSWORD=PG['password'], PGSSLMODE='require')
CONN = ['-h', PG['host'], '-p', str(PG['port']), '-U', PG['user']]

def psql(db, sql):
    r = subprocess.run(['psql', *CONN, '-d', db, '-X', '-q', '-t', '-A', '-v', 'ON_ERROR_STOP=1', '-c', sql], env=ENV, capture_output=True, text=True)
    if r.returncode: sys.exit(f'psql failed on {db}: {r.stderr.strip()[:400]}')
    return r.stdout.strip()

def counts(db):
    rows = psql(db, "SELECT c.relname, c.reltuples::bigint FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind='r' ORDER BY 1")
    out = {}
    for line in rows.splitlines():
        t = line.split('|')[0]
        out[t] = int(psql(db, f'SELECT count(*) FROM public."{t}"'))
    return out

def main():
    info = InMemoryAccountInfo(); b2 = B2Api(info)
    b2.authorize_account('production', B2KEY['keyID'], B2KEY['applicationKey'])
    bucket = b2.get_bucket_by_name(B2KEY['bucket'])
    dbs = [d.strip() for d in (sys.argv[1] if len(sys.argv) > 1 else 'springs,gorman').split(',')]
    failed = 0
    with tempfile.TemporaryDirectory() as tmp:
        for db in dbs:
            t0 = time.time()
            versions = [fv for fv, _ in bucket.ls(f'pg/{db}/', latest_only=True)]
            if not versions: print(f'{db}: NO BACKUP FOUND'); failed += 1; continue
            fv = max(versions, key=lambda v: v.file_name)
            enc = os.path.join(tmp, os.path.basename(fv.file_name)); dump = enc[:-4]
            bucket.download_file_by_id(fv.id_).save_to(enc)
            t1 = time.time()
            r = subprocess.run(['age', '-d', '-i', AGEKEY, '-o', dump, enc], capture_output=True, text=True)
            if r.returncode: print(f'{db}: DECRYPT FAILED {r.stderr}'); failed += 1; continue
            t2 = time.time()
            target = f"restore_test_{date.today():%Y%m%d}_{db}"
            psql('defaultdb', f'DROP DATABASE IF EXISTS {target}')
            psql('defaultdb', f'CREATE DATABASE {target}')
            r = subprocess.run(['pg_restore', *CONN, '-d', target, '--no-owner', '--no-privileges', '--exit-on-error', dump], env=ENV, capture_output=True, text=True)
            t3 = time.time()
            if r.returncode: print(f'{db}: PG_RESTORE FAILED {r.stderr[-600:]}'); failed += 1
            else:
                live, restored = counts(db), counts(target)
                ok = live == restored
                print(f'{db}: {fv.file_name} ({fv.size:,} B) download {t1-t0:.1f}s, decrypt {t2-t1:.1f}s, restore {t3-t2:.1f}s; '
                      f'tables live={len(live)} restored={len(restored)}; rows live={live} restored={restored}; MATCH={ok}')
                if not ok: failed += 1
            psql('defaultdb', f'DROP DATABASE {target}')
            print(f'{db}: dropped {target}; total {time.time()-t0:.1f}s')
    print('RESTORE TEST', 'PASSED' if not failed else f'FAILED ({failed})')
    return 1 if failed else 0

if __name__ == '__main__':
    sys.exit(main())
