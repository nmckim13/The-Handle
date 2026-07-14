-- Migration 004: let the admin "Remove driver" button actually work.
--
-- Run once in the Supabase SQL editor for the lnsvacnbgmklpkgzbodb project
-- (yardkartz.com's own project), same as the earlier migrations.
--
-- Why: the `drivers` table has row-level-security policies for select/insert/
-- update by the site's anon key, but was MISSING a delete policy. So every
-- DELETE from the anon key silently affected 0 rows, and the admin's "Remove"
-- button reported success while removing nothing. Other content tables
-- (announcements, races, videos, lap_records) already allow anon delete, so
-- this just brings `drivers` in line with them. The anon key is already public
-- in the admin HTML, so this doesn't change the security posture.
--
-- Note: a driver who already has official `results` may still be undeletable
-- if the results.driver_id foreign key restricts it — that's desirable (don't
-- silently rewrite season standings). The admin surfaces that as an error.

grant delete on public.drivers to anon;

drop policy if exists "anon can delete drivers" on public.drivers;
create policy "anon can delete drivers" on public.drivers
  for delete to anon using (true);

-- Cover the authenticated role too, in case the admin is ever used signed in.
grant delete on public.drivers to authenticated;

drop policy if exists "authenticated can delete drivers" on public.drivers;
create policy "authenticated can delete drivers" on public.drivers
  for delete to authenticated using (true);
