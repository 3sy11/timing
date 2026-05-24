import { getStatus, listRuns } from '../api.js'

export default {
  template: `
    <div>
      <h2 style="margin-bottom:20px">系统概览</h2>
      <div class="cards-row">
        <div class="card" v-for="s in services" :key="s.alias">
          <div class="card-title">{{ s.alias }}</div>
          <div class="card-value">
            <span :class="'badge badge-' + (s.running ? 'completed' : 'failed')">{{ s.running ? '运行中' : '停止' }}</span>
          </div>
        </div>
      </div>
      <div class="card" v-if="currentJob">
        <div class="card-title">当前任务</div>
        <div style="margin-bottom:8px">{{ currentJob.job_id }} - {{ currentJob.status }}</div>
        <div class="progress-bar"><div class="fill" :style="{width: jobProgress + '%'}"></div></div>
        <div class="text-sm text-muted" style="margin-top:4px">{{ currentJob.completed_runs }}/{{ currentJob.total_runs }}</div>
      </div>
      <div class="card">
        <div class="card-title">最近回测</div>
        <table v-if="recentRuns.length">
          <thead><tr><th>ID</th><th>参数</th><th>收益</th><th>回撤</th><th>状态</th></tr></thead>
          <tbody>
            <tr v-for="r in recentRuns" :key="r.run_id">
              <td class="text-sm">{{ r.run_id }}</td>
              <td class="text-sm">{{ JSON.stringify(r.params).slice(0,40) }}</td>
              <td>{{ r.metrics?.total_return ? (r.metrics.total_return * 100).toFixed(2) + '%' : '-' }}</td>
              <td>{{ r.metrics?.max_drawdown ? (r.metrics.max_drawdown * 100).toFixed(2) + '%' : '-' }}</td>
              <td><span :class="'badge badge-' + r.status">{{ r.status }}</span></td>
            </tr>
          </tbody>
        </table>
        <div v-else class="text-muted">暂无回测记录</div>
      </div>
    </div>
  `,
  data() { return { services: [], currentJob: null, recentRuns: [] } },
  async mounted() {
    const status = await getStatus()
    this.services = status.services || []
    this.currentJob = status.current_job
    const runs = await listRuns(5)
    this.recentRuns = runs.runs || []
  }
}
