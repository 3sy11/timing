# 数据层 — DataEngine

## 职责

管理所有标的的 K 线数据，提供写入、查询和广播接口。是整个系统的数据基础设施。

---

## 存储架构

```
DataEngine.protocol
  └── TableCacheLayer（内存 dict，按 "symbol:interval" 分区）
        └── DuckDBProtocol（磁盘列式存储 cache/data.duckdb）
```

---

## Kline 数据模型

| 字段 | 类型 | 说明 |
|------|------|------|
| symbol | str | 分区键（如 "159363.OF"） |
| interval | str | 分区键（如 "1d"） |
| open | float | 开盘价 |
| high | float | 最高价 |
| low | float | 最低价 |
| close | float | 收盘价 |
| volume | float | 成交量 |
| ts | int | 时间戳（毫秒） |

分区 key = `"symbol:interval"`，同分区内按 `ts` 升序。

---

## 命令入口

| 命令 | destination | 说明 |
|------|------------|------|
| PushBars | data.DataEngine.PushBars | 写入 + 广播（on_bar 触发入口） |
| GetKlines | data.DataEngine.GetKlines | 查询（支持 start_ts/end_ts） |
| IngestKlinesFromFile | data.DataEngine.IngestKlinesFromFile | 从 parquet/csv 批量导入 |

PushBars 执行后 `_publish` 自动广播，AnalysisEngine 子服务通过 `subscriber` 声明接收。

---

## 文件清单

| 文件 | 内容 |
|------|------|
| data/app.py | DataEngine 服务 |
| data/models.py | PushBars / GetKlines / IngestKlinesFromFile |
| data/clients/file.py | parquet/csv 文件读取 |
| models/kline.py | Kline 模型 + DDL + table_schema() |
