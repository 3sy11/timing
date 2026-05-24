import { listDatasets, uploadData } from '../api.js'

export default {
  template: `
    <div>
      <h2 style="margin-bottom:20px">数据管理</h2>
      <div class="card">
        <div class="card-title">导入数据</div>
        <div class="form-row">
          <div><label>Symbol</label><input v-model="symbol"></div>
          <div><label>Interval</label><input v-model="interval"></div>
        </div>
        <div class="form-row">
          <div>
            <label>文件 (parquet/csv)</label>
            <input type="file" @change="onFile" accept=".parquet,.csv">
          </div>
          <div style="flex:0 0 120px">
            <button class="btn btn-primary" @click="doUpload" :disabled="uploading || !file">
              {{ uploading ? '导入中...' : '导入' }}
            </button>
          </div>
        </div>
        <div v-if="uploadResult" class="text-sm" style="margin-top:8px;color:var(--success)">
          导入成功: {{ uploadResult.symbol }}/{{ uploadResult.interval }} {{ uploadResult.count }}条
        </div>
      </div>
      <div class="card">
        <div class="card-title">已导入数据集</div>
        <table v-if="datasets.length">
          <thead><tr><th>Symbol</th><th>Interval</th><th>条数</th><th>文件</th><th>时间</th></tr></thead>
          <tbody>
            <tr v-for="d in datasets" :key="d.symbol+d.interval+d.uploaded_at">
              <td>{{ d.symbol }}</td>
              <td>{{ d.interval }}</td>
              <td>{{ d.count }}</td>
              <td class="text-sm">{{ d.filename }}</td>
              <td class="text-sm">{{ new Date(d.uploaded_at).toLocaleString() }}</td>
            </tr>
          </tbody>
        </table>
        <div v-else class="text-muted">暂无数据</div>
      </div>
    </div>
  `,
  data() { return { symbol: '', interval: '1d', file: null, uploading: false, uploadResult: null, datasets: [] } },
  async mounted() {
    const res = await listDatasets()
    this.datasets = res.datasets || []
  },
  methods: {
    onFile(e) { this.file = e.target.files[0] || null },
    async doUpload() {
      if (!this.file || !this.symbol) return
      this.uploading = true; this.uploadResult = null
      const res = await uploadData(this.symbol, this.interval, this.file)
      this.uploading = false
      if (res.error) { alert(res.error); return }
      this.uploadResult = res
      const ds = await listDatasets()
      this.datasets = ds.datasets || []
    }
  }
}
