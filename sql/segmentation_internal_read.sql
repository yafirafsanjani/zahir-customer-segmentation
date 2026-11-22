-- REVIEW ONLY. This file is not applied by Streamlit or by local tests.
-- First run inspect_segmentation_schema.sql and confirm the three detail columns
-- below match the live schema. Adapt identifiers here if they differ.
-- Run as a trusted database administrator. The allowlist starts EMPTY.
begin;

do $$
begin
  if to_regclass('public.segment_summary') is null
     or to_regclass('public.faktur_lolos_dengan_klaster') is null then
    raise exception 'Import and inspect both segmentation tables before applying this script.';
  end if;
  if (select count(*) from information_schema.columns
      where table_schema = 'public'
        and table_name = 'faktur_lolos_dengan_klaster'
        and column_name in ('id_samaran','id_kelompok_faktur','grand_total_rp')) <> 3 then
    raise exception 'Detail schema differs: review aggregate RPC column names before applying.';
  end if;
end $$;

create schema if not exists zahir_internal;
revoke all on schema zahir_internal from public, anon, authenticated;
create table if not exists zahir_internal.segmentation_readers (
  user_id uuid primary key references auth.users(id) on delete cascade,
  enabled boolean not null default true,
  granted_at timestamptz not null default now()
);
revoke all on table zahir_internal.segmentation_readers from public, anon, authenticated;
alter table zahir_internal.segmentation_readers enable row level security;

-- Only the trusted owner reads the allowlist. No user-supplied user_id argument,
-- and no editable user_metadata claim is used to grant access.
create or replace function public.has_segmentation_access()
returns boolean
language sql stable security definer
set search_path = ''
as $$
  select exists (
    select 1
    from zahir_internal.segmentation_readers r
    where r.user_id = (select auth.uid()) and r.enabled
  );
$$;
revoke all on function public.has_segmentation_access() from public, anon;
grant execute on function public.has_segmentation_access() to authenticated;

do $$
declare target text;
begin
  foreach target in array array['segment_summary','faktur_lolos_dengan_klaster'] loop
    execute format('alter table public.%I enable row level security', target);
    execute format('revoke select on table public.%I from anon, public', target);
    execute format('grant select on table public.%I to authenticated', target);
    -- Preserve existing policies. The restrictive guard prevents an existing
    -- permissive policy from admitting authenticated users outside the allowlist.
    if not exists (
      select 1 from pg_policies where schemaname='public' and tablename=target
        and policyname='zahir_segmentation_internal_guard'
    ) then
      execute format(
        'create policy zahir_segmentation_internal_guard on public.%I as restrictive for select to authenticated using ((select public.has_segmentation_access()))',
        target
      );
    end if;
    if not exists (
      select 1 from pg_policies where schemaname='public' and tablename=target
        and policyname='zahir_segmentation_internal_read'
    ) then
      execute format(
        'create policy zahir_segmentation_internal_read on public.%I for select to authenticated using ((select public.has_segmentation_access()))',
        target
      );
    end if;
  end loop;
end $$;

-- Runs with the caller's table privileges and RLS; never bypasses them.
-- Server-side totals avoid the REST 1,000-row cap and don't download all names.
create or replace function public.segmentation_invoice_totals()
returns table (
  invoice_count bigint,
  customer_count bigint,
  total_grand_total numeric,
  unique_invoice_count bigint
)
language plpgsql stable security invoker
set search_path = ''
as $$
begin
  if not public.has_segmentation_access() then
    raise insufficient_privilege using message = 'Internal segmentation access required.';
  end if;
  return query
    select count(*),
           count(distinct f.id_samaran),
           coalesce(sum(f.grand_total_rp::numeric), 0::numeric),
           count(distinct nullif(btrim(f.id_kelompok_faktur::text), ''))
    from public.faktur_lolos_dengan_klaster f;
end;
$$;
revoke all on function public.segmentation_invoice_totals() from public, anon;
grant execute on function public.segmentation_invoice_totals() to authenticated;

commit;

-- ADMIN ACTION AFTER REVIEW: create/confirm a Supabase Auth account, then grant
-- read access explicitly. Replace the UUID; do not run this placeholder:
-- insert into zahir_internal.segmentation_readers(user_id)
-- values ('REPLACE_WITH_AUTH_USER_UUID'::uuid)
-- on conflict (user_id) do update set enabled = true;
--
-- Revoke immediately without waiting for JWT expiration:
-- update zahir_internal.segmentation_readers set enabled=false
-- where user_id='REPLACE_WITH_AUTH_USER_UUID'::uuid;
--
-- Review existing policy/grant output again after applying. This script grants
-- no INSERT/UPDATE/DELETE rights and does not disable any existing RLS policy.
