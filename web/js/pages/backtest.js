import { startBatch, WsClient } from '../api.js'

export default {
  template: `
    <div>
      <h2 style="margin-bottom:20px">批量回测</h2>
      <div class="card">
        <div class="card-title">基础配置</div>
        <div class="form-row">
          <div><label>Symbol</label><input v-model="symbol"></div>
          <div><label>Interval</label><input v-model="interval"></div>
          <div><label>Warmup Bars</label><input v-model.number="warmupBars" type="number"></div>
        </div>
      </div>
      <div class="card">
        <div class="card-title">参数网格</div>
        <div v-for="(row, i) in paramRows" :key="i" class="form-row">
          <div style="flex:.4"><input v-model="row.key" placeholder="参数名"></div>
          <div style="flex:1"><input v-model="row.values" placeholder="值列表，逗号分隔 (如 0.3,0.5,0.8)"></div>
          <div style="flex:0 0 40px"><button class="btn btn-sm" @click="paramRows.splice(i,1)">✕</button></div>
        </div>
        <button class="btn btn-sm" @click="paramRows.push({key:'',values:''})">+ 添加参数</button>
      </div>
      <button class="btn btn-primary" @click="submit" :disabled="running" style="margin-bottom:20px">
        {{ running ? '运行中...' : '开始回测' }}
      </button>
      <div class="card" v-if="running || progress.length">
        <div class="card-title">进度 {{ completedCount }}/{{ totalRuns }}</div>
        <div class="progress-bar mb-16"><div class="fill" :style="{width: progressPct + '%'}"></div></div>
        <table v-if="progress.length">
          <thead><tr><th>#</th><th>参数</th><th>收益</th><th>回撤</th><th>状态</th></tr></thead>
          <tbody>
            <tr v-for="p in progress" :key="p.run_index">
              <td>{{ p.run_index + 1 }}</td>
              <td class="text-sm">{{ JSON.stringify(p.params).slice(0,50) }}</td>
              <td>{{ p.metrics?.total_return ? (p.metrics.total_return*100).toFixed(2)+'%' : '-' }}</td>
              <td>{{ p.metrics?.max_drawdown ? (p.metrics.max_drawdown*100).toFixed(2)+'%' : '-' }}</td>
              <td><span :class="'badge badge-'+p.status">{{ p.status }}</span></td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  `,
  data() {
    return {
      symbol: '159363.OF', interval: '1d', warmupBars: 200,
      paramRows: [{key: 'touch_tolerance', values: '0.3,0.5,0.8'}, {key: 'min_strength', values: '0.5,0.6,0.7'}],
      running: false, progress: [], totalRuns: 0, ws: null
    }
  },
  computed: {
    completedCount() { return this.progress.filter(p => p.status !== 'running').length },
    progressPct() { return this.totalRuns ? (this.completedCount / this.totalRuns * 100) : 0 }
  },
  methods: {
    async submit() {
      const grid = {}
      for (const row of this.paramRows) {
        if (!row.key || !row.values) continue
        grid[row.key] = row.values.split(',').map(v => parseFloat(v.trim())).filter(v => !isNaN(v))
      }
      this.progress = []
      this.running = true
      this.connectWs()
      const res = await startBatch({symbol: this.symbol, interval: this.interval, warmup_bars: this.warmupBars, param_grid: grid})
      if (res.error) { alert(res.error); this.running = false }
    },
    connectWs() {
      if (this.ws) this.ws.close()
      this.ws = new WsClient((msg) => {
        const data = msg.data || msg
        if (data.destination && data.destination.includes('BacktestProgress')) {
          this.totalRuns = data.total_runs || this.totalRuns
          const existing = this.progress.find(p => p.run_index === data.run_index)
          if (existing) Object.assign(existing, data)
          else this.progress.push(data)
          if (data.status === 'completed' || data.status === 'failed') {
            if (this.completedCount >= this.totalRuns) this.running = false
          }
        }
      })
      this.ws.connect()
    }
  },
  beforeUnmount() { if (this.ws) this.ws.close() }
}
