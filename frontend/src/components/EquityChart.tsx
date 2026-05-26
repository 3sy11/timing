import { useEffect, useRef } from 'react'
import * as echarts from 'echarts'
import type { Fill } from '../services/api'

interface Props { fills: Fill[]; initialBalance: number; height?: number }

export default function EquityChart({ fills, initialBalance, height = 250 }: Props) {
  const ref = useRef<HTMLDivElement>(null)
  const chartRef = useRef<echarts.ECharts | null>(null)

  useEffect(() => {
    if (!ref.current) return
    chartRef.current = echarts.init(ref.current, 'dark')
    const resizeOb = new ResizeObserver(() => chartRef.current?.resize())
    resizeOb.observe(ref.current)
    return () => { resizeOb.disconnect(); chartRef.current?.dispose() }
  }, [])

  useEffect(() => {
    if (!chartRef.current || !fills.length) return
    const sorted = [...fills].sort((a, b) => a.ts - b.ts)
    let bal = initialBalance
    const eqData: [number, number][] = [[sorted[0].ts, bal]]
    for (const f of sorted) {
      const cost = f.filled_price * f.filled_quantity
      bal += f.side === 'sell' ? cost - f.commission : -cost - f.commission
      eqData.push([f.ts, bal])
    }
    // 回撤
    let peak = eqData[0][1]
    const ddData: [number, number][] = []
    for (const [ts, eq] of eqData) {
      if (eq > peak) peak = eq
      ddData.push([ts, peak > 0 ? -((peak - eq) / peak) * 100 : 0])
    }

    chartRef.current.setOption({
      backgroundColor: '#141414',
      grid: [{ top: '8%', height: '42%', left: 60, right: 20 }, { top: '58%', height: '34%', left: 60, right: 20 }],
      xAxis: [
        { type: 'time', gridIndex: 0, axisLabel: { show: false } },
        { type: 'time', gridIndex: 1 },
      ],
      yAxis: [
        { type: 'value', gridIndex: 0, name: '权益' },
        { type: 'value', gridIndex: 1, name: '回撤%', max: 0 },
      ],
      series: [
        { type: 'line', data: eqData, xAxisIndex: 0, yAxisIndex: 0, showSymbol: false, lineStyle: { color: '#1976D2', width: 2 }, name: '权益' },
        { type: 'line', data: ddData, xAxisIndex: 1, yAxisIndex: 1, showSymbol: false, areaStyle: { color: 'rgba(229,57,53,0.3)' }, lineStyle: { color: '#E53935', width: 1 }, name: '回撤' },
      ],
      tooltip: { trigger: 'axis' },
    })
  }, [fills, initialBalance])

  return <div ref={ref} style={{ width: '100%', height }} />
}
