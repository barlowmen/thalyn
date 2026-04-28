-- Migration 005 — eternal-thread durability surface.
--
-- Lays in the per-turn status column the recovery path keys off, an
-- index that makes the in-progress scan O(K) over the recovery set
-- rather than the whole transcript, and the FTS5 virtual table that
-- backs `thread.search` per ADR-0022.
--
-- The FTS5 table is non-contentless: it carries its own copy of
-- `body` and the `role` / `turn_id` UNINDEXED facets so the index
-- is self-contained and rebuildable by deleting all rows and
-- streaming `thread_turns` back through it. The `porter unicode61`
-- tokenizer handles English stemming + diacritic folding without an
-- ICU dependency.

ALTER TABLE thread_turns ADD COLUMN status TEXT NOT NULL DEFAULT 'completed';

CREATE INDEX IF NOT EXISTS thread_turns_status_idx
    ON thread_turns(thread_id, status);

CREATE VIRTUAL TABLE IF NOT EXISTS thread_turn_index USING fts5(
    turn_id UNINDEXED,
    body,
    role UNINDEXED,
    tokenize = 'porter unicode61'
);
