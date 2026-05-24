import { listRuns, getRun } from '../api.js'

export default {
  template: `
    <div>
      <h2 style="margin-bottom:20px">回测结果</h2>
      <div class="card" v-if="!selectedRun">
        <div class="card-title">全部记录 ({{ total }})</div>
        <table v-if="runs.length">
          <thead><tr><th>ID</th><th>Symbol</th><th>参数</th><th>收益</th><th>回撤</th><th>夏普</th><th>交易数</th><th>操作</th></tr></thead>
          <tbody>
            <tr v-for="r in runs" :key="r.run_id">
              <td class="text-sm">{{ r.run_id }}</td>
              <td>{{ r.symbol }}/{{ r.interval }}</td>
              <td class="text-sm">{{ JSON.stringify(r.params).slice(0,35) }}</td>
              <td :style="{color: (r.metrics?.total_return||0)>=0?'var(--success)':'var(--error)'}">{{ fmtPct(r.metrics?.total_return) }}</td>
              <td>{{ fmtPct(r.metrics?.max_drawdown) }}</td>
              <td>{{ r.metrics?.sharpe_ratio?.toFixed(2) || '-' }}</td>
              <td>{{ r.metrics?.trade_count || 0 }}</td>
              <td><button class="btn btn-sm btn-primary" @click="viewDetail(r.run_id)">详情</button></td>
            </tr>
          </tbody>
        </table>
        <div v-else class="text-muted">暂无记录</div>
      </div>
      <div v-if="selectedRun">
        <button class="btn btn-sm" @click="selectedRun=null" style="margin-bottom:12px">← 返回列表</button>
        <div class="card">
          <div class="card-title">{{ selectedRun.run_id }} | {{ selectedRun.symbol }}/{{ selectedRun.interval }}</div>
          <div class="text-sm text-muted mb-16">参数: {{ JSON.stringify(selectedRun.params) }}</div>
          <div class="cards-row">
            <div class="card"><div class="card-title">收益</div><div class="card-value">{{ fmtPct(selectedRun.metrics?.total_return) }}</div></div>
            <div class="card"><div class="card-title">回撤</div><div class="card-value">{{ fmtPct(selectedRun.metrics?.max_drawdown) }}</div></div>
            <div class="card"><div class="card-title">夏普</div><div class="card-value">{{ selectedRun.metrics?.sharpe_ratio?.toFixed(2) || '-' }}</div></div>
            <div class="card"><div class="card-title">交易数</div><div class="card-value">{{ selectedRun.metrics?.trade_count || 0 }}</div></div>
          </div>
        </div>
        <div class="card"><div id="chart-kline" class="chart-container"></div></div>
        <div class="card"><div id="chart-equity" class="chart-container" style="min-height:250px"></div></div>
      </div>
    </div>
  `,
  data() { return { runs: [], total: 0, selectedRun: null } },
  async mounted() { await this.loadRuns() },
  methods: {
    fmtPct(v) { return v != null ? (v * 100).toFixed(2) + '%' : '-' },
    async loadRuns() {
      const res = await listRuns(100)
      this.runs = res.runs || []; this.total = res.total || 0
    },
    async viewDetail(runId) {
      const detail = await getRun(runId)
      if (!detail) return
      this.selectedRun = detail
      this.$nextTick(() => this.renderCharts(detail))
    },
    renderCharts(detail) {
      const result = detail.result || {}
      const klines = result.klines || []
      const fills = result.fills || []
      if (klines.length) {
        const ts = klines.map(k => k.ts)
        const trace = {x: ts, open: klines.map(k=>k.open), high: klines.map(k=>k.high),
                       low: klines.map(k=>k.low), close: klines.map(k=>k.close),
                       type: 'candlestick', name: 'K线', increasing: {line:{color:'#26a69a'}}, decreasing: {line:{color:'#ef5350'}}}
        const traces = [trace]
        const buys = fills.filter(f=>f.side==='buy')
        const sells = fills.filter(f=>f.side==='sell')
        if (buys.length) traces.push({x: buys.map(f=>f.ts), y: buys.map(f=>f.filled_price), mode:'markers', name:'买入', marker:{size:9,color:'#00C853',symbol:'triangle-up'}})
        if (sells.length) traces.push({x: sells.map(f=>f.ts), y: sells.map(f=>f.filled_price), mode:'markers', name:'卖出', marker:{size:9,color:'#D50000',symbol:'triangle-down'}})
        Plotly.newPlot('chart-kline', traces, {title:'K线 + 交易点', template:'plotly_dark', xaxis:{rangeslider:{visible:false}}, height:400, margin:{t:40,b:40}})
      }
      // 权益曲线（从 fills 重建）
      if (fills.length) {
        const initial = detail.result?.account?.initial_balance || 100000
        let bal = initial
        const eqTs = [0], eqVal = [initial]
        for (const f of fills.sort((a,b)=>a.ts-b.ts)) {
          const cost = f.filled_price * f.filled_quantity
          bal += f.side === 'sell' ? cost - f.commission : -cost - f.commission
          eqTs.push(f.ts); eqVal.push(bal)
        }
        Plotly.newPlot('chart-equity', [{x: eqTs, y: eqVal, mode:'lines', name:'权益', line:{color:'#1976D2',width:2}}],
          {title:'权益曲线', template:'plotly_dark', height:250, margin:{t:40,b:40}})
      }
    }
  }
}
