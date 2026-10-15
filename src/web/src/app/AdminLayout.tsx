import { Layout, Menu } from 'antd'
import { Outlet, useNavigate } from 'react-router-dom'

const { Sider, Header, Content } = Layout

const items = [
  { key: '/assets', label: '资产与规则' },
  { key: '/tasks', label: '巡检与任务' },
  { key: '/findings', label: '风险结果' },
  { key: '/alerts', label: '告警与复测' },
  { key: '/reports', label: '报告' },
  { key: '/system', label: '审计与配置' },
]

export default function AdminLayout() {
  const nav = useNavigate()

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Sider>
        <Menu
          theme="dark"
          items={items}
          onClick={(e) => nav(e.key)}
        />
      </Sider>
      <Layout>
        <Header style={{ background: '#fff' }}>网站安全智能巡检管理端</Header>
        <Content style={{ padding: 24 }}>
          <Outlet />
        </Content>
      </Layout>
    </Layout>
  )
}