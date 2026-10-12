import { createBrowserRouter, Navigate } from 'react-router-dom'
import type { ReactElement } from 'react'
import Login from '../pages/Login'
import AdminLayout from './AdminLayout'

function Guard({ children }: { children: ReactElement }) {
  return localStorage.getItem('token') ? children : <Navigate to="/login" />
}

function Placeholder({ name }: { name: string }) {
  return <div>{name} - 待开发</div>
}

export const router = createBrowserRouter([
  { path: '/login', element: <Login /> },
  {
    path: '/',
    element: (
      <Guard>
        <AdminLayout />
      </Guard>
    ),
    children: [
      { index: true, element: <Navigate to="/assets" /> },
      { path: 'assets', element: <Placeholder name="资产与规则管理" /> },
      { path: 'tasks', element: <Placeholder name="巡检与任务" /> },
      { path: 'findings', element: <Placeholder name="风险结果与可视化" /> },
      { path: 'alerts', element: <Placeholder name="告警与复测管理" /> },
      { path: 'reports', element: <Placeholder name="报告" /> },
      { path: 'system', element: <Placeholder name="审计与系统配置" /> },
    ],
  },
])