-- Public, unfiltered marginal counts only. Apply after 019 with operator approval.
begin;
create function public.racer_survey_summary()
returns jsonb
language sql stable security definer set search_path = ''
as $$
  select pg_catalog.jsonb_build_object(
    'total_responses', (select count(*) from public.racer_survey_responses),
    'counts', coalesce((
      select pg_catalog.jsonb_agg(pg_catalog.to_jsonb(c) order by c.question, c.answer)
      from (
        select a.key as question, a.value as answer, count(*) as response_count
        from public.racer_survey_responses r
        cross join lateral pg_catalog.jsonb_each_text(r.answers) a
        group by a.key, a.value
      ) c
    ), '[]'::jsonb)
  );
$$;
revoke all on function public.racer_survey_summary() from public, anon, authenticated, service_role;
grant execute on function public.racer_survey_summary() to anon, authenticated;
-- Deliberately no table grants, row read-back, dates, identifiers or filters.
notify pgrst, 'reload schema';
commit;
