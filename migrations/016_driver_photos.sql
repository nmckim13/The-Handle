-- Apply once as trusted postgres after 010 -> 011 -> 012 -> 013 -> 014 -> 015.
begin;

create table public.driver_photo_receipts (
  client_ref text primary key,
  actor_id uuid not null,
  driver_id uuid not null,
  expected text,
  photo_url text,
  receipt jsonb not null
);
alter table public.driver_photo_receipts enable row level security;
revoke all on public.driver_photo_receipts from public, anon, authenticated, service_role;

create function public.set_driver_photo(
  p_driver_id uuid, p_expected text, p_photo_url text, p_client_ref text
) returns jsonb
language plpgsql security definer set search_path = ''
as $$
declare
  v_actor uuid := auth.uid();
  v_saved public.driver_photo_receipts%rowtype;
  v_photo text;
  v_object text;
  v_host text;
  v_port text;
  v_receipt jsonb;
begin
  if not public.is_admin() or v_actor is null then
    raise exception 'Administrator authorization required' using errcode = '42501';
  end if;
  if p_driver_id is null or p_client_ref is null or p_client_ref !~ '[^[:space:]]'
     or length(p_client_ref) > 128 then
    raise exception 'Invalid driver-photo request' using errcode = '22023';
  end if;

  perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended('driver-photo-ref:' || p_client_ref, 0));
  select * into v_saved from public.driver_photo_receipts where client_ref = p_client_ref;
  if found then
    if v_saved.actor_id is distinct from v_actor or v_saved.driver_id is distinct from p_driver_id
       or v_saved.expected is distinct from p_expected or v_saved.photo_url is distinct from p_photo_url then
      raise exception 'Driver-photo reference already used for a different request' using errcode = '22023';
    end if;
    return v_saved.receipt;
  end if;

  if p_photo_url is not null then
    -- Never trim, decode or normalize. Only literal public Storage object paths
    -- are accepted, with an HTTP(S) host (or bracketed IP) and optional port.
    if length(p_photo_url) > 2048 or p_photo_url !~
      '^[Hh][Tt][Tt][Pp][Ss]?://([A-Za-z0-9._~-]+|\[[0-9A-Fa-f:.]+\])(:[0-9]+)?/storage/v1/object/public/driver-photos/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\.(webp|png)$' then
      raise exception 'Invalid driver-photo HTTP(S) Storage URL' using errcode = '22023';
    end if;
    v_host := substring(p_photo_url from '://\[([^]]+)\]');
    if v_host is not null then
      begin
        if family(v_host::inet) <> 6 then
          raise exception 'Invalid driver-photo IPv6 host' using errcode = '22023';
        end if;
      exception when invalid_text_representation then
        raise exception 'Invalid driver-photo IPv6 host' using errcode = '22023';
      end;
    end if;
    v_port := substring(p_photo_url from '://(?:\[[^]]+\]|[^/:]+):([0-9]+)/');
    if v_port::numeric > 65535 then
      raise exception 'Invalid driver-photo URL port' using errcode = '22023';
    end if;
    v_object := split_part(p_photo_url, '/storage/v1/object/public/driver-photos/', 2);
    if split_part(v_object, '/', 1) is distinct from p_driver_id::text then
      raise exception 'Photo must belong to the selected driver' using errcode = '22023';
    end if;
    if not exists (select 1 from storage.objects
      where bucket_id = 'driver-photos' and name = v_object) then
      raise exception 'Driver-photo Storage object does not exist' using errcode = '22023';
    end if;
  end if;

  -- Same driver-global gate as 014/015, but compare only the raw photo column.
  -- NO KEY UPDATE is compatible with timing's driver FK KEY SHARE checks.
  lock table public.drivers in share row exclusive mode;
  select photo_url into v_photo from public.drivers where id = p_driver_id for no key update;
  if not found then
    raise exception 'Unknown driver' using errcode = '22023';
  end if;
  if v_photo is distinct from p_expected then
    raise exception 'Stale driver-photo snapshot; reload before changing' using errcode = '22023';
  end if;

  -- Do not route through 015: even legacy/overlong profile text must survive
  -- byte-for-byte. Null removes only the custom link, never a Storage object.
  update public.drivers set photo_url = p_photo_url where id = p_driver_id;
  v_receipt := jsonb_build_object('driver_id', p_driver_id, 'client_ref', p_client_ref, 'photo_url', p_photo_url);
  insert into public.driver_photo_receipts(client_ref, actor_id, driver_id, expected, photo_url, receipt)
    values (p_client_ref, v_actor, p_driver_id, p_expected, p_photo_url, v_receipt);
  return v_receipt;
end $$;
revoke all on function public.set_driver_photo(uuid, text, text, text) from public, anon, authenticated, service_role;
grant execute on function public.set_driver_photo(uuid, text, text, text) to authenticated;

notify pgrst, 'reload schema';
commit;
