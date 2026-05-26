import { useEffect, useRef } from 'react'
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
  return (ts > 1e12 ? Math.floor(ts / 1000) : ts) as Time
}

export default function KlineChart({ klines, fills = [], signals = [], height = 500 }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const seriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null)

  useEffect(() => {
    if (!containerRef.current) return
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
  }, [height])

  useEffect(() => {
    if (!seriesRef.current || !klines.length) return
    const data: CandlestickData[] = klines.map(k => ({ time: tsToTime(k.ts), open: k.open, high: k.high, low: k.low, close: k.close }))
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
    markers.sort((a, b) => (a.time as number) - (b.time as number))
    createSeriesMarkers(seriesRef.current, markers)

    // Fibonacci 水平线
    const levels = new Set<number>()
    for (const s of signals) { if (s.level_price) levels.add(s.level_price) }
    for (const price of levels) {
      seriesRef.current.createPriceLine({ price, color: '#FFD700', lineWidth: 1, lineStyle: LineStyle.Dashed, title: '' })
    }

    chartRef.current?.timeScale().fitContent()
  }, [klines, fills, signals])

  return <div ref={containerRef} style={{ width: '100%' }} />
}
