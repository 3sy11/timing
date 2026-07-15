-- Query A
WITH raw AS (
  SELECT *, row_number() OVER () AS file_ord
  FROM read_parquet('/warehouse/timing/execution/${execution_id:raw}/positions.parquet')
  WHERE symbol = '${symbol:raw}'
), last AS (
  SELECT * FROM raw
  QUALIFY ROW_NUMBER() OVER (PARTITION BY ts ORDER BY file_ord DESC) = 1
)
SELECT to_timestamp(ts / 1000) AS time, realized_pnl AS "累积已实现盈亏"
FROM last ORDER BY ts ASC

-- Query B
SELECT
  to_timestamp(ts / 1000) AS time,
  0.0 AS "成交点"
FROM read_parquet('/warehouse/timing/execution/${execution_id:raw}/fills.parquet')
WHERE symbol = '${symbol:raw}'
ORDER BY ts ASC
