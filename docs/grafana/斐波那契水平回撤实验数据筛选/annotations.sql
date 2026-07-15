-- Annotation: 信号详情
SELECT
  to_timestamp(ts / 1000) AS time,
  direction || ' Fib ' || ROUND(ratio * 100, 1)::VARCHAR || '%' AS title,
  '价位: ' || ROUND(level_price, 4)::VARCHAR
    || ' | 实际: ' || ROUND(touch_price, 4)::VARCHAR
    || '
强度: ' || ROUND(strength, 2)::VARCHAR
    || ' | 实验: ' || analysis_id
    || ' | 计算: ' || compute_id AS text,
  direction AS tags
FROM read_parquet('/warehouse/timing/signals/**/*.parquet')
WHERE symbol = '${symbol:raw}'
  AND interval = '${interval:raw}'
  AND analysis_id = '${analysis_id:raw}'
ORDER BY ts

-- Annotation: 决策详情
SELECT
  to_timestamp(ts / 1000) AS time,
  side || ' ' || ROUND(quantity, 3)::VARCHAR AS title,
  '方向: ' || direction || ' | 强度: ' || ROUND(strength, 2)::VARCHAR || ' | 价格: ' || ROUND(price, 4)::VARCHAR || ' | 原因: ' || reason AS text,
  side AS tags
FROM read_parquet('/warehouse/timing/decisions/**/*.parquet')
WHERE decision_id = '${decision_id:raw}'
  AND symbol = '${symbol:raw}'
  AND action = 'submit'
ORDER BY ts
