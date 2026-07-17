-- Broadcast support for the qualifying stage: syncs the qualifying group
-- draw (previously kept only on the admin's own device, in localStorage) to
-- a column on live_state, so the big screen / driver-facing pages — usually
-- a separate device on race night — can show the same roster and groups.
--
-- Run once in the Supabase SQL editor for the lnsvacnbgmklpkgzbodb project
-- (yardkartz.com's own Supabase project), same place as 001..007.

alter table live_state
  add column if not exists qual_groups jsonb;

-- The checked-in roster needs to reach the big screen live, not just on a
-- manual refresh, so it can show every driver the moment Race Night goes
-- live and update as people check in.
do $$
begin
  alter publication supabase_realtime add table entries;
exception when duplicate_object then null;
end $$;
