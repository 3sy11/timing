import OverviewPage from './pages/overview.js'
import BacktestPage from './pages/backtest.js'
import ResultsPage from './pages/results.js'
import DataPage from './pages/data.js'

const { createApp } = Vue

const app = createApp({
  data() { return { page: 'overview' } }
})

app.component('overview-page', OverviewPage)
app.component('backtest-page', BacktestPage)
app.component('results-page', ResultsPage)
app.component('data-page', DataPage)
app.mount('#app')
