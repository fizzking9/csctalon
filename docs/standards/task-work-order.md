# 任务派工单模板（Prompts）

**生效日期**：2026-09-01
**适用范围**：所有交给 AI 的单项开发任务（对应"AI 协作治理规范" 4.4）
**关联文档**：AGENTS.md（全局约束）、[error-code.md](error-code.md)、[review-checklist.md](review-checklist.md)

> 目的：把模糊需求（"帮我写个接口"）转成结构化派工单，减少 AI 猜测，提高一次通过率。
> 用法：复制下方模板，按任务填写 `## 任务` 之后的每个部分，再交给 Developer 阶段执行。

---

## 模板

```markdown
## 任务
<一句话说明要实现什么功能>

## 输入
- <参数名>: <类型>, 必填/可选, 说明
  （示例：order_id: string, 必填, 长度 32）
- <参数名>: enum[...], 可选

## 输出
- 列表字段：<字段1>, <字段2>, ...（含单位，金额必须是"分"）
- 分页字段：total, page, page_size, total_pages
- 统一包装：{code: 0, data: {...}, message: "ok"}   （错误走 HTTPException，见 error-code.md）

## 约束
1. 使用 <框架>，路径 <API 路径>
2. 数据库表 <表名>，字段见 <文档>
3. 金额字段必须以"分"为单位存储，接口返回也要是整数（分）
4. 必须支持按 <字段> 倒序
5. 必须检查 <权限>（通过 user_id / 角色关联）
6. 时间字段必须 AT TIME ZONE 'Asia/Shanghai' 转换

## 参考
- 现有类似接口：<docs/api/xxx.md>
- 错误码规范：docs/standards/error-code.md
- 数据时间规范：docs/数据时间字段使用规范.md

## 禁止事项
1. 禁止直接拼接 SQL（须 text() + 绑定参数）
2. 禁止硬编码配置 / 密钥 / 内网 IP
3. 禁止绕过 Alembic 改表结构
4. 禁止在生产代码使用 console.log / print

## 测试要求
- 至少 1 个正向测试 + 2 个边界测试
- 测试文件命名：test_模块名_功能点.py（放 backend/tests/）
```

---

## 填写示例（退款查询接口）

```markdown
## 任务
实现退款查询接口

## 输入
- order_id: string, 必填, 长度 32
- refund_status: enum['pending', 'approved', 'rejected'], 可选
- page: int, 默认 1, 范围 1-1000
- page_size: int, 默认 20, 范围 10-100

## 输出
- 列表字段：refund_id, order_id, amount(分), status, created_at, updated_at
- 分页字段：total, page, page_size, total_pages
- 统一包装：{code: 0, data: {...}, message: "ok"}

## 约束
1. 使用 FastAPI，路径 /api/v1/refunds
2. 数据库表名 t_refund，已存在，字段见 docs/db/t_refund.md
3. 金额字段必须以"分"为单位存储，接口返回也要是整数（分）
4. 必须支持按 updated_at 倒序
5. 必须检查用户是否有该订单的查询权限（通过 user_id 关联）
6. 时间字段 AT TIME ZONE 'Asia/Shanghai' 转北京时间返回

## 参考
- 现有类似接口：docs/api/order-query.md
- 错误码规范：docs/standards/error-code.md

## 禁止事项
1. 禁止直接拼接 SQL
2. 禁止硬编码表名以外的任何配置

## 测试要求
- 至少 1 个正向 + 2 个边界测试，命名 test_refund_query.py
```

---

## 差 vs 好对比

| 维度 | 差 Prompt | 好 Prompt |
|---|---|---|
| 需求 | "帮我写个退款查询接口" | 明确路径、参数、输出、约束 |
| 输入 | 无 | 参数名/类型/必填/范围全列 |
| 输出 | 无 | 字段、单位、分页、包装格式全列 |
| 约束 | 无 | 表名、金额单位、排序、权限、时区 |
| 参考 | 无 | 指向现有文档，避免重复造轮子 |
| 禁止 | 无 | 明确禁止拼接 SQL、硬编码 |
