-- Apply once as trusted postgres after 010 -> 011 -> 012 -> 013 -> 014.
begin;

-- Preserve 010's exact public allowlist, column order, barrier and grants.
-- Explicit staff age wins; clearing it restores birthday-derived display age.
create or replace view public.public_drivers with (security_barrier = true) as
select id, name, number, color, wins, starts, nickname, hometown, occupation,
       quote, bio, photo_url, kart_make, kart_colors, sponsor, fav_condition,
       racing_style, pre_race_ritual, biggest_rival, career_highlight,
       transponder_id,
       coalesce(age, extract(year from age(current_date, birthday::date))::integer) as age
from public.drivers;

create table public.driver_profile_receipts (
  client_ref text primary key,
  actor_id uuid not null,
  driver_id uuid not null,
  expected jsonb not null,
  profile jsonb not null,
  receipt jsonb not null
);
alter table public.driver_profile_receipts enable row level security;
revoke all on public.driver_profile_receipts from public, anon, authenticated, service_role;

create function public.save_driver_profile(
  p_driver_id uuid, p_expected jsonb, p_profile jsonb, p_client_ref text
) returns jsonb
language plpgsql security definer set search_path = ''
as $$
declare
  v_actor uuid := auth.uid();
  v_saved public.driver_profile_receipts%rowtype;
  v_driver public.drivers%rowtype;
  v_keys constant text[] := array['nickname','hometown','age','occupation','quote','bio',
    'photo_url','kart_make','kart_colors','sponsor','fav_condition','racing_style',
    'pre_race_ritual','biggest_rival','career_highlight'];
  v_key text;
  v_text text;
  v_profile jsonb := '{}'::jsonb;
  v_snapshot jsonb;
  v_receipt jsonb;
begin
  if not public.is_admin() or v_actor is null then
    raise exception 'Administrator authorization required' using errcode = '42501';
  end if;
  if p_driver_id is null or jsonb_typeof(p_expected) is distinct from 'object'
     or jsonb_typeof(p_profile) is distinct from 'object'
     or p_client_ref is null or p_client_ref !~ '[^[:space:]]'
     or length(p_client_ref) > 128 then
    raise exception 'Invalid driver-profile request' using errcode = '22023';
  end if;

  perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended('driver-profile-ref:' || p_client_ref, 0));
  select * into v_saved from public.driver_profile_receipts where client_ref = p_client_ref;
  if found then
    if v_saved.actor_id is distinct from v_actor or v_saved.driver_id is distinct from p_driver_id
       or v_saved.expected is distinct from p_expected or v_saved.profile is distinct from p_profile then
      raise exception 'Driver-profile reference already used for a different request' using errcode = '22023';
    end if;
    return v_saved.receipt;
  end if;

  if not (p_profile ?& v_keys) or (p_profile - v_keys) <> '{}'::jsonb then
    raise exception 'Profile must contain exactly the profile fields' using errcode = '22023';
  end if;
  foreach v_key in array v_keys loop
    if p_profile->v_key = 'null'::jsonb then
      v_profile := v_profile || jsonb_build_object(v_key, null);
    elsif v_key = 'age' then
      if jsonb_typeof(p_profile->v_key) <> 'number'
         or (p_profile->>v_key) !~ '^(0|[1-9][0-9]{0,2})$' then
        raise exception 'Age must be an integer from 0 to 120 or null' using errcode = '22023';
      end if;
      if (p_profile->>v_key)::integer > 120 then
        raise exception 'Age out of range' using errcode = '22023';
      end if;
      v_profile := v_profile || jsonb_build_object(v_key, (p_profile->>v_key)::integer);
    else
      if jsonb_typeof(p_profile->v_key) <> 'string' then
        raise exception 'Profile text must be a string or null' using errcode = '22023';
      end if;
      v_text := regexp_replace(p_profile->>v_key, '^[[:space:]]+|[[:space:]]+$', '', 'g');
      if length(v_text) < 1 or length(v_text) > (case v_key when 'bio' then 2000 when 'photo_url' then 2048 else 300 end)
         or (v_key = 'photo_url' and v_text !~* '^https?://[^@/?#[:space:]]+([/?#][^[:space:]]*)?$') then
        raise exception 'Invalid or overlong profile text/HTTP(S) URL' using errcode = '22023';
      end if;
      v_profile := v_profile || jsonb_build_object(v_key, v_text);
    end if;
  end loop;

  -- Match 014 and the other driver-global writers. NO KEY UPDATE remains
  -- compatible with timing's FK KEY SHARE checks; no dependent writes/locks.
  lock table public.drivers in share row exclusive mode;
  select * into v_driver from public.drivers where id = p_driver_id for no key update;
  if not found then
    raise exception 'Unknown driver' using errcode = '22023';
  end if;
  select jsonb_object_agg(k, to_jsonb(v_driver)->k) into v_snapshot from unnest(v_keys) as fields(k);
  if v_snapshot is distinct from p_expected then
    raise exception 'Stale driver-profile snapshot; reload before changing' using errcode = '22023';
  end if;

  update public.drivers set
    nickname = v_profile->>'nickname', hometown = v_profile->>'hometown',
    age = (v_profile->>'age')::integer, occupation = v_profile->>'occupation',
    quote = v_profile->>'quote', bio = v_profile->>'bio', photo_url = v_profile->>'photo_url',
    kart_make = v_profile->>'kart_make', kart_colors = v_profile->>'kart_colors',
    sponsor = v_profile->>'sponsor', fav_condition = v_profile->>'fav_condition',
    racing_style = v_profile->>'racing_style', pre_race_ritual = v_profile->>'pre_race_ritual',
    biggest_rival = v_profile->>'biggest_rival', career_highlight = v_profile->>'career_highlight'
  where id = p_driver_id;
  v_receipt := jsonb_build_object('driver_id', p_driver_id, 'client_ref', p_client_ref, 'profile', v_profile);
  insert into public.driver_profile_receipts(client_ref, actor_id, driver_id, expected, profile, receipt)
    values (p_client_ref, v_actor, p_driver_id, p_expected, p_profile, v_receipt);
  return v_receipt;
end $$;
revoke all on function public.save_driver_profile(uuid, jsonb, jsonb, text) from public, anon, authenticated, service_role;
grant execute on function public.save_driver_profile(uuid, jsonb, jsonb, text) to authenticated;

notify pgrst, 'reload schema';
commit;
