import { useEffect, useState } from 'react'
import { Table, Card, Statistic, Row, Col, Empty, Spin } from 'antd'
import KlineChart from '../components/KlineChart'
import EquityChart from '../components/EquityChart'
import { fetchRuns, fetchRun, type RunSummary, type RunDetail } from '../services/api'

export default function BacktestPage() {
  const [runs, setRuns] = useState<RunSummary[]>([])
  const [detail, setDetail] = useState<RunDetail | null>(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => { fetchRuns().then(r => setRuns(r.runs || [])) }, [])

  const loadDetail = async (runId: string) => {
    setLoading(true)
    const d = await fetchRun(runId)
    setDetail(d); setLoading(false)
  }

  const columns = [
    { title: 'Symbol', dataIndex: 'symbol', key: 'symbol', render: (_: string, r: RunSummary) => `${r.symbol}/${r.interval}` },
    { title: '参数', dataIndex: 'params', key: 'params', render: (p: Record<string, number>) => JSON.stringify(p).slice(0, 40) },
    { title: '收益', dataIndex: ['metrics', 'total_return'], key: 'ret', render: (v: number) => v != null ? `${(v * 100).toFixed(2)}%` : '-' },
    { title: '回撤', dataIndex: ['metrics', 'max_drawdown'], key: 'dd', render: (v: number) => v != null ? `${(v * 100).toFixed(2)}%` : '-' },
    { title: '夏普', dataIndex: ['metrics', 'sharpe_ratio'], key: 'sharpe', render: (v: number) => v?.toFixed(2) ?? '-' },
    { title: '交易数', dataIndex: ['metrics', 'trade_count'], key: 'trades' },
  ]

  if (detail) {
    const { result, metrics, params, symbol, interval } = detail
    const fills = result?.fills || []
    const klines = result?.klines || []
    const signals = result?.signals || []
    const initialBalance = result?.account?.initial_balance || 100000
    return (
      <div style={{ padding: 16 }}>
        <a onClick={() => setDetail(null)} style={{ marginBottom: 12, display: 'inline-block', cursor: 'pointer' }}>← 返回列表</a>
        <Card title={`${symbol}/${interval} — ${JSON.stringify(params)}`} style={{ marginBottom: 16 }}>
          <Row gutter={16}>
            <Col span={4}><Statistic title="收益" value={`${((metrics?.total_return || 0) * 100).toFixed(2)}%`} valueStyle={{ color: (metrics?.total_return || 0) >= 0 ? '#00C853' : '#D50000' }} /></Col>
            <Col span={4}><Statistic title="最大回撤" value={`${((metrics?.max_drawdown || 0) * 100).toFixed(2)}%`} /></Col>
            <Col span={4}><Statistic title="夏普" value={metrics?.sharpe_ratio?.toFixed(2) || '-'} /></Col>
            <Col span={4}><Statistic title="胜率" value={`${((metrics?.win_rate || 0) * 100).toFixed(1)}%`} /></Col>
            <Col span={4}><Statistic title="盈亏比" value={metrics?.profit_factor?.toFixed(2) || '-'} /></Col>
            <Col span={4}><Statistic title="交易数" value={metrics?.trade_count || 0} /></Col>
          </Row>
        </Card>
        {klines.length > 0 && <Card styles={{ body: { padding: 0 } }} style={{ marginBottom: 16 }}><KlineChart klines={klines} fills={fills} signals={signals} height={500} /></Card>}
        {fills.length > 0 && <Card styles={{ body: { padding: 0 } }}><EquityChart fills={fills} initialBalance={initialBalance} height={300} /></Card>}
        {!klines.length && <Empty description="无K线数据" />}
      </div>
    )
  }

  return (
    <div style={{ padding: 16 }}>
      {loading && <Spin />}
      {!runs.length && !loading && <Empty description="暂无回测记录" />}
      {runs.length > 0 && <Table dataSource={runs} columns={columns} rowKey="run_id" size="small"
        onRow={(r) => ({ onClick: () => loadDetail(r.run_id), style: { cursor: 'pointer' } })} />}
    </div>
  )
}
