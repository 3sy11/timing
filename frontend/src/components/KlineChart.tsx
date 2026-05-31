import { useEffect, useRef, useState } from 'react'
import { createChart, createSeriesMarkers, CandlestickSeries, LineStyle } from 'lightweight-charts'
import type { IChartApi, ISeriesApi, CandlestickData, Time, SeriesMarker } from 'lightweight-charts'
import type { Kline, Fill, Signal } from '../services/api'

interface Props {
  klines: Kline[]
  fills?: Fill[]
  signals?: Signal[]
  height?: number
}

function tsToTime(ts: number): Time {
  if (!ts || !Number.isFinite(ts)) return 0 as Time
  return (ts > 1e12 ? Math.floor(ts / 1000) : ts) as Time
}

function dedup(data: CandlestickData[]): CandlestickData[] {
  const seen = new Set<number>()
  return data.filter(d => { const t = d.time as number; if (seen.has(t)) return false; seen.add(t); return true })
}

export default function KlineChart({ klines, fills = [], signals = [], height = 500 }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const seriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null)
  const [error, setError] = useState<string>('')

  useEffect(() => {
    if (!containerRef.current) return
    try {
      const chart = createChart(containerRef.current, {
        width: containerRef.current.clientWidth, height,
        layout: { background: { color: '#141414' }, textColor: '#ccc' },
        grid: { vertLines: { color: '#222' }, horzLines: { color: '#222' } },
        timeScale: { timeVisible: true },
      })
      const series = chart.addSeries(CandlestickSeries, { upColor: '#26a69a', downColor: '#ef5350', borderUpColor: '#26a69a', borderDownColor: '#ef5350', wickUpColor: '#26a69a', wickDownColor: '#ef5350' })
      chartRef.current = chart
      seriesRef.current = series
      const resizeObserver = new ResizeObserver(() => {
        if (containerRef.current) chart.applyOptions({ width: containerRef.current.clientWidth })
      })
      resizeObserver.observe(containerRef.current)
      return () => { resizeObserver.disconnect(); chart.remove() }
    } catch (e: any) {
      console.error('[KlineChart] createChart failed:', e)
      setError(e.message || String(e))
    }
  }, [height])

  useEffect(() => {
    if (!seriesRef.current || !klines.length) return
    try {
      const raw: CandlestickData[] = klines
        .filter(k => k.ts && Number.isFinite(k.open))
        .map(k => ({ time: tsToTime(k.ts), open: k.open, high: k.high, low: k.low, close: k.close }))
      const data = dedup(raw).sort((a, b) => (a.time as number) - (b.time as number))
      console.log(`[KlineChart] setData: ${data.length} bars, first=${data[0]?.time}, last=${data[data.length - 1]?.time}`)
      seriesRef.current.setData(data)

      const markers: SeriesMarker<Time>[] = []
      for (const f of fills) {
        markers.push({
          time: tsToTime(f.ts), position: f.side === 'buy' ? 'belowBar' : 'aboveBar',
          color: f.side === 'buy' ? '#00C853' : '#D50000',
          shape: f.side === 'buy' ? 'arrowUp' : 'arrowDown',
          text: f.side === 'buy' ? 'B' : 'S',
        })
      }
      for (const s of signals) {
        markers.push({
          time: tsToTime(s.ts), position: s.direction === 'long' ? 'belowBar' : 'aboveBar',
          color: s.direction === 'long' ? '#2196F3' : '#FF9800',
          shape: 'circle', text: '',
        })
      }
      if (markers.length) {
        markers.sort((a, b) => (a.time as number) - (b.time as number))
        createSeriesMarkers(seriesRef.current, markers)
      }

      const levels = new Set<number>()
      for (const s of signals) { if (s.level_price) levels.add(s.level_price) }
      for (const price of levels) {
        seriesRef.current.createPriceLine({ price, color: '#FFD700', lineWidth: 1, lineStyle: LineStyle.Dashed, title: '' })
      }
      chartRef.current?.timeScale().fitContent()
    } catch (e: any) {
      console.error('[KlineChart] render data failed:', e)
      setError(e.message || String(e))
    }
  }, [klines, fills, signals])

  if (error) return <div style={{ padding: 24, color: '#f55' }}>图表渲染错误: {error}</div>
  return <div ref={containerRef} style={{ width: '100%' }} />
}
