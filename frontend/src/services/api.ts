const BASE = ''

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`)
  return res.json()
}

export interface Kline { ts: number; open: number; high: number; low: number; close: number; volume: number }
export interface Signal { ts: number; direction: string; strength: number; touch_price: number; level_price: number; source: string }
export interface Fill { order_id: string; symbol: string; side: string; filled_price: number; filled_quantity: number; commission: number; ts: number }
export interface SymbolInfo { symbol: string; interval: string; count: number; first_ts: number; last_ts: number }
export interface Metrics { total_return: number; max_drawdown: number; win_rate: number; sharpe_ratio: number; profit_factor: number; trade_count: number }
export interface RunSummary { run_id: string; symbol: string; interval: string; params: Record<string, number>; metrics: Metrics; status: string }
export interface RunDetail extends RunSummary { result: { klines: Kline[]; fills: Fill[]; signals: Signal[]; account: { initial_balance: number; total: number } } }

export const fetchSymbols = () => get<SymbolInfo[]>('/api/data/symbols')
export const fetchKlines = (symbol: string, interval: string) => get<Kline[]>(`/api/data/klines?symbol=${symbol}&interval=${interval}`)
export const fetchSignals = (symbol: string, interval: string) => get<Signal[]>(`/api/data/signals?symbol=${symbol}&interval=${interval}`)
export const fetchRuns = (limit = 100) => get<{ runs: RunSummary[]; total: number }>(`/api/dashboard/runs?limit=${limit}`)
export const fetchRun = (runId: string) => get<RunDetail>(`/api/dashboard/runs/${runId}`)
