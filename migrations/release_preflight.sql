-- PRE-010 only. Catalogs + aggregate counts; no RPCs, DDL, writes or row exports.
-- Run standalone as a trusted operator, not through the public/browser API.
-- Read RELEASE_CHECKLIST.md. A clean run is NOT deployment authorization.
begin isolation level repeatable read read only;
set local statement_timeout = '20s';
set local lock_timeout = '2s';
set local client_min_messages = notice;

-- Metadata only, available even when the checks below block. Definitions and
-- row contents are omitted because they can contain private literals.
select schemaname,tablename,policyname,roles,cmd from pg_policies
  where schemaname in ('public','storage') order by schemaname,tablename,policyname;
select n.nspname,c.relname,t.tgname,t.tgenabled,p.proname as trigger_function
  from pg_trigger t join pg_class c on c.oid=t.tgrelid join pg_namespace n on n.oid=c.relnamespace
  join pg_proc p on p.oid=t.tgfoid where not t.tgisinternal and n.nspname in ('public','storage')
  order by n.nspname,c.relname,t.tgname;
select n.nspname,p.proname,pg_get_function_identity_arguments(p.oid) as arguments,
  pg_get_userbyid(p.proowner) as owner,p.prosecdef as security_definer
  from pg_proc p join pg_namespace n on n.oid=p.pronamespace
  where n.nspname='public' and p.prosecdef order by p.proname;
select table_schema,table_name,grantee,privilege_type from information_schema.role_table_grants
  where table_schema in ('public','storage') order by table_schema,table_name,grantee,privilege_type;
select pubname,schemaname,tablename from pg_publication_tables where pubname='supabase_realtime' order by tablename;

do $$
declare
  r record;
  v_type text;
  v_count bigint;
  v_blocks integer := 0;
  v_schema_blocks integer;
  v_ok boolean;
