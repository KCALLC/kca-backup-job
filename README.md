# kca-backup-job

Build context for the KCA data platform nightly Postgres backup job (DigitalOcean App Platform scheduled job).
Mirror of `ops/backup/` and `keys/age-backup.pub` from the private `KCALLC/kca-data-platform` repo; kept public
only because App Platform's public-git source needs no GitHub App installation. Contains no secrets:
all connection and B2 credentials are injected as encrypted App Platform environment variables, and
`keys/age-backup.pub` is an age *public* key (backups are only decryptable with the offline private key).
