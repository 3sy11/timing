-- Query A
WITH csih_raw AS (
  SELECT epoch_ms(ts)::DATE AS dt, close FROM read_parquet('/warehouse/timing/klines/CSIH00300/1d/*.parquet')
),
wi_raw AS (
  SELECT epoch_ms(ts)::DATE AS dt, close FROM read_parquet('/warehouse/timing/klines/885003.WI/1d/*.parquet')
),
common AS (
  SELECT c.dt, c.close AS csih_close, w.close AS wi_close
  FROM csih_raw c INNER JOIN wi_raw w ON c.dt = w.dt
),
union_kline AS (
  SELECT dt, csih_close AS close, 'CSIH00300' AS code FROM common
  UNION ALL
  SELECT dt, wi_close AS close, '885003.WI' AS code FROM common
),
with_growth AS (
  SELECT dt, code, close,
         (close - lag(close) OVER w) / NULLIF(lag(close) OVER w, 0) AS g_close
  FROM union_kline
  WINDOW w AS (PARTITION BY code ORDER BY dt ASC)
)
SELECT dt AS time, code,
       EXP(SUM(LN(g_close + 1)) OVER (PARTITION BY code ORDER BY dt ASC)) - 1 AS cum_growth
FROM with_growth
WHERE g_close IS NOT NULL AND g_close > -1
ORDER BY dt ASC
