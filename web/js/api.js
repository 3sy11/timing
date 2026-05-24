const API_BASE = ''
const WS_URL = `ws://${location.hostname}:8001`

export async function fetchJSON(path, opts = {}) {
  const res = await fetch(`${API_BASE}${path}`, {headers: {'Content-Type': 'application/json'}, ...opts})
  return res.json()
}

export function getStatus() { return fetchJSON('/api/dashboard/status') }
export function listRuns(limit = 50, offset = 0) { return fetchJSON(`/api/dashboard/runs?limit=${limit}&offset=${offset}`) }
export function getRun(runId) { return fetchJSON(`/api/dashboard/runs/${runId}`) }
export function listDatasets() { return fetchJSON('/api/dashboard/datasets') }

export function startBatch(payload) {
  return fetchJSON('/api/dashboard/batch', {method: 'POST', body: JSON.stringify(payload)})
}

export async function uploadData(symbol, interval, file) {
  const form = new FormData()
  form.append('symbol', symbol)
  form.append('interval', interval)
  form.append('file', file)
  const res = await fetch('/api/dashboard/upload', {method: 'POST', body: form})
  return res.json()
}

export class WsClient {
  constructor(onMessage) {
    this.onMessage = onMessage
    this.ws = null
    this._reconnectTimer = null
  }
  connect() {
    this.ws = new WebSocket(WS_URL)
    this.ws.onmessage = (e) => {
      try { this.onMessage(JSON.parse(e.data)) } catch {}
    }
    this.ws.onclose = () => { this._reconnectTimer = setTimeout(() => this.connect(), 3000) }
    this.ws.onerror = () => this.ws.close()
  }
  send(data) { if (this.ws?.readyState === 1) this.ws.send(JSON.stringify(data)) }
  close() { clearTimeout(this._reconnectTimer); this.ws?.close() }
}