begin
  raise notice 'PREFLIGHT | PRE-010 | read_only=% | PostgreSQL=% | timezone=% | runtime_default_isolation=%',
    current_setting('transaction_read_only'), current_setting('server_version'),
    current_setting('TimeZone'), current_setting('default_transaction_isolation');
  if current_setting('transaction_read_only') <> 'on' then
    raise exception 'PREFLIGHT BLOCK | Read-only transaction not established; run the complete file standalone';
  end if;
  if not exists (select 1 from pg_roles where rolname = current_user and (rolsuper or rolbypassrls)) then
    raise exception 'PREFLIGHT BLOCK | Run as a trusted BYPASSRLS operator; filtered counts cannot establish readiness';
  end if;
  if current_setting('default_transaction_isolation') <> 'read committed' then
    v_blocks := v_blocks + 1;
    raise notice 'BLOCK | Runtime default isolation must be reviewed: RPCs assume short READ COMMITTED calls';
  end if;
  for r in select name from (values ('anon'),('authenticated'),('service_role')) x(name) loop
    if not exists (select 1 from pg_roles where rolname = r.name) then
      v_blocks := v_blocks + 1; raise notice 'BLOCK | Missing role %', r.name;
    end if;
  end loop;
  if not exists (select 1 from pg_roles where rolname='service_role' and (rolsuper or rolbypassrls)) then
    v_blocks := v_blocks + 1; raise notice 'BLOCK | service_role must bypass RLS for bridge timing under 010';
  end if;
  if to_regprocedure('auth.uid()') is null or not exists (
    select 1 from pg_proc where oid = to_regprocedure('auth.uid()') and prorettype = 'uuid'::regtype
  ) then
    v_blocks := v_blocks + 1; raise notice 'BLOCK | auth.uid() returning UUID is required';
  end if;

  -- Conservative, tested type envelope. Domains/enums/numeric lap-time columns
  -- require explicit compatibility review, not a silent cast or schema repair.
  for r in
    select x.tbl, x.typ, unnest(string_to_array(x.cols,' ')) as col
    from (values
      ('auth.users','uuid','id'),
      ('storage.objects','text','bucket_id name'),
      ('storage.buckets','text','id'),
      ('storage.buckets','bool','public'),
      ('public.drivers','uuid','id'),
      ('public.drivers','text','name number color nickname hometown occupation quote bio photo_url kart_make kart_colors sponsor fav_condition racing_style pre_race_ritual biggest_rival career_highlight'),
      ('public.drivers','int4','wins starts age transponder_id'),
      ('public.drivers','date','birthday'),
      ('public.races','uuid','id'),
      ('public.races','text','name status winner_name'),
      ('public.races','date','race_date'),
      ('public.races','int4','round season laps'),
      ('public.results','uuid','id race_id driver_id'),
      ('public.results','int4','position points season'),
      ('public.results','text','lap_time'),
      ('public.lap_records','uuid','id race_id driver_id'),
      ('public.lap_records','numeric','lap_time_seconds'),
      ('public.videos','uuid','id'),
      ('public.sponsors','uuid','id'),
      ('public.announcements','uuid','id'),
      ('public.announcements','text','message type'),
      ('public.announcements','bool','active'),
      ('public.announcements','timestamptz','created_at'),
      ('public.entries','uuid','id race_id driver_id'),
      ('public.entries','text','name car_number color email phone waiver_name status client_ref'),
      ('public.entries','timestamptz','waiver_signed_at created_at'),
      ('public.race_sessions','uuid','id race_id'),
      ('public.race_sessions','text','session_type status flag_state'),
      ('public.race_sessions','int4','session_number laps_scheduled current_lap'),
      ('public.race_sessions','timestamptz','started_at completed_at'),
      ('public.lineups','uuid','id session_id entry_id'),
      ('public.lineups','int4','starting_position'),
      ('public.session_results','uuid','id session_id entry_id'),
      ('public.session_results','text','status client_ref'),
      ('public.session_results','int4','finishing_position laps_complete'),
      ('public.session_results','numeric','best_lap_seconds'),
      ('public.live_state','uuid','id race_id session_id'),
      ('public.live_state','bool','is_live'),
      ('public.live_state','text','current_stage message'),
      ('public.live_state','timestamptz','updated_at'),
      ('public.live_state','jsonb','qual_groups qual_times'),
      ('public.live_timing','uuid','id race_id driver_id'),
      ('public.live_timing','text','room_id lm_name'),
      ('public.live_timing','int4','transponder_id laps_count position'),
      ('public.live_timing','numeric','last_seconds best_seconds total_seconds'),
      ('public.live_timing','timestamptz','updated_at')
    ) x(tbl,typ,cols)
  loop
    select t.typname into v_type from pg_attribute a join pg_type t on t.oid = a.atttypid
      where a.attrelid = to_regclass(r.tbl) and a.attname = r.col and a.attnum > 0 and not a.attisdropped;
    if v_type is null or not (v_type = r.typ or (r.typ = 'text' and v_type = 'varchar')) then
      v_blocks := v_blocks + 1;
      raise notice 'BLOCK | Column %.% expected %, found %', r.tbl, r.col, r.typ, coalesce(v_type,'MISSING');
    end if;
    if not exists (select 1 from pg_class where oid = to_regclass(r.tbl) and relkind = 'r') then
      v_blocks := v_blocks + 1; raise notice 'BLOCK | % must be an ordinary base table', r.tbl;
    end if;
  end loop;
  v_schema_blocks := v_blocks;

  -- These migrations are once-only. Any collision means stop and establish the
  -- applied boundary from trusted records; existence alone is not provenance.
  for r in select n.nspname, c.relname from pg_class c join pg_namespace n on n.oid = c.relnamespace
    where n.nspname = 'public' and c.relname = any(array[
      'admin_users','public_entries','public_drivers','entries_active_race_driver_key',
      'official_result_receipts','season_result_import_receipts','manual_result_receipts',
      'driver_management_receipts','driver_profile_receipts','driver_photo_receipts',
      'race_scheduling_receipts','announcement_receipts'])
  loop
    v_blocks := v_blocks + 1; raise notice 'BLOCK | Release object already exists: %.%; do not blindly rerun 010-018', r.nspname,r.relname;
  end loop;
  for r in select p.proname, pg_get_function_identity_arguments(p.oid) as args
    from pg_proc p join pg_namespace n on n.oid = p.pronamespace
    where n.nspname = 'public' and p.proname = any(array[
      'is_admin','submit_signup','public_entries','public_drivers','post_official_results',
      'import_season_results','add_manual_result','manage_driver','save_driver_profile',
      'set_driver_photo','manage_race','manage_announcement'])
  loop
    v_blocks := v_blocks + 1; raise notice 'BLOCK | Release routine already exists: %(%); reconcile migration history',r.proname,r.args;
  end loop;
  if exists (select 1 from pg_attribute where attrelid = to_regclass('public.announcements')
    and attname = 'updated_at' and not attisdropped) then
    v_blocks := v_blocks + 1; raise notice 'BLOCK | announcements.updated_at already exists; 018 adds it without IF NOT EXISTS';
  end if;

  for r in select tbl, cols, predicate from (values
    ('public.entries',array['client_ref'],'client_ref IS NOT NULL'),
    ('public.session_results',array['client_ref'],'client_ref IS NOT NULL'),
    ('public.drivers',array['transponder_id'],'transponder_id IS NOT NULL'),
    ('public.race_sessions',array['race_id','session_type','session_number'],''),
    ('public.live_state',array['race_id'],''),
    ('public.live_timing',array['room_id','transponder_id'],'')
  ) x(tbl,cols,predicate) loop
    select exists (select 1 from pg_index i where i.indrelid = to_regclass(r.tbl)
      and i.indisunique and i.indisvalid and i.indisready and i.indimmediate
      and (select array_agg(a.attname::text order by k.ord) from unnest(i.indkey::smallint[]) with ordinality k(attnum,ord)
        join pg_attribute a on a.attrelid=i.indrelid and a.attnum=k.attnum where k.ord <= i.indnkeyatts) = r.cols
      and coalesce(trim(both '()' from pg_get_expr(i.indpred,i.indrelid)), '') = r.predicate)
      into v_ok;
    if not v_ok then
      v_blocks := v_blocks + 1; raise notice 'BLOCK | Missing compatible valid immediate unique index on % (%) predicate [%]',r.tbl,array_to_string(r.cols,','),r.predicate;
    end if;
  end loop;
  for r in select c.oid::regclass as tbl from pg_class c join pg_namespace n on n.oid=c.relnamespace
    where n.nspname='public' and c.relname = any(array['drivers','races','results','lap_records','videos','sponsors','announcements','entries','race_sessions','lineups','session_results','live_state','live_timing'])
    and not exists (select 1 from pg_constraint k join pg_attribute a on a.attrelid=k.conrelid and a.attname='id'
      where k.conrelid=c.oid and k.contype='p' and k.conkey=array[a.attnum])
  loop
    v_blocks := v_blocks + 1; raise notice 'BLOCK | Missing single-column id primary key: %',r.tbl;
  end loop;
  for r in select tbl,col from (values ('public.drivers','color'),('public.results','lap_time')) x(tbl,col) loop
    if exists (select 1 from pg_attribute where attrelid=to_regclass(r.tbl) and attname=r.col and attnotnull) then
      v_blocks := v_blocks + 1; raise notice 'BLOCK | %.% must permit NULL',r.tbl,r.col;
    end if;
  end loop;

  if v_schema_blocks = 0 then
    -- Aggregate-only output. Never emit names, contacts, waiver signatures,
    -- request references, object names or private row payloads.
    for r in select label, query, severity from (values
      ('active_signup_duplicate_groups', 'select count(*) from (select 1 from public.entries where driver_id is not null and status <> ''withdrawn'' group by race_id,driver_id having count(*)>1) d','BLOCK'),
      ('invalid_career_counters', 'select count(*) from public.drivers where starts is null or wins is null or starts<0 or wins<0 or wins>starts','REVIEW'),
      ('ambiguous_driver_identity_groups', 'select count(*) from (select 1 from public.drivers group by lower(btrim(name)),btrim(number::text) having count(*)>1) d','REVIEW'),
      ('invalid_official_results', 'select count(*) from public.results where driver_id is null or position is null or position<1 or points is null or points<0','REVIEW'),
      ('duplicate_result_driver_groups', 'select count(*) from (select 1 from public.results group by race_id,driver_id having count(*)>1) d','REVIEW'),
      ('duplicate_result_position_groups', 'select count(*) from (select 1 from public.results group by race_id,position having count(*)>1) d','REVIEW'),
      ('live_races', 'select count(*) from public.live_state where is_live','REVIEW'),
      ('running_sessions', 'select count(*) from public.race_sessions where status=''in_progress''','REVIEW'),
      ('active_announcements', 'select count(*) from public.announcements where active','REVIEW')
    ) x(label,query,severity) loop
      execute r.query into v_count;
      if v_count > 0 then
        raise notice '% | % = %',r.severity,r.label,v_count;
        if r.severity='BLOCK' then v_blocks := v_blocks + 1; end if;
      else raise notice 'PASS | % = 0',r.label;
      end if;
    end loop;
    execute 'select count(*) from storage.buckets where id=''driver-photos'' and public' into v_count;
    if v_count <> 1 then
      v_blocks := v_blocks + 1; raise notice 'BLOCK | Existing public driver-photos bucket required';
    end if;
  else
    raise notice 'SKIP | Data checks: prerequisite schema/operator checks failed; no readiness conclusion';
  end if;
  raise notice 'REVIEW | Inventory above is not an automated security audit; inspect all custom triggers, defaults, constraints, grants, policies and privileged routines';
  raise notice 'REVIEW | Confirm UTC PostgREST timestamp representation, API exposed schemas, real Auth, bridge credential, backup/restore and public polling in staging';
  if v_blocks > 0 then
    raise exception 'PREFLIGHT BLOCKED | % blocking finding(s); no changes made. Review every preceding BLOCK/SKIP before any rollout.',v_blocks;
  end if;
  raise notice 'PREFLIGHT COMPLETE | No automated blockers found. Manual checklist gates remain; this is not a release approval.';
end $$;

rollback;
