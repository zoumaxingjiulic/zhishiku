# 平台 1.1：配置、执行与质量闭环

## 本轮范围

保留 Vue 3 / FastAPI / MySQL / MinIO / Milvus / OpenSearch / Infinity，不替换为整套低代码产品。

| 能力 | 实现与入口 |
|---|---|
| 智能体管理 | 管理员“智能体工作室”：新增/编辑/停用，部门、知识库、只读工具、模型绑定；乐观锁防止覆盖并发修改 |
| 配置版本 | 工作室发布配置保存快照；历史版本载入编辑区，确认发布为新版本；不覆盖审计历史 |
| 检索配置 | 混合/向量/全文、RRF、候选数、Top K、rerank 开关/阈值、父上下文、历史长度及上下文预算 |
| 检索调试 | 使用已发布配置，显示命中文档/切片、耗时、召回数量、rerank 方式及降级警告；管理员测试不等于员工权限测试 |
| 评测 | 人工标注相关文档或无证据问题；后台跑 1–100 条用例；记录参数快照、HitRate、Recall、MRR、nDCG、无证据通过率和平均耗时 |
| 对话任务 | 用户消息与任务同事务持久化；后台 `chat-runner` 最多 4 个任务；模型流式输出逐步保存，页面每秒取状态与部分回答，切换页面后恢复 |
| 工具调用 | 有限多轮调用、JSON Schema 参数验证、每次执行重新检查用户和工具授权，最大轮数和调用次数有上限 |
| 工作流 | 顺序节点：检索、只读 MCP 工具、模型生成、人工确认；步骤输出持久化，等待确认后继续；可停止 |
| 用户反馈 | 回答“有帮助/需改进”，服务器按回答所有者校验；管理员反馈接口 `/api/v1/studio/feedback` |
| 文档策略 | 知识库“文档处理策略”：通用或父子分段、字符长度、重叠；只作用于新上传或手动重建 |
| 文件扩展 | PPTX 原生文字/表格；DXF TEXT/MTEXT/属性文本，保留图层/布局/实体句柄；原有 PDF、DOCX、XLSX、文本和图片 OCR 保留 |

### 权限与边界

- 在工作室发布的智能体使用显式部门授权；管理员始终可管理。旧智能体未在工作室保存前保持原兼容规则。
- 智能体知识授权与用户知识权限取交集；非管理员在向量/全文召回前限定有权访问的文档，再次校验返回切片。
- `readOnlyHint` 只是工具声明，不是安全沙箱。只给可信 MCP 服务配置账号，服务账号应实际只读并限制组织/数据范围。平台不会把用户部门自动映射成 ERP 数据权限。
- ERP/OA 查询结果可能发送到绑定的外部大模型；启用前需确认公司数据出域规则。
- 工作流不是任意代码执行器；本版无循环、条件分支、并行节点、系统写入和事务补偿。审批是继续执行的人工确认，不代表已实现业务系统审批流。
- 工具参数用 JSON，引用 `$input.question` 或 `$steps.<已完成步骤标识>.<字段>`。不使用 eval，也不允许任意 HTTP 节点。
- 停止是协作式取消：已在执行的外部调用可能先完成；不会撤销调用产生的外部效果。本版仅允许只读工具。
- 单个 `chat-runner` 服务，4 个执行线程；不要直接扩容多个副本。重启时运行中的任务标为失败，不自动重放工具调用；排队和等待确认任务保留。需要高可用时再实现租约/心跳及幂等工具契约。
- 前端采用每秒轮询持久化部分回答，不是浏览器 SSE；后台调用模型使用流式协议。
- 评测衡量**文档召回**，不冒充回答正确率、忠实度或业务完成率。没有足够人工标注的样本不能据此承诺准确率。
- 追问改写是保守规则补充上下文，不是完整的语义查询规划器。
- DXF 仅文本语义检索，DWG 需先转换成 DXF/PDF；不支持完整 CAD 几何理解。PPTX 中嵌入图片不单独 OCR。
- 发布检索策略不会自动重建文档；切片策略改变后须按需手动重建。旧切片没有父块时仍能正常检索。

## 数据库变化

`012_platform_quality_runtime.sql`：新增配置版本、后台对话任务、工作流运行、评测用例/结果、回答反馈表；agent 新增 config_version；knowledge_base 新增 processing_config_json；content_unit 新增可空 parent_text。

不删除原文档，不更换向量模型/集合，不自动重建索引。新增子块时向量索引仍以 content_unit_id 为主键，父文本保存在 MySQL。

## 升级

先完成数据库备份，再应用 012（仅一次），重建 `api frontend worker chat-runner`。启动整个系统继续带模型配置：

```bash
docker compose --env-file .env -f deploy/docker-compose.yml -f deploy/docker-compose.models.yml up -d --build api frontend worker chat-runner
```

数据库迁移需要保留备份；MySQL DDL 不具备整文件事务回滚。模型、数据库容器无需重启。回退应用前不要删除新表或 parent_text，旧代码能忽略新增字段。

## 验收

1. 管理员进入工作室，保存临时智能体，修改版本后测试旧版本号提交返回 409；普通账号直接请求工作室返回 403。
2. 分别验证部门允许/拒绝、检索结果隔离、工具绑定撤销；不把管理员检索测试当作隔离测试。
3. 提问后切换菜单、刷新、重新登录：对话和任务状态仍在；不同对话任务不串扰；停止后最终状态可见。
4. 配置“检索→确认→模型生成”，启动后等待确认，确认后继续；取消后不执行后续步骤。
5. 标注已知文档，运行评测集，查看逐题结果和汇总；故障降级显示警告。
6. 用新建临时知识库上传生成的 DOCX/PPTX/DXF/PDF/图片，检查切片来源及父块；不重建用户已有生产资料。

## 参考与取舍

- [Dify Knowledge Pipeline](https://dify.ai/blog/introducing-knowledge-pipeline)：参考处理策略、检索配置、调试的分层设计，并非复制全部管道能力。
- [Dify 父子检索](https://dify.ai/blog/introducing-parent-child-retrieval-for-enhanced-knowledge)：参考小块检索、大块提供上下文。
- [LangGraph Persistence](https://docs.langchain.com/oss/python/langgraph/persistence)：参考运行状态与检查点持久化的原则；本项目本轮未引入 LangGraph。
- [Ragas Context Recall](https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/context_recall/)：本轮先做可核验的人工标注文档级指标，不额外购买模型评审调用。
- [MCP Tools](https://modelcontextprotocol.io/specification/2025-06-18/server/tools)：参考工具 schema 与权限边界；不将 annotations 当成服务端强制授权。
