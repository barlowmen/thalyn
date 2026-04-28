"""Brain SQLite migrations.

Per ADR-0028, the brain owns every SQLite store and schema lives
exclusively in this directory. yoyo-migrations is the runner; each
file matches `NNN_*.py` (Python migration) or `NNN_*.sql` (plain SQL),
applied in numeric order. The runtime entrypoint is
`thalyn_brain.orchestration.storage.apply_pending_migrations`.
"""
