-- Keep race-night signup open through the track's calendar day, not UTC midnight.
-- America/Chicago matches the public race countdown and handles daylight saving.
-- Apply to the existing signup RPC only; no entries, waivers, grants or race
-- statuses change. Both the PRE-010 repair and canonical 010 expose this RPC.
begin;
set local lock_timeout = '2s';
set local statement_timeout = '20s';

alter function public.submit_signup(uuid, uuid, text, text, text, text)
  set timezone = 'America/Chicago';

notify pgrst, 'reload schema';
commit;
