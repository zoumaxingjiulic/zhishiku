# 企业智能体平台前端

此 MVP 使用轻量静态页面，包含登录、工作台、知识库与文档处理、切片状态、人资智能体问答、用户与部门、系统连接占位及审计日志。Nginx 只将 `/api/` 代理到 API 容器；浏览器不会直接访问 MinIO、MySQL、Redis、Milvus 或 OpenSearch。

认证使用 HttpOnly Cookie，权限判断全部由 API 重新计算，浏览器提交的部门信息不能扩大访问范围。后续可保持相同 API 边界替换为 Vue/React，并接入公司 SSO。
