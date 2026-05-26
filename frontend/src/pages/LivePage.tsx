import { useEffect, useState } from 'react'
import { Select, Empty, Spin, Card } from 'antd'
import KlineChart from '../components/KlineChart'
import { fetchSymbols, fetchKlines, fetchSignals, type SymbolInfo, type Kline, type Signal } from '../services/api'

export default function LivePage() {
  const [symbols, setSymbols] = useState<SymbolInfo[]>([])
  const [selected, setSelected] = useState<string>('')
  const [klines, setKlines] = useState<Kline[]>([])
  const [signals, setSignals] = useState<Signal[]>([])
  const [loading, setLoading] = useState(false)

  useEffect(() => { fetchSymbols().then(setSymbols) }, [])

  const onSelect = async (val: string) => {
    setSelected(val)
    const [symbol, interval] = val.split('|')
    setLoading(true)
    const [k, s] = await Promise.all([fetchKlines(symbol, interval), fetchSignals(symbol, interval)])
    setKlines(k); setSignals(s); setLoading(false)
  }

  return (
    <div style={{ padding: 16 }}>
      <Select placeholder="选择品种" style={{ width: 300, marginBottom: 16 }} value={selected || undefined} onChange={onSelect}
        options={symbols.map(s => ({ label: `${s.symbol} / ${s.interval} (${s.count}条)`, value: `${s.symbol}|${s.interval}` }))} />
      {loading && <Spin />}
      {!loading && klines.length > 0 && (
        <Card styles={{ body: { padding: 0 } }}>
          <KlineChart klines={klines} signals={signals} height={600} />
        </Card>
      )}
      {!loading && selected && !klines.length && <Empty description="该品种无数据" />}
    </div>
  )
}
