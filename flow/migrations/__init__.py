"""SQL migrations, one file per version.

Files are named `NNN_description.sql` (zero-padded integer prefix). `db._migrate`
discovers them at startup via `importlib.resources`, sorts by filename, and
applies any whose version is greater than the current `schema_version`.

To add a migration: drop a new `NNN_*.sql` file here. No Python edits needed.
"""
