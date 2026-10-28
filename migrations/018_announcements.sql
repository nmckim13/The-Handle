-- Apply once as trusted postgres after 010–017 against the legacy announcements
-- schema (message/type/active/created_at). Do not remove or rewrite legacy rows.
begin;

alter table public.announcements add column updated_at timestamptz;
update public.announcements set updated_at = coalesce(created_at, clock_timestamp());
alter table public.announcements alter column updated_at set default now();
alter table public.announcements alter column updated_at set not null;

create table public.announcement_receipts (
  client_ref text primary key,
  actor_id uuid not null,
  announcement_id uuid,
  expected jsonb,
  announcement jsonb,
  active_expected jsonb,
  receipt jsonb not null
);
alter table public.announcement_receipts enable row level security;
revoke all on public.announcement_receipts from public, anon, authenticated, service_role;

create function public.manage_announcement(
  p_announcement_id uuid, p_expected jsonb, p_announcement jsonb,
  p_active_expected jsonb, p_client_ref text
) returns jsonb
language plpgsql security definer set search_path = ''
as $$
declare
  v_actor uuid := auth.uid();
  v_saved public.announcement_receipts%rowtype;
  v_row public.announcements%rowtype;
  v_keys text[] := array['message','type','active'];
  v_expected_keys text[] := array['message','type','active','created_at','updated_at'];
  v_message text;
  v_active boolean;
  v_snapshot jsonb;
  v_action text;
  v_receipt jsonb;
begin
  if not public.is_admin() or v_actor is null then
    raise exception 'Administrator authorization required' using errcode = '42501';
  end if;
  if p_client_ref is null or p_client_ref !~ '[^[:space:]]' or length(p_client_ref) > 128 then
    raise exception 'Invalid announcement reference' using errcode = '22023';
  end if;

  perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended('announcement-ref:' || p_client_ref, 0));
  select * into v_saved from public.announcement_receipts where client_ref = p_client_ref;
  if found then
    if v_saved.actor_id is distinct from v_actor
       or v_saved.announcement_id is distinct from p_announcement_id
       or v_saved.expected is distinct from p_expected
       or v_saved.announcement is distinct from p_announcement
       or v_saved.active_expected is distinct from p_active_expected then
      raise exception 'Announcement reference already used for a different request' using errcode = '22023';
    end if;
    return v_saved.receipt;
  end if;

  -- SQL NULL alone selects an action; JSON null is not a sentinel. Expected
  -- fields are raw PostgREST JSON, never trimmed, cast or timestamp-normalized.
  if p_announcement_id is null then
    if p_expected is not null or p_announcement is not null then
      raise exception 'Invalid hide-all request' using errcode = '22023';
    end if;
  elsif p_expected is null and p_announcement is null then
    raise exception 'Missing announcement intent' using errcode = '22023';
  end if;
  if p_expected is not null then
    if jsonb_typeof(p_expected) is distinct from 'object'
       or not (p_expected ?& v_expected_keys) or (p_expected - v_expected_keys) <> '{}'::jsonb then
      raise exception 'Announcement snapshot requires exactly five fields' using errcode = '22023';
    end if;
  end if;
  if p_announcement is not null then
    if jsonb_typeof(p_announcement) is distinct from 'object'
       or not (p_announcement ?& v_keys) or (p_announcement - v_keys) <> '{}'::jsonb then
      raise exception 'Announcement proposal requires exactly three fields' using errcode = '22023';
    end if;
    if jsonb_typeof(p_announcement->'message') is distinct from 'string'
       or jsonb_typeof(p_announcement->'type') is distinct from 'string'
       or jsonb_typeof(p_announcement->'active') is distinct from 'boolean'
       or (p_announcement->>'type') not in
          ('General','Rain Delay','Cancellation','Reschedule','All Clear','Race Night Reminder') then
      raise exception 'Invalid announcement fields' using errcode = '22023';
    end if;
    v_message := regexp_replace(p_announcement->>'message', '^[[:space:]]+|[[:space:]]+$', '', 'g');
    v_active := (p_announcement->>'active')::boolean;
    if length(v_message) not between 1 and 1000 then
      raise exception 'Announcement message must contain 1 to 1000 characters' using errcode = '22023';
    end if;
  end if;

  -- Announcements have no race/driver dependencies. Take this gate BEFORE any
  -- announcement row locks, serializing RPCs and trusted direct table writers.
  -- Readers and timing FK checks on races/drivers remain entirely independent.
  lock table public.announcements in share row exclusive mode;
  if p_announcement_id is not null then
    select * into v_row from public.announcements where id = p_announcement_id;
    if p_expected is null then
      if found then
        raise exception 'Announcement ID already exists' using errcode = '22023';
      end if;
    else
      if not found then
        raise exception 'Unknown announcement' using errcode = '22023';
      end if;
      v_snapshot := jsonb_build_object('message',v_row.message,'type',v_row.type,
        'active',v_row.active,'created_at',v_row.created_at,'updated_at',v_row.updated_at);
      if v_snapshot is distinct from p_expected then
        raise exception 'Stale announcement snapshot; reload before changing' using errcode = '22023';
      end if;
    end if;
  end if;
  if p_announcement_id is null or v_active is true then
    select coalesce(jsonb_agg(jsonb_build_object('id',a.id,'message',a.message,
      'type',a.type,'active',a.active,'created_at',a.created_at,'updated_at',a.updated_at)
      order by a.id), '[]'::jsonb) into v_snapshot
      from public.announcements a where a.active;
    if jsonb_typeof(p_active_expected) is distinct from 'array'
       or v_snapshot is distinct from p_active_expected then
      raise exception 'Stale active announcements; reload and review before changing' using errcode = '22023';
    end if;
  end if;

  if p_announcement_id is null then
    update public.announcements set active=false, updated_at=clock_timestamp() where active;
    v_action := 'cleared';
  elsif p_announcement is null then
    delete from public.announcements where id=p_announcement_id;
    v_action := 'removed';
  else
    if v_active then
      update public.announcements set active=false, updated_at=clock_timestamp()
        where active and id <> p_announcement_id;
    end if;
    if p_expected is null then
      insert into public.announcements(id,message,type,active,updated_at)
        values (p_announcement_id,v_message,p_announcement->>'type',v_active,clock_timestamp())
        returning * into v_row;
      v_action := 'created';
    else
      update public.announcements set message=v_message,type=p_announcement->>'type',
        active=v_active,updated_at=clock_timestamp() where id=p_announcement_id
        returning * into v_row;
      v_action := 'updated';
    end if;
  end if;
  v_receipt := jsonb_build_object('announcement_id',p_announcement_id,'client_ref',p_client_ref,
    'action',v_action,'announcement',case when v_action in ('removed','cleared') then null else to_jsonb(v_row) end);
  insert into public.announcement_receipts(client_ref,actor_id,announcement_id,expected,announcement,active_expected,receipt)
    values (p_client_ref,v_actor,p_announcement_id,p_expected,p_announcement,p_active_expected,v_receipt);
  return v_receipt;
end $$;
revoke all on function public.manage_announcement(uuid,jsonb,jsonb,jsonb,text) from public, anon, authenticated, service_role;
grant execute on function public.manage_announcement(uuid,jsonb,jsonb,jsonb,text) to authenticated;

notify pgrst, 'reload schema';
commit;
