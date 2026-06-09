-- Create anonymous WACA core output sample tables for WACA path install tests.
--
-- Usage:
--   bq query --use_legacy_sql=false \
--     --parameter=project_id:STRING:your-gcp-project-id \
--     --parameter=dataset_id:STRING:waca_path_sample_core \
--     --parameter=location:STRING:asia-northeast1 \
--     < samples/bigquery/create_anonymous_waca_core_output_sample.sql
--
-- This dataset contains synthetic journeys only. Do not replace it with real
-- customer logs.

DECLARE project_id STRING DEFAULT @project_id;
DECLARE dataset_id STRING DEFAULT @dataset_id;
DECLARE location STRING DEFAULT @location;
DECLARE full_dataset STRING DEFAULT CONCAT(project_id, '.', dataset_id);

EXECUTE IMMEDIATE FORMAT(
  "CREATE SCHEMA IF NOT EXISTS `%s` OPTIONS(location='%s')",
  full_dataset,
  location
);

EXECUTE IMMEDIATE FORMAT("""
CREATE OR REPLACE TABLE `%s.micro_user_table` AS
SELECT * FROM UNNEST([
  STRUCT(
    DATE '2026-05-01' AS event_date,
    DATETIME '2026-05-01 10:00:00' AS event_timestamp,
    1777611600000000 AS event_timestamp_micros,
    'session_start' AS event_name,
    'anon_user_001' AS user_pseudo_id,
    CAST(NULL AS STRING) AS user_id,
    FALSE AS is_identified_user,
    10001 AS ga_session_id,
    'anon_user_001_10001' AS pseudonymous_session_id,
    'https://example.test/' AS page_location,
    '/' AS clean_page_path,
    'Example Home' AS page_title,
    1200 AS engagement_time_msec,
    'desktop' AS device_category,
    'Chrome' AS browser,
    'Japan' AS country,
    'Tokyo' AS region,
    FALSE AS is_key_event,
    'sample_batch_20260501' AS batch_id
  ),
  STRUCT(
    DATE '2026-05-01',
    DATETIME '2026-05-01 10:01:00',
    1777611660000000,
    'page_view',
    'anon_user_001',
    CAST(NULL AS STRING),
    FALSE,
    10001,
    'anon_user_001_10001',
    'https://example.test/pricing',
    '/pricing',
    'Example Pricing',
    4500,
    'desktop',
    'Chrome',
    'Japan',
    'Tokyo',
    FALSE,
    'sample_batch_20260501'
  ),
  STRUCT(
    DATE '2026-05-01',
    DATETIME '2026-05-01 10:05:00',
    1777611900000000,
    'purchase',
    'anon_user_001',
    CAST(NULL AS STRING),
    FALSE,
    10001,
    'anon_user_001_10001',
    'https://example.test/checkout/thank-you',
    '/checkout/thank-you',
    'Example Thank You',
    2600,
    'desktop',
    'Chrome',
    'Japan',
    'Tokyo',
    TRUE,
    'sample_batch_20260501'
  ),
  STRUCT(
    DATE '2026-05-02',
    DATETIME '2026-05-02 11:00:00',
    1777698000000000,
    'page_view',
    'anon_user_002',
    CAST(NULL AS STRING),
    FALSE,
    20001,
    'anon_user_002_20001',
    'https://example.test/articles/guide',
    '/articles/guide',
    'Example Guide',
    3800,
    'mobile',
    'Safari',
    'Japan',
    'Osaka',
    FALSE,
    'sample_batch_20260502'
  )
])
""", full_dataset);

EXECUTE IMMEDIATE FORMAT("""
CREATE OR REPLACE TABLE `%s.micro_items_table` AS
SELECT * FROM UNNEST([
  STRUCT(
    DATE '2026-05-01' AS event_date,
    DATETIME '2026-05-01 10:05:00' AS event_timestamp,
    1777611900000000 AS event_timestamp_micros,
    'purchase' AS event_name,
    'anon_user_001' AS user_pseudo_id,
    CAST(NULL AS STRING) AS user_id,
    FALSE AS is_identified_user,
    10001 AS ga_session_id,
    'sample_item_001' AS item_id,
    'Sample Starter Plan' AS item_name,
    'service' AS item_category,
    1200.0 AS price,
    1 AS quantity,
    'sample_batch_20260501' AS batch_id
  )
])
""", full_dataset);

SELECT
  full_dataset AS sample_dataset,
  'micro_user_table and micro_items_table created with anonymous synthetic data' AS status;
