"""RunBacktest + MergeBacktest — 回测命令。

用法：
  # 单进程回测
  python main.py execute RunBacktest --config config.toml --backtest_config bt_a.toml --timeout 7200

  # 多进程并行（用 config_backtest.toml 避免端口/锁冲突）
  python main.py execute RunBacktest --config config_backtest.toml --backtest_config bt_a.toml --run_id exp_a --timeout 7200 &
  python main.py execute RunBacktest --config config_backtest.toml --backtest_config bt_b.toml --run_id exp_b --timeout 7200 &

  # 合并所有临时回测结果到主库
  python main.py execute MergeBacktest --config config_backtest.toml

架构：
  - RunBacktest 内部串行执行，写入 bt_tmp/<run_id>.duckdb 单文件
  - 多进程并行通过多次 execute 不同 config_backtest.toml 实现（进程级隔离）
  - K 线优先从 parquet 直读（ods_dir），无主库锁依赖
  - MergeBacktest 用 ATTACH + INSERT OR REPLACE 合并小文件到主库
"""
import os, time, tomllib, logging, uuid, glob
from typing import Any, ClassVar
import duckdb
from mode.utils.imports import smart_import
from bollydog.globals import app
from bollydog.models.base import BaseCommand
from timing.adapters.duckdb import TimingDuckDBProtocol
from timing.common.clock import SimulatedClock

log = logging.getLogger(__name__)
_MERGE_TABLES = ("signals", "analysis", "decisions")


