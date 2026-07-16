-- Migration 006: allow deleting rows from `races` (fixes the "Remove" button).
--
-- Run once in the Supabase SQL editor for the lnsvacnbgmklpkgzbodb project.
--
-- Why: like `drivers` (004) and `results` (005) before it, the `races` table
-- had row-level-security policies for select/insert/update but NOT delete, so
-- the site's anon key couldn't delete a race. The admin Races tab has a
-- "Remove" button (deleteRace) that toasted "Race removed" while the row
-- silently stayed — a lying button. This grants the delete so the owner can
-- remove a mistakenly-created race. `deleteRace` now also verifies the row is
-- actually gone and reports honestly instead of faking success.
--
-- Note: foreign keys still protect a race that has entries, race_sessions, or
-- official results hanging off it — those deletes are rejected (desirable), and
-- the admin surfaces that as "clear its sign-ups, sessions, and results first."
-- The anon key is already public in the site HTML, so this matches the existing
-- posture and every other content table.

grant delete on public.races to anon, authenticated;

drop policy if exists "anon can delete races" on public.races;
create policy "anon can delete races" on public.races
  for delete to anon using (true);

drop policy if exists "authenticated can delete races" on public.races;
create policy "authenticated can delete races" on public.races
  for delete to authenticated using (true);

-- Clean up the round-999 "TEST — Race Night Dry Run (DELETE ME)" race left over
-- from an admin dry-run (its entries/sessions/results were already cleared, so
-- this just removes the empty race shell).
delete from public.races where round = 999 and name like 'TEST %';
