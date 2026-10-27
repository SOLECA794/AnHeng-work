# SDD-12 · D 模块认证桥与可选路由装配

> v1.0，2026-10-27，D；随统一候选 PR 评审。依据 SDD-03 Bearer JWT/角色约定及 SDD-06/09/10/11 的可信 Principal 要求。无数据库结构或既有 API 字段变更。

## 1. 解决的缺口

现有 B 的 `current_user` 已能验证真实 JWT，但 D 的四组路由仅接收外部注入的 Principal。新增桥接器复用该身份验证结果，再从**服务器配置的授权解析器**读取资产 ID，构造 D 的 Principal。调用方不能通过请求头、请求体或 Token 自带 scope 字段授予资产权限。

## 2. 授权解析契约

`create_d_principal_dependency(grant_resolver=None)` 返回 FastAPI 依赖。解析器为同步函数 `grant_resolver(user_id: int) -> frozenset[int]`：

- 复用 `current_user` 核验签名、有效期、退出吊销及用户当前密码/角色/停用状态，不自行再次解析 Token。
- User ID 必须为正整数且小于 2^63，启用标记必须为 True；仅 SEC_OPS/RULE_ADMIN 可进入 D 业务接口，ADMIN 无隐式业务权限。
- 解析器每个请求调用一次，不跨请求缓存。缺少解析器等同空授权集合；列表可为空，指定资产访问由服务拒绝为 40310。
- 返回值严格为 frozenset，成员为合法正整数，不接受 bool。None、列表/可变集合、非法 ID 或解析器异常均统一 50000，原始异常文本不出响应。异步解析器不在本版契约内，配置时拒绝。
- 无效身份为 40100，角色拒绝为 40300。依赖抛安全 HTTPException，经既有 VerificationRoute 输出统一信封；避免 Depends 中的服务异常绕过接口内的错误处理。

真实用户到资产的 ACL 表、查询规则及维护接口由 B/C 提供；本版不新增临时授权表，也不自动将全部资产授给登录用户。服务器解析器是受信任代码，不能从 Request 或未经校验的用户输入返回权限集合。

## 3. 可选装配

`DModuleServices(verification, alerts, retesting, reports)` 与 `create_d_module_router(services, grant_resolver=None, ...)` 将确认/验证查询、告警、复测和报告四组已有路由装配到一个 APIRouter，统一使用上述身份桥。可注入既有测试时钟，业务权限/状态/事务仍由各服务负责。

默认 `src.main` 不自动挂载。B 在实际授权解析器、数据库会话和服务实例就绪后主动 include_router；本契约不增加新的业务端点、不改变既有默认拒绝工厂。

## 4. 验收与边界

使用真实登录签发的 JWT、私有 SQLite 用户存储与业务记录验证四组路由，覆盖撤销/停用/改密、角色、跨资产、伪造请求头、解析器类型/异常及动态撤权。每请求读授权意味着撤权对下一次请求生效；不承诺中断已在执行的长耗时请求。生产长任务还需结合授权版本或完成前重新核验等机制。

通过上述测试可称“真实 JWT 到 D 接口的可注入桥接已实现”，不能称“生产 ACL 已实现”或“默认应用、前端、PostgreSQL、恒脑及在线采集均已联调”。
