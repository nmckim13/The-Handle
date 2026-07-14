-- Migration 005: allow deleting rows from `results` (fixes re-posting results).
--
-- Run once in the Supabase SQL editor for the lnsvacnbgmklpkgzbodb project.
--
-- Why: like `drivers` before migration 004, the `results` table had row-level-
-- security policies for select/insert but NOT delete, so the site's anon key
-- couldn't delete result rows. Two flows depend on that delete and were
-- silently failing:
--   * Re-posting official race-night results (rnPostOfficial clears the race's
--     old results before writing the corrected set) — without delete, the old
--     rows stay and the standings double-count.
--   * The season-results CSV importer, which clears a race's results before
--     importing a replacement set.
-- The anon key is already public in the site HTML, so this matches the existing
-- posture and every other content table.
--
-- Cleanup: also removes leftover rows from a 20-car end-to-end test on the
-- Halloween round (R8) that couldn't be deleted before this policy existed.
-- They were already unlinked (driver_id null, points 0) so they don't affect
-- standings, but this purges them for good.

grant delete on public.results to anon, authenticated;

drop policy if exists "anon can delete results" on public.results;
create policy "anon can delete results" on public.results
  for delete to anon using (true);

drop policy if exists "authenticated can delete results" on public.results;
create policy "authenticated can delete results" on public.results
  for delete to authenticated using (true);

-- purge the R8 test leftovers
delete from public.results
  where race_id = '2da92842-1191-4d94-a6e5-d432a4109f9c' and driver_id is null;
