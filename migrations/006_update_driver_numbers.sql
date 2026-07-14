-- Update two drivers' car numbers.
--
-- Run this once in the Supabase SQL editor for the lnsvacnbgmklpkgzbodb
-- project (yardkartz.com's own Supabase project), the same way the other
-- migrations in this folder are applied.
--
-- Requested changes:
--   * William Shane -> #70
--   * Kelton        -> #73
--
-- Names are matched case-insensitively against the existing `drivers.name`
-- values. The `number` literals are written unquoted-as-text ('70'/'73') so
-- Postgres coerces them to whatever type the `number` column actually is
-- (integer or text) — assigning an unknown-typed literal works either way.

update public.drivers
   set number = '70'
 where name ilike '%william shane%';

update public.drivers
   set number = '73'
 where name ilike '%kelton%';

-- Verify the result after running:
--   select id, name, number from public.drivers
--    where name ilike '%shane%' or name ilike '%kelton%';
