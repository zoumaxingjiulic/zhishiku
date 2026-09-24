# 后端架构说明

## 架构形态

当前后端是模块化单体，不是微服务。一个 FastAPI API 进程提供同步 HTTP 接口；独立的 `chat-runner` 处理对话、工作流和评测任务；独立 Worker 处理文档解析、OCR、切片和索引。任务状态持久化在 MySQL，Redis 目前不是任务队列。

~~~text
main.py
  └─ application.create_app()          仅负责装配
       ├─ core/                        配置、事务、安全、审计、脱敏、出站策略
       ├─ domains/<domain>/router.py   HTTP 入站与 DTO
       │                  /service.py  权限、事务内业务规则
       │                  /repository.py SQL 与持久化
       │                  /schemas.py  请求/响应和领域配置结构
       ├─ infrastructure/              MinIO 等基础设施适配器
       └─ runtime/                     聊天、检索能力、MCP、工作流、评测后台运行时
~~~

`main.py` 只暴露 `app = create_app()`。应用工厂统一注册生命周期、异常处理器、系统健康端点和所有领域 Router；测试可传入依赖覆盖和无外部连接的启动函数。

## 业务域

| 目录 | 职责 |
| --- | --- |
| `domains/auth` | 登录、会话令牌、当前账号加载 |
| `domains/users` | 部门、账号、平台管理员约束、账号直授知识库与只读工具 |
| `domains/knowledge` | 知识库、文件夹、部门继承与账号直授 ACL |
| `domains/documents` | 上传、版本、入库任务、删除和重建 |
| `domains/agents` | 智能体配置、授权、会话和聊天任务 |
| `domains/prompts` | 个人提示词模板隔离 |
| `domains/requests` | 智能体需求申请与处理 |
| `domains/model_gateway` | 模型供应商配置和智能体模型绑定 |
| `domains/connectors` | 企业系统连接、工具发现与授权 |
| `domains/studio` | 工作流、评测数据集、用例和运行 |
| `domains/observability` | 审计、追踪、反馈与 Dashboard |

根目录的 `agent_runtime.py`、`retrieval.py`、`quality.py` 和 `readiness.py` 是跨域计算/探测模块；它们不装配 HTTP 路由，也不能反向导入 `app.main`。

## 依赖方向

允许的主要依赖方向为：

~~~text
application → routers → services → repositories → database driver
                         ↓
                    core / runtime / infrastructure
~~~

- Repository 不依赖 FastAPI；Router 不直接依赖 MySQL 驱动。
- Domain 和 Runtime 不导入 `app.main`，不得把应用入口当服务定位器。
- Runtime 通过显式参数或依赖对象获得模型、检索、MCP 和持久化能力；`runtime/retrieval.py` 是聊天、Studio 和工作流共用的公开检索端口，不允许 Runtime 反向导入 Domain 私有函数。
- `core` 不依赖具体业务 Router；跨域写操作由 Service 协调，不在 Router 拼接 SQL。
- 层级约束由 `tests/api/test_layer_boundaries.py` 的 AST 检查保护。

## 事务和并发规则

`core.database.UnitOfWork` 拥有一个 MySQL 连接和游标。进入上下文不会自动提交；业务成功路径必须显式 `commit()`，异常或未完成路径回滚并关闭资源。Service 定义事务边界，Repository 不自行提交。

写操作遵循以下顺序：

1. 在同一个 UnitOfWork 中重新加载当前账号与部门关系；不能只信任请求开始时缓存的管理员标记。
2. 先锁稳定的权限/范围行，再锁业务实体，随后锁关联行和任务行。
3. 完成权限校验、状态校验和写入后一次提交；最终授权检查和结果落库不可拆成两个事务。
4. 多行锁按稳定主键顺序获取；抢占队列使用 `FOR UPDATE SKIP LOCKED`。
5. MinIO、Milvus、OpenSearch 等外部副作用不假装与 MySQL 原子提交；使用固定任务键、状态机、重试和补偿记录实现幂等恢复。

典型锁顺序：

