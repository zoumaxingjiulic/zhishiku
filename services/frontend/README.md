# 企业智能体平台前端

此 MVP 使用一个轻量静态页面，包含人资问答、智能体选择、知识库文档列表与上传入口。Nginx 只将 `/api/` 代理到 API 容器；浏览器不会直接访问 MinIO、MySQL、Redis、Milvus 或 OpenSearch。

后续替换为正式前端项目时，保持相同的 API 边界，并接入 SSO、角色与审计界面。
