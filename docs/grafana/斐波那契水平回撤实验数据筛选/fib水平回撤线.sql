-- Query A
WITH ranked AS (
  SELECT
    multiplier, leg_low, leg_high,
    ROW_NUMBER() OVER (PARTITION BY multiplier ORDER BY score DESC) AS rn
  FROM read_parquet(
    '/warehouse/timing/computation/fib_retracement/${compute_id:raw}/${symbol:raw}/${interval:raw}/result.parquet'
  )
)
SELECT
  to_timestamp(k.ts / 1000) AS time,
  k.open, k.high, k.low, k.close,
  s.leg_high AS "短期_0%",
  s.leg_low + (s.leg_high - s.leg_low) * 0.618 AS "短期_38.2%",
  (s.leg_low + s.leg_high) / 2 AS "短期_50%",
  s.leg_low + (s.leg_high - s.leg_low) * 0.382 AS "短期_61.8%",
  s.leg_low AS "短期_100%",
  m.leg_high AS "中期_0%",
  m.leg_low + (m.leg_high - m.leg_low) * 0.618 AS "中期_38.2%",
  (m.leg_low + m.leg_high) / 2 AS "中期_50%",
  m.leg_low + (m.leg_high - m.leg_low) * 0.382 AS "中期_61.8%",
  m.leg_low AS "中期_100%",
  l.leg_high AS "长期_0%",
  l.leg_low + (l.leg_high - l.leg_low) * 0.618 AS "长期_38.2%",
  (l.leg_low + l.leg_high) / 2 AS "长期_50%",
  l.leg_low + (l.leg_high - l.leg_low) * 0.382 AS "长期_61.8%",
  l.leg_low AS "长期_100%"
FROM read_parquet('/warehouse/timing/klines/${symbol:raw}/${interval:raw}/*.parquet') k
CROSS JOIN (SELECT * FROM ranked WHERE multiplier = 1 AND rn = 1) s
CROSS JOIN (SELECT * FROM ranked WHERE multiplier = 2 AND rn = 1) m
CROSS JOIN (SELECT * FROM ranked WHERE multiplier = 3 AND rn = 1) l
ORDER BY k.ts

-- Query B
SELECT
  to_timestamp(ts / 1000) AS time,
  level_price AS "信号价位"
FROM read_parquet('/warehouse/timing/signals/**/*.parquet')
WHERE symbol = '${symbol:raw}'
  AND interval = '${interval:raw}'
  AND analysis_id = '${analysis_id:raw}'
  AND compute_id = '${compute_id:raw}'
ORDER BY ts ASC

-- Query C
SELECT
  to_timestamp(ts / 1000) AS time,
  price * 1.008 AS "决策价位"
FROM read_parquet('/warehouse/timing/decisions/**/*.parquet')
WHERE decision_id = '${decision_id:raw}'
  AND analysis_id = '${analysis_id:raw}'
  AND symbol = '${symbol:raw}'
  AND action = 'submit'
ORDER BY ts ASC
