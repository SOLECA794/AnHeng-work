import { Tabs } from 'antd'
import SensitiveRules from './SensitiveRules'
import { useEffect, useState } from 'react'
import { Table, Button, Space, Tag, Modal, Form, Input, Select, message, Popconfirm } from 'antd'
import { listAssets, createAsset, updateAsset, disableAsset, enableAsset } from '../api/assets'
import type { Asset } from '../types/asset'

export default function Assets() {
  const [data, setData] = useState<Asset[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [status, setStatus] = useState<string | undefined>()
  const [loading, setLoading] = useState(false)
  const [open, setOpen] = useState(false)
  const [editing, setEditing] = useState<Asset | null>(null)
  const [form] = Form.useForm()

  const load = async () => {
    setLoading(true)
    try {
      const res = await listAssets({ page, size: 10, status: status as any })
      setData(res.list); setTotal(res.total)
    } finally { setLoading(false) }
  }

  useEffect(() => { load() }, [page, status])

  const openCreate = () => { setEditing(null); form.resetFields(); setOpen(true) }
  const openEdit = (r: Asset) => { setEditing(r); form.setFieldsValue(r); setOpen(true) }

  const onSubmit = async () => {
    const v = await form.validateFields()
    if (editing) await updateAsset(editing.id, v)
    else await createAsset(v)
    message.success(editing ? '修改成功' : '新增成功')
    setOpen(false); load()
  }

  const onDisable = async (id: number) => {
    await disableAsset(id); message.success('已停用'); load()
  }

  const onEnable = async (id: number) => {
    await enableAsset(id); message.success('已启用'); load()
  }
  const columns = [
    { title: '域名', dataIndex: 'domain' },
    { title: '范围', dataIndex: 'scopeType', render: (v: string, r: Asset) =>
        <span>{v}{r.scopeValue ? ` (${r.scopeValue})` : ''}</span> },
    { title: '归属', dataIndex: 'owner' },
    { title: '页面数', dataIndex: 'pageCount' },
    { title: '状态', dataIndex: 'status', render: (v: string) =>
        v === 'ACTIVE' ? <Tag color="green">启用</Tag> : <Tag>停用</Tag> },
    { title: '录入时间', dataIndex: 'createdAt' },
    { title: '操作', render: (_: any, r: Asset) => (
    <Space>
        <a onClick={() => openEdit(r)}>编辑</a>
        {r.status === 'ACTIVE' ? (
        <Popconfirm title="确认停用该资产？" onConfirm={() => onDisable(r.id)}>
            <a style={{ color: 'red' }}>停用</a>
        </Popconfirm>
        ) : (
        <Popconfirm title="确认启用该资产？" onConfirm={() => onEnable(r.id)}>
            <a style={{ color: 'green' }}>启用</a>
        </Popconfirm>
        )}
    </Space>
    )},
  ]

  return (
    <Tabs
      defaultActiveKey="assets"
      items={[
        {
          key: 'assets',
          label: '授权资产',
          children: (
            <div>
              <Space style={{ marginBottom: 16 }}>
                <Select
                  placeholder="状态筛选" allowClear style={{ width: 140 }}
                  value={status} onChange={setStatus}
                  options={[{ value: 'ACTIVE', label: '启用' }, { value: 'DISABLED', label: '停用' }]}
                />
                <Button type="primary" onClick={openCreate}>新增资产</Button>
              </Space>

              <Table
                rowKey="id" loading={loading} columns={columns} dataSource={data}
                pagination={{ current: page, pageSize: 10, total, onChange: setPage }}
              />

              <Modal
                title={editing ? '编辑资产' : '新增资产'}
                open={open} onOk={onSubmit} onCancel={() => setOpen(false)}
                destroyOnClose
              >
                <Form form={form} layout="vertical">
                  <Form.Item name="domain" label="域名" rules={[{ required: true, message: '请输入域名' }]}>
                    <Input placeholder="例如 demo.example.com" />
                  </Form.Item>
                  <Form.Item name="scopeType" label="范围类型" rules={[{ required: true }]} initialValue="FULL">
                    <Select options={[
                      { value: 'FULL', label: '全站' },
                      { value: 'PATH', label: '指定路径' },
                      { value: 'SUBDOMAIN', label: '子域范围' },
                    ]} />
                  </Form.Item>
                  <Form.Item name="scopeValue" label="范围值">
                    <Input placeholder="PATH 时填 /app；SUBDOMAIN 时填子域" />
                  </Form.Item>
                  <Form.Item name="owner" label="归属部门">
                    <Input />
                  </Form.Item>
                  <Form.Item name="authNote" label="授权备注">
                    <Input.TextArea rows={2} />
                  </Form.Item>
                </Form>
              </Modal>
            </div>
          ),
        },
        { key: 'rules', label: '敏感信息规则', children: <SensitiveRules /> },
      ]}
    />
  )
}