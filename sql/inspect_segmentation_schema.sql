-- READ ONLY: run in Supabase SQL Editor before reviewing the access migration.
-- The app has not connected to a live database in this repository.
select table_schema, table_name, column_name, data_type, is_nullable
from information_schema.columns
where table_schema = 'public'
  and table_name in ('segment_summary', 'faktur_lolos_dengan_klaster')
order by table_name, ordinal_position;

-- Actual model labels and k; no customer names or invoice numbers.
-- If the first query shows other key names, adjust these JSON keys first.
select to_jsonb(s)->>'model' as actual_model,
       to_jsonb(s)->>'k' as actual_k,
       count(*) as summary_rows
from public.segment_summary s
group by 1, 2
order by 1, 2;

select schemaname, tablename, policyname, permissive, roles, cmd, qual
from pg_policies
where schemaname = 'public'
  and tablename in ('segment_summary', 'faktur_lolos_dengan_klaster');

select table_name, grantee, privilege_type
from information_schema.role_table_grants
where table_schema = 'public'
  and table_name in ('segment_summary', 'faktur_lolos_dengan_klaster')
order by table_name, grantee, privilege_type;