- 管理员写操作：`PLATFORM_ADMIN` 部门互斥行 → actor/target 用户 → 用户部门关系 → 当前启用管理员集合。
- 知识库/文件夹写操作：知识库与 ACL → 父目录/目标目录 → 子目录或文档集合。
- 总助手最终落库：账号/部门与账号直授能力 → 知识库/文档 ACL → 已使用工具授权 → 会话/任务 → 消息与运行结果。
- 专业智能体最终落库：账号与用户智能体分发 → 智能体启用状态及绑定 → 活跃知识库/文档和只读工具 → 会话/任务 → 消息与运行结果。

对话生成期间只持久化任务阶段和进度状态，不保存或返回模型生成正文，`partial_answer` 始终为空。总助手不会借用专业智能体能力；专业智能体的知识库和工具委托只在该智能体任务内生效。只有最终事务重新核对对应永久权限或临时委托、会话和任务状态后，完整回答、引用、运行结果才会一起发布；失败、撤权、归档、取消和进程重启都不写 assistant 消息。

## 后台任务与扩容限制

`chat-runner` 启动时会把遗留的 `running` 对话、工作流和评测任务标记为因重启失败；文档 Worker 启动时会重新排队遗留的入库任务。因此当前部署约束是 **一个 chat-runner 实例和一个文档 Worker 实例**。现在不能直接横向增加副本，否则一个新实例的启动恢复可能干扰另一个仍在执行的任务。

需要多实例前，必须先增加 `owner_id`、租约到期时间和心跳，并让恢复逻辑只接管已过期租约；随后再做并发、进程崩溃和重复投递测试。此限制不是 Kubernetes 或消息中间件本身能够自动解决的。

## 出站网络和凭据

模型与 MCP 请求默认拒绝任意目标。允许目标必须同时满足部署配置中的精确主机白名单；访问私网地址时还必须命中 CIDR 白名单。连接阶段重新解析 DNS 并固定到通过校验的 IP，拒绝 loopback、link-local、multicast、unspecified、reserved、云 metadata 地址和重定向。

- 模型：`MODEL_ALLOWED_HOSTS`、`MODEL_ALLOWED_CIDRS`
- MCP：`MCP_ALLOWED_HOSTS`、`MCP_ALLOWED_CIDRS`
- 供应商 API Key 和 MCP Token 以 Fernet 密文持久化，解密明文只在短生命周期调用中存在。
- 日志、异常、审计和 API 响应必须经过脱敏；不得记录提示词全文、Token 或业务系统返回正文。后台失败日志只记录任务/追踪标识和异常类型，数据库错误字段只保存稳定错误码。
- 应用层校验还需配合容器网络、宿主机防火墙或云 NSG 出站 ACL。

## 新增业务域

1. 在 `app/domains/<name>/` 创建 `schemas.py`、`repository.py`、`service.py`、`router.py` 和 `__init__.py`。
2. 先写 Service/Router 行为测试以及未授权、并发状态变化和敏感信息错误路径测试。
3. Repository 只接收游标并执行 SQL；Service 接收 UnitOfWork，完成权限检查并显式提交。
4. 将 Router 加入 `application.ROUTERS`，运行 94 条 `/api/v1` 路由契约测试；新增或有意变更 API 时同步评审并更新契约 fixture。
5. 若需要长任务，把执行器放入 `runtime/`，通过显式依赖调用领域能力，不导入 `app.main`。
6. 若需要外部存储或协议客户端，把适配器放入 `infrastructure/` 或 `runtime/`，并复用出站白名单、超时、脱敏和幂等约束。
7. 运行后端全量测试、前端 lint/typecheck/test/build、双 Compose 配置展开和仓库敏感文件检查。

数据库变更只能新增下一序号迁移；已执行的 `database/mysql/001` 至 `014` 不得修改、合并或删除。空数据卷由 MySQL 官方镜像按文件名顺序执行目录中的全部 `*.sql`（当前为 001 至 014）；已有数据卷必须逐个、且仅一次执行新增迁移。代码回滚不能依赖破坏性数据库回滚。014 之后旧 `agent_department_acl` 不再同步，不能作为用户级权限的回滚来源。

## 验证边界

自动化测试和 Compose `config` 能证明代码与配置静态一致，但不能证明镜像可构建、容器依赖真实可达或生产数据兼容。容器运行验收必须在 Docker 守护进程可用时，以隔离的数据目录执行 build/up、`/healthz`、`/readyz` 和依赖故障测试；不得用生产数据卷做试验。
