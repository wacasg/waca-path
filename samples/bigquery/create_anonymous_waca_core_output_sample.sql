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
    -- Optional in WACA core output. When present, WACA path renders a recording
    -- link plus the Clarity-side IDs on the session row.
    'https://clarity.microsoft.com/player/samplproj/su001/rec0001' AS clarity_play_url,
    -- Example of a tenant-declared GA4 custom dimension. See
    -- tenant_config/example.yaml -> ui.custom_columns.
    'guest' AS membership_type,
    'sample_batch_20260501' AS batch_id
  ),
  STRUCT(
    DATE '2026-05-01',
    DATETIME '2026-05-01 10:01:00',
    1777611600000000,
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
    'https://clarity.microsoft.com/player/samplproj/su001/rec0001',
    'guest',
    'sample_batch_20260501'
  ),
  STRUCT(
    DATE '2026-05-01',
    DATETIME '2026-05-01 10:04:00',
    1777611600000000,
    'page_view',
    'anon_user_001',
    CAST(NULL AS STRING),
    FALSE,
    10001,
    'anon_user_001_10001',
    'https://example.test/contact',
    '/contact',
    'Example Contact',
    3000,
    'desktop',
    'Chrome',
    'Japan',
    'Tokyo',
    FALSE,
    'https://clarity.microsoft.com/player/samplproj/su001/rec0001',
    'guest',
    'sample_batch_20260501'
  ),
  STRUCT(
    DATE '2026-05-02',
    DATETIME '2026-05-02 00:05:00',
    1777611600000000,
    'session_start',
    'anon_user_001',
    CAST(NULL AS STRING),
    FALSE,
    10002,
    'anon_user_001_10002',
    'https://example.test/pricing',
    '/pricing',
    'Example Pricing',
    900,
    'desktop',
    'Chrome',
    'Japan',
    'Tokyo',
    FALSE,
    'https://clarity.microsoft.com/player/samplproj/su001/rec0002',
    'guest',
    'sample_batch_20260501'
  ),
  STRUCT(
    DATE '2026-05-02',
    DATETIME '2026-05-02 00:07:00',
    1777611600000000,
    'page_view',
    'anon_user_001',
    CAST(NULL AS STRING),
    FALSE,
    10002,
    'anon_user_001_10002',
    'https://example.test/checkout',
    '/checkout',
    'Example Checkout',
    5200,
    'desktop',
    'Chrome',
    'Japan',
    'Tokyo',
    FALSE,
    'https://clarity.microsoft.com/player/samplproj/su001/rec0002',
    'guest',
    'sample_batch_20260501'
  ),
  STRUCT(
    DATE '2026-05-02',
    DATETIME '2026-05-02 00:09:00',
    1777611600000000,
    'purchase',
    'anon_user_001',
    CAST(NULL AS STRING),
    FALSE,
    10002,
    'anon_user_001_10002',
    'https://example.test/checkout/thank-you',
    '/checkout/thank-you',
    'Thank You',
    2100,
    'desktop',
    'Chrome',
    'Japan',
    'Tokyo',
    TRUE,
    'https://clarity.microsoft.com/player/samplproj/su001/rec0002',
    'guest',
    'sample_batch_20260501'
  ),
  STRUCT(
    DATE '2026-05-02',
    DATETIME '2026-05-02 00:50:00',
    1777611600000000,
    'page_view',
    'anon_user_001',
    CAST(NULL AS STRING),
    FALSE,
    10003,
    'anon_user_001_10003',
    'https://example.test/docs',
    '/docs',
    'Example Docs',
    3300,
    'desktop',
    'Chrome',
    'Japan',
    'Tokyo',
    FALSE,
    'https://clarity.microsoft.com/player/samplproj/su001/rec0002',
    'guest',
    'sample_batch_20260501'
  ),
  STRUCT(
    DATE '2026-05-03',
    DATETIME '2026-05-03 09:00:00',
    1777611600000000,
    'session_start',
    'anon_user_002',
    'sample-user-002',
    TRUE,
    20001,
    'anon_user_002_20001',
    'https://example.test/',
    '/',
    'Example Home',
    800,
    'mobile',
    'Safari',
    'Japan',
    'Osaka',
    FALSE,
    'https://clarity.microsoft.com/player/samplproj/su002/rec0003',
    'member',
    'sample_batch_20260501'
  ),
  STRUCT(
    DATE '2026-05-03',
    DATETIME '2026-05-03 09:02:00',
    1777611600000000,
    'page_view',
    'anon_user_002',
    'sample-user-002',
    TRUE,
    20001,
    'anon_user_002_20001',
    'https://example.test/pricing',
    '/pricing',
    'Example Pricing',
    6100,
    'mobile',
    'Safari',
    'Japan',
    'Osaka',
    FALSE,
    'https://clarity.microsoft.com/player/samplproj/su002/rec0003',
    'member',
    'sample_batch_20260501'
  ),
  STRUCT(
    DATE '2026-05-03',
    DATETIME '2026-05-03 09:06:00',
    1777611600000000,
    'sign_up',
    'anon_user_002',
    'sample-user-002',
    TRUE,
    20001,
    'anon_user_002_20001',
    'https://example.test/signup',
    '/signup',
    'Example Sign Up',
    4400,
    'mobile',
    'Safari',
    'Japan',
    'Osaka',
    TRUE,
    'https://clarity.microsoft.com/player/samplproj/su001/rec0001',
    'member',
    'sample_batch_20260501'
  ),
  STRUCT(
    DATE '2026-05-04',
    DATETIME '2026-05-04 14:00:00',
    1777611600000000,
    'session_start',
    'anon_user_003',
    CAST(NULL AS STRING),
    FALSE,
    30001,
    'anon_user_003_30001',
    'https://example.test/',
    '/',
    'Example Home',
    500,
    'tablet',
    'Edge',
    'Japan',
    'Fukuoka',
    FALSE,
    CAST(NULL AS STRING),
    'guest',
    'sample_batch_20260501'
  ),
  STRUCT(
    DATE '2026-05-04',
    DATETIME '2026-05-04 14:01:00',
    1777611600000000,
    'page_view',
    'anon_user_003',
    CAST(NULL AS STRING),
    FALSE,
    30001,
    'anon_user_003_30001',
    'https://example.test/blog',
    '/blog',
    'Example Blog',
    2600,
    'tablet',
    'Edge',
    'Japan',
    'Fukuoka',
    FALSE,
    CAST(NULL AS STRING),
    'guest',
    'sample_batch_20260501'
  )
]);
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
