-- Prevents duplicate heat/B-Main/feature sessions when two admin devices
-- both trigger the same stage-creation action within the same round trip
-- (e.g. two phones both tapping "Draw Heats" at once). The client already
-- pre-checks for an existing stage before creating one, but that check and
-- the write aren't atomic across two separate browsers — this unique index
-- makes the database itself reject the second, redundant insert.

create unique index if not exists race_sessions_unique_stage
  on race_sessions(race_id, session_type, session_number);
