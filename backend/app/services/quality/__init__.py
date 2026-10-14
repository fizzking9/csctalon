"""数据质量审核域服务包(#322 P2 逐域切换)。

原 `app/services/analytics/refund.py` 内的「手动退款登记质量审核」含**写路径**
(直接 UPDATE `ods.manual_refunds` 基表), analytics 层已切到 public 干净视图读路径,
门禁 C2 禁止 analytics 再引用 ods 基表/基类, 故将写路径移出到本包。
"""
