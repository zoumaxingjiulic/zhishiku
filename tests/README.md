# 回归测试

本目录覆盖解析、权限隔离、企业总助手、专业智能体、MCP、迁移/Compose、进程健康和仓库安全契约。默认测试使用临时文件、内存数据库或替身，不连接服务器业务库、不调用真实模型/MCP，也不执行 Docker build/up。

## 本地完整门禁

从仓库根目录运行 Python；前端命令在 `services/frontend` 运行：

```bash
python -m pip install -r requirements-test.txt
python -m pip install ./shared/python
python -m pytest -q

npm --prefix services/frontend ci
npm --prefix services/frontend test
npm --prefix services/frontend run typecheck
npm --prefix services/frontend run lint
npm --prefix services/frontend run build

python tools/check_repository_hygiene.py
git diff --check
```

`test_quality_platform.py` 固定 API `1.2.0`、总助手 router、chat-runner 分派、低代码画布依赖冻结和实际仓库凭据扫描。`test_process_health.py` 验证前端稳定 `/healthz`，以及 Worker/chat-runner 的 MySQL 只读探针与脱敏失败输出。`test_compose_contract.py` 展开真实双 Compose 配置，验证依赖合并、前端健康端点、MinIO 初始化与 013 注册。

`tests/api/test_assistant_*` 覆盖权限过滤、严格正整数能力 ID、意图保护、会话所有权、专业智能体候选、只读工具、Skill、持久化任务、来源追踪和 worker 恢复。`test_parsing.py` 覆盖跨页续段/续表、页眉页脚、新章隔离、真实 PDF 提取、混合 PDF 逐页 OCR 路由、DOCX 顺序、XLSX 表头/工作表/行号、长文本有界切分及入库页码/元数据。

仓库安全扫描只输出命中的文件路径和规则名，不输出匹配正文。内置规则覆盖被跟踪的 `.env`、私钥文件/PEM、`sk-`、静态 Authorization Bearer 和 MCP Token。若需核对已知内部凭据片段，在本地把片段通过 `REPOSITORY_SECRET_FRAGMENTS`（Windows 用分号、Unix 用冒号分隔）传入扫描器；不要把真实片段写进测试、fixture、README 或命令日志。

OCR 路由测试模拟识别结果，不等于任意真实扫描件的识别质量验收。服务器业务验收脚本见 `deploy/verify-platform-v11.py`，企业总助手 1.2 的备份、013、双 Compose 健康检查和真实账号验收步骤见根目录 README；这些操作会修改外部环境，必须单独授权，本地门禁不会执行。
