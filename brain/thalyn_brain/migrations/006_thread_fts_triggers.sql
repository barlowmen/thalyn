-- Migration 006 — auto-sync triggers for the eternal-thread FTS index.
--
-- Migration 005 created `thread_turn_index` (the FTS5 virtual table)
-- but left index maintenance to the application layer. That left a
-- crash window where a `thread_turns` row could land without its
-- corresponding FTS row, breaking ADR-0022's "the index is a cache,
-- rebuildable from `thread_turns`" invariant the moment the
-- application crashed mid-write.
--
-- These triggers move the maintenance into SQLite itself: every
-- `thread_turns` write that lands a 'completed' row mirrors into
-- the index inside the same transaction, so `thread_turn_index`
-- and `thread_turns` are consistent at every transaction boundary.
-- Deletes cascade by trigger as well.

-- Index a turn the moment its row lands with status='completed'.
-- This covers brain reply turns (inserted directly as 'completed')
-- and any caller that writes a turn fully formed.
CREATE TRIGGER IF NOT EXISTS thread_turns_fts_insert_completed
AFTER INSERT ON thread_turns
WHEN NEW.status = 'completed'
BEGIN
    INSERT INTO thread_turn_index (turn_id, body, role)
    VALUES (NEW.turn_id, NEW.body, NEW.role);
END;

-- Index when a turn flips from 'in_progress' to 'completed'. This is
-- the user-turn path: the row first lands in_progress before the brain
-- emits its reply, then flips to completed once the reply is durable.
CREATE TRIGGER IF NOT EXISTS thread_turns_fts_update_completed
AFTER UPDATE OF status ON thread_turns
WHEN NEW.status = 'completed' AND OLD.status != 'completed'
BEGIN
    INSERT INTO thread_turn_index (turn_id, body, role)
    VALUES (NEW.turn_id, NEW.body, NEW.role);
END;

-- Cascade deletes from thread_turns into the FTS index so a deleted
-- turn cannot resurface in search results.
CREATE TRIGGER IF NOT EXISTS thread_turns_fts_delete
AFTER DELETE ON thread_turns
BEGIN
    DELETE FROM thread_turn_index WHERE turn_id = OLD.turn_id;
END;
