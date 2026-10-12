import { Button, Form, Input, message } from 'antd'
import { useNavigate } from 'react-router-dom'
import { login } from '../api/auth'

export default function Login() {
  const nav = useNavigate()

  const onFinish = async (v: { username: string; password: string }) => {
    try {
      const res: any = await login(v)
      localStorage.setItem('token', res.token)
      message.success('登录成功')
      nav('/')
    } catch (e: any) {
      message.error(e.message || '登录失败')
    }
  }

  return (
    <div style={{ maxWidth: 360, margin: '120px auto' }}>
      <h2>网站安全智能巡检管理端</h2>
      <Form onFinish={onFinish}>
        <Form.Item name="username" rules={[{ required: true, message: '请输入用户名' }]}>
          <Input placeholder="用户名" />
        </Form.Item>
        <Form.Item name="password" rules={[{ required: true, message: '请输入密码' }]}>
          <Input.Password placeholder="密码" />
        </Form.Item>
        <Button type="primary" htmlType="submit" block>
          登录
        </Button>
      </Form>
    </div>
  )
}