-- Apply once, only with explicit operator approval, as the trusted SQL owner.
-- Independent of 010/admin authorization; changes no existing schema grants.
begin;

create table public.racer_survey_responses (
  id uuid primary key,
  answers jsonb not null,
  submitted_on date not null default current_date
);
alter table public.racer_survey_responses enable row level security;
-- No client policies or individual read-back endpoints. Migration 020 adds
-- public aggregate reporting without granting access to this table.
-- Explicit revokes also override pre-existing default table privileges.
revoke all on public.racer_survey_responses from public, anon, authenticated, service_role;

create function public.submit_racer_survey(p_answers jsonb, p_request_id uuid)
returns uuid
language plpgsql security definer set search_path = ''
as $$
declare
  v_keys text[] := array['attendance','enjoyment','barrier','prep_time','prep_task','improvement'];
  v_choices jsonb := '{
    "attendance": ["regularly","occasionally","not_recently","never"],
    "enjoyment": ["racing","people","improving","watching","not_yet"],
    "barrier": ["schedule","prep_time","cost","mechanical","no_kart","nothing","other"],
    "prep_time": ["under_one","one_to_three","three_to_six","six_plus","not_applicable"],
    "prep_task": ["tires","engine","clutch_chain","cleaning","other","not_applicable"],
    "improvement": ["less_prep","more_track_time","lower_cost","clearer_rules","schedule","nothing","other"]
  }'::jsonb;
  v_key text;
  v_saved jsonb;
begin
  if p_request_id is null or pg_catalog.jsonb_typeof(p_answers) is distinct from 'object' then
    raise exception 'Invalid survey request' using errcode = '22023';
  end if;
  if not (p_answers ?& v_keys) or (p_answers - v_keys) <> '{}'::jsonb then
    raise exception 'Survey requires exactly six answers' using errcode = '22023';
  end if;
  foreach v_key in array v_keys loop
    if pg_catalog.jsonb_typeof(p_answers -> v_key) is distinct from 'string'
       or not ((v_choices -> v_key) ? (p_answers ->> v_key)) then
      raise exception 'Invalid survey answer' using errcode = '22023';
    end if;
  end loop;

  -- The unique index waits for an uncommitted same-ID insert. The separate
  -- SELECT gets a fresh READ COMMITTED snapshot after that wait; do not merge
  -- these statements into a CTE (which could miss a concurrent winning row).
  insert into public.racer_survey_responses(id, answers)
    values (p_request_id, p_answers)
    on conflict (id) do nothing;
  select answers into v_saved from public.racer_survey_responses where id = p_request_id;
  if v_saved is distinct from p_answers then
    raise exception 'Survey request ID already used for different answers' using errcode = '22023';
  end if;
  return p_request_id;
end $$;
revoke all on function public.submit_racer_survey(jsonb, uuid) from public, anon, authenticated, service_role;
grant execute on function public.submit_racer_survey(jsonb, uuid) to anon, authenticated;

notify pgrst, 'reload schema';
commit;
