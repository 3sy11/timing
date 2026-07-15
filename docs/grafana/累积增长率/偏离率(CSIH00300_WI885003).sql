-- Query A
WITH csih AS (
  SELECT epoch_ms(ts)::DATE AS dt, close
  FROM read_parquet('/warehouse/timing/klines/CSIH00300/1d/*.parquet')
),
wi AS (
  SELECT epoch_ms(ts)::DATE AS dt, close
  FROM read_parquet('/warehouse/timing/klines/885003.WI/1d/*.parquet')
)
SELECT csih.dt AS time, csih.close / wi.close - 1 AS bias
FROM csih INNER JOIN wi ON csih.dt = wi.dt
ORDER BY csih.dt ASC

-- Query B
WITH csih AS (
  SELECT epoch_ms(ts)::DATE AS dt FROM read_parquet('/warehouse/timing/klines/CSIH00300/1d/*.parquet')
),
wi AS (
  SELECT epoch_ms(ts)::DATE AS dt FROM read_parquet('/warehouse/timing/klines/885003.WI/1d/*.parquet')
),
dates AS (
  SELECT csih.dt AS time FROM csih INNER JOIN wi ON csih.dt = wi.dt
)
SELECT time, 0.236 AS "236", 0.382 AS "382", 0.5 AS "500", 0.618 AS "618", 0.786 AS "786"
FROM dates ORDER BY time ASC
