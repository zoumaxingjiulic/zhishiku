# 企业智能体平台前端

前端使用 Vue 3 + TypeScript + Vite + Vue Router。生产容器使用 Node 多阶段构建，并由 Nginx 提供静态资源和 `/api/` 反向代理；Nginx 的 history fallback 支持业务 URL 直接访问和刷新。

当前页面包含：

- 登录、退出和修改密码；
- 工作台与处理链路概览；
- 知识库、文件夹、批量上传、下载、移动、切片查看、重建索引、删除和归档；
- 智能体列表、按智能体授权知识范围问答、会话管理和引用展示；
- 平台管理员的账号、部门和审计管理；
- ERP、PLM、MOM 系统连接占位。

认证使用 HttpOnly Cookie，页面只负责展示和操作入口。知识库、文档和智能体的部门权限由 API 在每次请求时强制校验，浏览器提交的部门字段不能扩大权限。

主要路由：

- `/workbench`、`/knowledge-bases`、`/prompt-templates`、`/agent-requests`、`/connections`；
- `/agents` 为智能体列表，`/agents/:agentId` 为智能体入口，`/agents/:agentId/chat/:sessionId` 对应一个持久化对话；
- `/model-gateway`、`/users`、`/audit` 由前端路由守卫和后端接口共同执行管理员权限控制。

本地开发：

```bash
npm ci
npm run dev
```

生产构建验证：

```bash
npm run build
```
