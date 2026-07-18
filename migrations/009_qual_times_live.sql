-- Broadcast support for qualifying times: syncs each entry's best qualifying
-- lap (previously kept only on the admin's own device, in localStorage) to a
-- column on live_state, so the big screen can rank-order the qualifying list
-- live as times are entered. Mirrors 008_qual_groups_live.sql.
--
-- Run once in the Supabase SQL editor for the lnsvacnbgmklpkgzbodb project
-- (yardkartz.com's own Supabase project), same place as 001..008.

alter table live_state
  add column if not exists qual_times jsonb;
