{{ config(materialized="incremental", unique_key="id") }}

select
  1 as id,
  {{ rowray_test_package.compat_literal(var("label")) }} as label,
  {% if flags.FULL_REFRESH %}'full-refresh'{% else %}'incremental'{% endif %} as load_mode