class RunBacktest(BaseCommand):
    """串行回测命令 — 读取指定 backtest toml，顺序执行所有 service。

    结果写入 bt_tmp/<run_id>.duckdb，不触碰主库。
    多进程并行靠外部同时 execute 不同 toml 实现。
    """
    destination: ClassVar[str] = "backtest.BacktestApp.RunBacktest"
    expire_time: float = 7200
    backtest_config: str = ""
    run_id: str = ""

    async def __call__(self, *args, **kwargs) -> Any:
        bt_conf = self._load_config()
        if bt_conf is None:
            return None
        run_id = self.run_id or bt_conf.get("run_id", "") or f"bt_{uuid.uuid4().hex[:8]}"

        main_db = TimingDuckDBProtocol.shared()
        data_root = os.path.dirname(main_db.url)
        tmp_dir = os.path.join(data_root, "bt_tmp")
        os.makedirs(tmp_dir, exist_ok=True)
        tmp_path = os.path.join(tmp_dir, f"{run_id}.duckdb")

        svcs = self._create_services(bt_conf)
        if not svcs:
            log.error('[回测] 配置中无有效 service'); return None

        tmp_db = TimingDuckDBProtocol(url=tmp_path)
        await tmp_db.on_start()

        # 预加载 K 线 — 优先从 parquet 直读（多进程无锁），fallback 到主库
        ods_dir = bt_conf.get("ods_dir", "") or getattr(app, '_ods_dir', 'warehouse/ods')
        klines_cache = {}
        for svc_info in svcs:
            key = f'{svc_info["symbol"]}:{svc_info["interval"]}'
            if key not in klines_cache:
                klines_cache[key] = self._load_klines(svc_info["symbol"], svc_info["interval"], ods_dir)

        symbols_desc = ", ".join(f'{s["symbol"]}/{s["interval"]}' for s in svcs)
        tmp_db.adapter.execute('INSERT OR REPLACE INTO runs VALUES (?,?,?,?,?,?)',
                               [run_id, int(time.time()), "running", "backtest", symbols_desc,
                                f'{{"services": {len(svcs)}, "symbols": "{symbols_desc}"}}'])
        log.info(f'[回测] run_id={run_id} 共{len(svcs)}个任务串行执行 → {tmp_path}')

        # 串行执行每个 service
        total_signals = 0
        clock = SimulatedClock()
        for idx, svc_info in enumerate(svcs):
            cnt = await self._run_service(svc_info, run_id, klines_cache, tmp_db, clock)
            total_signals += cnt
            log.info(f'[回测] [{idx+1}/{len(svcs)}] {svc_info["alias"]} 完成 信号={cnt}')

        tmp_db.adapter.execute('INSERT OR REPLACE INTO runs VALUES (?,?,?,?,?,?)',
                               [run_id, int(time.time()), "completed", "backtest", symbols_desc,
                                f'{{"services": {len(svcs)}, "total_signals": {total_signals}}}'])

        log.info(f'[回测] 全部完成 run_id={run_id} 信号={total_signals} 文件={tmp_path}')
        return {"run_id": run_id, "services": len(svcs), "symbols": symbols_desc,
                "signals_count": total_signals, "tmp_path": tmp_path}

    def _load_config(self) -> dict | None:
        path = self.backtest_config or getattr(app, '_bt_config_path', 'backtest.toml')
        try:
            with open(path, 'rb') as f:
                return tomllib.load(f)
        except FileNotFoundError:
            log.error(f'[回测] 配置文件不存在: {path}'); return None

    def _load_klines(self, symbol: str, interval: str, ods_dir: str) -> list[dict]:
        """从 parquet 直读 K 线（进程独立，无锁）。fallback 用 read-only 连接读主库。"""
        path = os.path.join(ods_dir, f"{symbol}.parquet")
        if os.path.exists(path):
            try:
                conn = duckdb.connect(":memory:")
                result = conn.execute(f"SELECT * FROM read_parquet('{path}') ORDER BY ts").fetchall()
                cols = [d[0] for d in conn.execute(f"SELECT * FROM read_parquet('{path}') LIMIT 0").description]
                conn.close()
                log.info(f'[回测] 从 parquet 加载 {symbol}/{interval} {len(result)}条')
                return [dict(zip(cols, r)) for r in result]
            except Exception as e:
                log.warning(f'[回测] 读取 parquet 失败 {path}: {e}')
        # fallback: read-only 连接主库
        main_db = TimingDuckDBProtocol.shared()
        try:
            result = main_db.adapter.execute(
                'SELECT * FROM klines WHERE symbol=? AND "interval"=? ORDER BY ts', [symbol, interval]).fetchall()
            cols = main_db.columns("klines")
            log.info(f'[回测] 从主库加载 {symbol}/{interval} {len(result)}条')
            return [dict(zip(cols, r)) for r in result]
        except Exception as e:
            log.warning(f'[回测] 从主库获取 {symbol}/{interval} 失败: {e}'); return []

    def _create_services(self, bt_conf: dict) -> list[dict]:
        results = []
        for i, svc_conf in enumerate(bt_conf.get("services", [])):
            symbol = svc_conf.get("symbol", "")
            if not symbol:
                continue
            base_cls = smart_import(svc_conf["module"])
            alias = f'{base_cls.alias}_{i}'
            svc_cls = type(alias, (base_cls,), {'alias': alias})
            svc = svc_cls()
            if svc_conf.get("config"):
                svc.config = svc_conf["config"] if not isinstance(svc.config, dict) else {**(svc.config or {}), **svc_conf["config"]}
            results.append({"svc": svc, "alias": alias, "symbol": symbol,
                            "interval": svc_conf.get("interval", "1d"),
                            "warmup_bars": svc_conf.get("warmup_bars", 200)})
        return results

    async def _run_service(self, svc_info: dict, run_id: str, klines_cache: dict,
                           tmp_db: TimingDuckDBProtocol, clock: SimulatedClock) -> int:
        """串行跑单个 service。"""
        svc = svc_info["svc"]
        symbol, interval, warmup_n = svc_info["symbol"], svc_info["interval"], svc_info["warmup_bars"]
        alias = svc_info["alias"]

        key = f'{symbol}:{interval}'
        klines = klines_cache.get(key, [])
        if not klines or len(klines) <= warmup_n:
            log.warning(f'[回测] {alias} {symbol}/{interval} 数据不足'); return 0

        svc.run_id = run_id
        svc.db = tmp_db
        svc.clock = clock
        svc._bt_klines = klines

        warmup_data = klines[:warmup_n]
        replay_data = klines[warmup_n:]
        await svc._warmup(symbol, interval, warmup_data)
        log.info(f'[回测] {alias} {symbol}/{interval} warmup={warmup_n} replay={len(replay_data)}')

        svc_signals = 0
        sig_batch = []
        for i, bar in enumerate(replay_data):
            clock.set_time_ms(int(bar["ts"]))
            result = await svc._process_bar(symbol, interval, bar)
            if result:
                for sig in result.get("signals", []):
                    svc_signals += 1
                    sig_batch.append([run_id, symbol, interval, sig.get("ts", clock.now_ms()),
                                      sig.get("direction", "neutral"), sig.get("strength", 0.0),
                                      sig.get("touch_price", sig.get("price", 0.0)), alias,
                                      sig.get("level_price", sig.get("level", 0.0)), None])
            if len(sig_batch) >= 500:
                tmp_db.adapter.executemany('INSERT OR REPLACE INTO signals VALUES (?,?,?,?,?,?,?,?,?,?)', sig_batch)
                sig_batch.clear()
            if (i + 1) % 500 == 0:
                log.info(f'[回测] {alias} 进度 {i+1}/{len(replay_data)} 信号={svc_signals}')
        if sig_batch:
            tmp_db.adapter.executemany('INSERT OR REPLACE INTO signals VALUES (?,?,?,?,?,?,?,?,?,?)', sig_batch)

        svc._bt_klines = None
        return svc_signals


