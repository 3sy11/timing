-- Query A
SELECT
  to_timestamp(ts / 1000) AS "时间",
  symbol AS "品种",
  side AS "方向",
  ROUND(quantity, 4) AS "数量",
  ROUND(avg_price, 4) AS "均价",
  ROUND(realized_pnl, 6) AS "已实现盈亏"
FROM read_parquet('/warehouse/timing/execution/${execution_id:raw}/positions.parquet')
WHERE symbol = '${symbol:raw}'
ORDER BY ts ASC
