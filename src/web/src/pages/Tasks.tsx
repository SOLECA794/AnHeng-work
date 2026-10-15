import { useEffect, useState } from 'react'
import { Table, Tabs, Tag, Switch, Space, message, Button } from 'antd'
import { listSchedules, toggleSchedule } from '../api/schedules'
import { listTasks, retryTask } from '../api/tasks'
import type { Schedule, Task } from '../types/schedule'

const statusColor: Record<string, string> = {
  QUEUED: 'default', DISCOVERING: 'blue', SCANNING: 'blue',
  ANALYZING: 'orange', DONE: 'green', FAILED: 'red',
}

function ScheduleTable() {
  const [data, setData] = useState<Schedule[]>([])
  const [loading, setLoading] = useState(false)

  const load = async () => {
    setLoading(true)
    try { setData(await listSchedules()) } finally { setLoading(false) }
  }
  useEffect(() => { load() }, [])

  const onToggle = async (r: Schedule, enabled: boolean) => {
    await toggleSchedule(r.id, enabled); message.success('已更新'); load()
  }

  const columns = [
    { title: '计划名称', dataIndex: 'name' },
    { title: '资产', dataIndex: 'scopeName' },
    { title: '周期', dataIndex: 'intervalType' },
    { title: '下次执行', dataIndex: 'nextRunAt' },
    { title: '启停', dataIndex: 'enabled', render: (v: boolean, r: Schedule) =>
        <Switch checked={v} onChange={(c) => onToggle(r, c)} /> },
  ]

  return <Table rowKey="id" loading={loading} columns={columns} dataSource={data} />
}

function TaskTable() {
  const [data, setData] = useState<Task[]>([])
  const [loading, setLoading] = useState(false)

  const load = async () => {
    setLoading(true)
    try { setData(await listTasks()) } finally { setLoading(false) }
  }
  useEffect(() => { load() }, [])

  const onRetry = async (id: number) => {
    await retryTask(id); message.success('已提交重试'); load()
  }

  const columns = [
    { title: '任务ID', dataIndex: 'id' },
    { title: '资产', dataIndex: 'scopeName' },
    { title: '状态', dataIndex: 'status', render: (v: string) =>
        <Tag color={statusColor[v]}>{v}</Tag> },
    { title: '进度', dataIndex: 'progress', render: (v?: number) => v != null ? `${v}%` : '-' },
    { title: '创建时间', dataIndex: 'createdAt' },
    { title: '结束时间', dataIndex: 'finishedAt', render: (v?: string) => v || '-' },
    { title: '操作', render: (_: any, r: Task) =>
        r.status === 'FAILED' ? <a onClick={() => onRetry(r.id)}>重试</a> : '-' },
  ]

  return (
    <div>
      <Space style={{ marginBottom: 16 }}>
        <Button onClick={load}>刷新</Button>
      </Space>
      <Table rowKey="id" loading={loading} columns={columns} dataSource={data} />
    </div>
  )
}

export default function Tasks() {
  return (
    <Tabs defaultActiveKey="tasks" items={[
      { key: 'tasks', label: '检测任务', children: <TaskTable /> },
      { key: 'schedules', label: '巡检计划', children: <ScheduleTable /> },
    ]} />
  )
}