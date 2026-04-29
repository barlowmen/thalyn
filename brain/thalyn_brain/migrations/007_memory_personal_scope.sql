-- Migration 007 — rename the legacy ``user`` memory scope to
-- ``personal`` to close the five-tier model.
--
-- v1's MemoryStore accepted ``user|project|agent``. The five-tier
-- model in 01-requirements.md §F6 / 02-architecture.md §5 names the
-- cross-project user-level tier ``personal`` and adds ``episodic``
-- alongside it. This migration renames every existing v1 row in
-- place so the application-level validator can tighten to the new
-- vocabulary in the same release without rejecting carried-forward
-- data.
--
-- Idempotent on its own (a second pass updates zero rows) and
-- composes with yoyo's apply log so it only runs once per database.

UPDATE memory_entries SET scope = 'personal' WHERE scope = 'user';
