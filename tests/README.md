# 回归测试

`test_parsing.py`：跨页续段/续表、页眉页脚、新章隔离、真实 PDF 提取、混合 PDF 逐页 OCR 路由、DOCX 顺序、XLSX 表头/工作表/行号、长文本有界切分及入库页码/元数据。

```bash
python -m pip install -r services/worker/requirements.txt pytest==8.3.5
python -m pytest tests/test_parsing.py -q
```

测试临时生成文件、模拟数据库，不接触服务器业务数据、不调用模型。OCR 路由测试模拟识别结果，不等于对任意真实扫描件的识别质量验收。

现有服务器业务验收脚本见 `deploy/e2e-admin.py`；运行这类脚本会创建测试账号/资料，应单独确认环境。
