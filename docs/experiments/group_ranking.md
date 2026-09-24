# group_ranking.parquet

路径：`computation/fib_retracement/{compute_id}/{symbol}/{interval}/group_ranking.parquet`

一行 = 数据末尾仍有效的一个 Fib 组。`rank <= rank_top_n`（默认 3）的组 `is_selected = true`。

分数只用 `line_events` 里 `target_kind = fib` 的拟合前（`prefit`）和拟合窗（`fitwin`）。生效之后的存活期不进分。比例 0 和 1 有过触碰时，`score_extrapolated` 最高加 `rank_w_extrapolated`（默认 0.5）。

`group_score = score_prefit + score_fitwin + score_structure + score_activity + score_extrapolated`。
