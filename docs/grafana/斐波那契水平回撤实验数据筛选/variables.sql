-- 变量: compute_id
SELECT DISTINCT compute_id FROM read_json('/warehouse/timing/computation/fib_retracement/*/*/*/manifest.json') ORDER BY compute_id

-- 变量: symbol
SELECT DISTINCT symbol FROM read_json('/warehouse/timing/computation/fib_retracement/${compute_id:raw}/*/*/manifest.json') ORDER BY symbol

-- 变量: interval
SELECT DISTINCT interval FROM read_json('/warehouse/timing/computation/fib_retracement/${compute_id:raw}/${symbol:raw}/*/manifest.json')

-- 变量: analysis_id
SELECT DISTINCT analysis_id FROM read_parquet('/warehouse/timing/signals/**/*.parquet') WHERE compute_id = '${compute_id:raw}' AND symbol = '${symbol:raw}' AND interval = '${interval:raw}' ORDER BY analysis_id

-- 变量: decision_id
SELECT DISTINCT decision_id FROM read_parquet('/warehouse/timing/decisions/**/*.parquet') WHERE analysis_id = '${analysis_id:raw}' AND symbol = '${symbol:raw}' ORDER BY decision_id

-- 变量: execution_id
SELECT DISTINCT execution_id FROM read_parquet('/warehouse/timing/execution/*/orders.parquet') WHERE decision_id = '${decision_id:raw}' AND symbol = '${symbol:raw}' ORDER BY execution_id
