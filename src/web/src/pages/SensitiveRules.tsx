import { useEffect, useState } from 'react'
import { Table, Button, Space, Tag, Modal, Form, Input, Select, Switch, message } from 'antd'
import { listRules, createRule, updateRule, toggleRule } from '../api/sensitive'
import type { SensitiveRule } from '../types/sensitive'

const typeLabel: Record<string, string> = {
  KEYWORD: '关键词', REGEX: '正则', FILE_TYPE: '文件类型', SAMPLE: '样例',
}

export default function SensitiveRules() {
  const [data, setData] = useState<SensitiveRule[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [loading, setLoading] = useState(false)
  const [open, setOpen] = useState(false)
  const [editing, setEditing] = useState<SensitiveRule | null>(null)
  const [form] = Form.useForm()

  const load = async () => {
    setLoading(true)
    try {
      const res = await listRules({ page, size: 10 })
      setData(res.list); setTotal(res.total)
    } finally { setLoading(false) }
  }

  useEffect(() => { load() }, [page])

  const openCreate = () => { setEditing(null); form.resetFields(); setOpen(true) }
  const openEdit = (r: SensitiveRule) => { setEditing(r); form.setFieldsValue(r); setOpen(true) }

  const onSubmit = async () => {
    const v = await form.validateFields()
    if (editing) await updateRule(editing.id, v)
    else await createRule(v)
    message.success(editing ? '修改成功' : '新增成功')
    setOpen(false); load()
  }

  const onToggle = async (r: SensitiveRule, enabled: boolean) => {
    await toggleRule(r.id, enabled); message.success(enabled ? '已启用' : '已停用'); load()
  }

  const columns = [
    { title: '规则名称', dataIndex: 'name' },
    { title: '类型', dataIndex: 'ruleType', render: (v: string) => <Tag>{typeLabel[v] || v}</Tag> },
    { title: '匹配模式', dataIndex: 'pattern', ellipsis: true },
    { title: '级别', dataIndex: 'severity', render: (v: string) => <Tag color="orange">{v}</Tag> },
    { title: '启停', dataIndex: 'enabled', render: (v: boolean, r: SensitiveRule) =>
        <Switch checked={v} onChange={(c) => onToggle(r, c)} /> },
    { title: '创建时间', dataIndex: 'createdAt' },
    { title: '操作', render: (_: any, r: SensitiveRule) => <a onClick={() => openEdit(r)}>编辑</a> },
  ]

  return (
    <div>
      <Space style={{ marginBottom: 16 }}>
        <Button type="primary" onClick={openCreate}>新增规则</Button>
      </Space>
      <Table rowKey="id" loading={loading} columns={columns} dataSource={data}
        pagination={{ current: page, pageSize: 10, total, onChange: setPage }} />
      <Modal title={editing ? '编辑规则' : '新增规则'} open={open} onOk={onSubmit}
        onCancel={() => setOpen(false)} destroyOnClose>
        <Form form={form} layout="vertical">
          <Form.Item name="name" label="规则名称" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="ruleType" label="规则类型" rules={[{ required: true }]} initialValue="KEYWORD">
            <Select options={[
              { value: 'KEYWORD', label: '关键词' },
              { value: 'REGEX', label: '正则表达式' },
              { value: 'FILE_TYPE', label: '文件类型' },
              { value: 'SAMPLE', label: '样例' },
            ]} />
          </Form.Item>
          <Form.Item name="pattern" label="匹配模式" rules={[{ required: true }]}>
            <Input.TextArea rows={3} placeholder="关键词/正则/扩展名列表/样例内容" />
          </Form.Item>
          <Form.Item name="severity" label="级别" initialValue="MEDIUM">
            <Select options={['CRITICAL','HIGH','MEDIUM','LOW','INFO'].map(v => ({ value: v, label: v }))} />
          </Form.Item>
          <Form.Item name="scopeBind" label="绑定站点（可选）">
            <Input placeholder="不填则对所有资产生效" />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}