class MergeBacktest(BaseCommand):
    """合并回测临时文件到主库。

    扫描 bt_tmp/ 下所有 .duckdb → ATTACH → INSERT OR REPLACE → 清理。
    """
    destination: ClassVar[str] = "backtest.BacktestApp.MergeBacktest"
    expire_time: float = 600
    keep_files: bool = False

    async def __call__(self, *args, **kwargs) -> Any:
        main_db = TimingDuckDBProtocol.shared()
        if not main_db.adapter:
            await main_db.on_start()
        data_root = os.path.dirname(main_db.url)
        tmp_dir = os.path.join(data_root, "bt_tmp")
        if not os.path.isdir(tmp_dir):
            log.info('[合并] bt_tmp 目录不存在，无需合并'); return {"merged": 0, "files": 0}

        tmp_files = sorted(glob.glob(os.path.join(tmp_dir, "*.duckdb")))
        if not tmp_files:
            log.info('[合并] 无待合并文件'); return {"merged": 0, "files": 0}

        log.info(f'[合并] 发现 {len(tmp_files)} 个临时文件')
        conn = main_db.adapter
        total = 0
        merged_files = []
        for i, path in enumerate(tmp_files):
            alias = f"bt_{i}"
            try:
                conn.execute(f"ATTACH '{path}' AS {alias} (READ_ONLY)")
                file_total = 0
                for table in (*_MERGE_TABLES, "runs"):
                    try:
                        cnt = conn.execute(f"SELECT COUNT(*) FROM {alias}.{table}").fetchone()[0]
                        if cnt > 0:
                            conn.execute(f"INSERT OR REPLACE INTO main.{table} SELECT * FROM {alias}.{table}")
                            file_total += cnt
                    except Exception:
                        pass
                conn.execute(f"DETACH {alias}")
                total += file_total
                merged_files.append(path)
                run_id = os.path.basename(path).replace(".duckdb", "")
                log.info(f'[合并] {run_id} → {file_total}行')
            except Exception as e:
                log.warning(f'[合并] 跳过 {path}: {e}')

        if not self.keep_files:
            for p in merged_files:
                os.remove(p)
            log.info(f'[合并] 已清理 {len(merged_files)} 个临时文件')

        log.info(f'[合并] 完成 合并{len(merged_files)}个文件 共{total}行')
        return {"merged": total, "files": len(merged_files)}
