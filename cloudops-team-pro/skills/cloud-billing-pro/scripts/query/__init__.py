"""提示词扩展查询子模块。

模块布局：
  - billing_data_loader.py: 多月数据加载与对齐
  - slot_dicts.py: 从历史账单自动发现槽位字典
  - router.py: 自然语言 → 触发器 ID + 槽位 提取
  - handlers.py: 16 个 P0 handler 的查询逻辑
  - renderers.py: 三段式输出渲染（一句话结论 + 数据明细表 + 建议/洞察）

入口脚本：scripts/query_billing.py
"""
