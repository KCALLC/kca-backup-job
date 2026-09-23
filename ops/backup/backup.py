#!/usr/bin/env python3
"""Nightly encrypted per-database Postgres backup to Backblaze B2.

For each database in $DATABASES: pg_dump -Fc | age -r <public key> -> upload to
B2 bucket $B2_BUCKET at pg/<db>/<db>-<UTC timestamp>.dump.age, then verify the
uploaded size and SHA-1 against the local file. Any failure exits non-zero.

Environment (set as App Platform SECRET envs, never in git):
  PGHOST PGPORT PGUSER PGPASSWORD  -- admin connection (PGSSLMODE=require)
  DATABASES                        -- comma-separated, e.g. "springs,gorman"
  B2_KEY_ID B2_APP_KEY B2_BUCKET   -- bucket-restricted key (no deleteFiles)
  AGE_RECIPIENT_FILE               -- default /app/age-backup.pub
"""
import hashlib
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone

from b2sdk.v2 import B2Api, InMemoryAccountInfo


def log(msg: str) -> None:
    print(f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] {msg}", flush=True)


def need(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        log(f"FATAL: missing env {name}")
        sys.exit(2)
    return v


def main() -> int:
    t0 = time.time()
    os.environ.setdefault("PGSSLMODE", "require")
    for k in ("PGHOST", "PGPORT", "PGUSER", "PGPASSWORD"):
        need(k)
    databases = [d.strip() for d in need("DATABASES").split(",") if d.strip()]
    recipient_file = os.environ.get("AGE_RECIPIENT_FILE", "/app/age-backup.pub")
    recipient = open(recipient_file).read().strip()
    if not recipient.startswith("age1"):
        log("FATAL: age recipient file does not contain an age public key")
        return 2

    info = InMemoryAccountInfo()
    b2 = B2Api(info)
    b2.authorize_account("production", need("B2_KEY_ID"), need("B2_APP_KEY"))
    bucket = b2.get_bucket_by_name(need("B2_BUCKET"))

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    failures = 0
    with tempfile.TemporaryDirectory() as tmp:
        for db in databases:
            t1 = time.time()
            out = os.path.join(tmp, f"{db}-{stamp}.dump.age")
            log(f"{db}: pg_dump -Fc | age -> {os.path.basename(out)}")
            with open(out, "wb") as fh:
                dump = subprocess.Popen(
                    ["pg_dump", "-Fc", "--no-password", "-d", db],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                )
                enc = subprocess.Popen(
                    ["age", "-r", recipient], stdin=dump.stdout, stdout=fh, stderr=subprocess.PIPE,
                )
                dump.stdout.close()
                enc_err = enc.communicate()[1]
                dump_err = dump.stderr.read()
                dump.wait()
            if dump.returncode != 0 or enc.returncode != 0:
                log(f"{db}: FAILED pg_dump rc={dump.returncode} age rc={enc.returncode}\n"
                    f"{dump_err.decode(errors='replace')[-2000:]}\n{enc_err.decode(errors='replace')[-500:]}")
                failures += 1
                continue
            size = os.path.getsize(out)
            if size < 200:
                log(f"{db}: FAILED suspiciously small output ({size} bytes)")
                failures += 1
                continue
            sha1 = hashlib.sha1(open(out, "rb").read()).hexdigest()
            key = f"pg/{db}/{os.path.basename(out)}"
            log(f"{db}: dump+encrypt {size:,} bytes in {time.time()-t1:.1f}s; uploading {key}")
            fv = bucket.upload_local_file(
                local_file=out, file_name=key, sha1_sum=sha1,
                file_info={"db": db, "pg_dump_format": "custom", "encryption": "age", "utc": stamp},
            )
            remote = b2.get_file_info(fv.id_)
            if remote.size != size or remote.content_sha1 != sha1:
                log(f"{db}: FAILED verification remote size={remote.size} sha1={remote.content_sha1}")
                failures += 1
                continue
            log(f"{db}: OK uploaded fileId={fv.id_} size={size:,} sha1={sha1} ({time.time()-t1:.1f}s)")
    log(f"done: {len(databases)-failures}/{len(databases)} databases backed up in {time.time()-t0:.1f}s")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
