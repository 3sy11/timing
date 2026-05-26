import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, Routes, Route, NavLink, Navigate } from 'react-router-dom'
import { ConfigProvider, theme, Layout, Menu } from 'antd'
import { LineChartOutlined, ExperimentOutlined } from '@ant-design/icons'
import LivePage from './pages/LivePage'
import BacktestPage from './pages/BacktestPage'

const { Sider, Content } = Layout

function App() {
  return (
    <ConfigProvider theme={{ algorithm: theme.darkAlgorithm }}>
      <BrowserRouter>
        <Layout style={{ minHeight: '100vh' }}>
          <Sider width={180} style={{ background: '#141414' }}>
            <div style={{ padding: '16px 24px', fontSize: 18, fontWeight: 700, color: '#fff' }}>Timing</div>
            <Menu theme="dark" mode="inline" defaultSelectedKeys={['live']} style={{ background: '#141414' }}
              items={[
                { key: 'live', icon: <LineChartOutlined />, label: <NavLink to="/live">生产数据</NavLink> },
                { key: 'backtest', icon: <ExperimentOutlined />, label: <NavLink to="/backtest">回测实验</NavLink> },
              ]} />
          </Sider>
          <Content style={{ background: '#1a1a1a' }}>
            <Routes>
              <Route path="/live" element={<LivePage />} />
              <Route path="/backtest" element={<BacktestPage />} />
              <Route path="*" element={<Navigate to="/live" replace />} />
            </Routes>
          </Content>
        </Layout>
      </BrowserRouter>
    </ConfigProvider>
  )
}

createRoot(document.getElementById('root')!).render(<StrictMode><App /></StrictMode>)
