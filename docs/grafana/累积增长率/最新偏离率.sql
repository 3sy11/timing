-- Query A
WITH csih AS (
  SELECT epoch_ms(ts)::DATE AS dt, close
  FROM read_parquet('/warehouse/timing/klines/CSIH00300/1d/*.parquet')
),
wi AS (
  SELECT epoch_ms(ts)::DATE AS dt, close
  FROM read_parquet('/warehouse/timing/klines/885003.WI/1d/*.parquet')
)
SELECT csih.close / wi.close - 1 AS "偏离率(CSIH/WI)"
FROM csih INNER JOIN wi ON csih.dt = wi.dt
ORDER BY csih.dt DESC
LIMIT 1
