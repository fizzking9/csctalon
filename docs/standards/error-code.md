# 错误码与响应格式规范

**生效日期**：2026-09-01
**适用范围**：后端 `backend/app/api/v1/**` 所有接口
**关联文档**：[API接口文档.md](../API接口文档.md)、AGENTS.md（全局约束）

> 本规范基于现有代码实测对齐（`data_sources.py`、`main.py` 全局异常处理器），确保 AI 生成的接口与项目现状一致，避免"派工单"与实现脱节。

---

## 一、成功响应（统一格式）

所有业务成功响应统一返回：

```json
{
  "code": 0,
  "message": "ok",
  "data": { }
}
```

约定：

| 字段 | 类型 | 说明 |
|---|---|---|
| `code` | int | 恒为 `0` 表示成功 |
| `message` | string | 成功时通常为 `"ok"` |
| `data` | object/array | 业务数据；分页时内含 `{items, total, page, page_size}` |

分页结构示例（对齐 `data_sources.py`）：

```json
{
  "code": 0,
  "message": "ok",
  "data": {
    "items": [],
    "total": 100,
    "page": 1,
    "page_size": 20
  }
}
```

## 二、错误响应（FastAPI HTTPException 格式）

**错误响应与成功响应不同**：错误走 `HTTPException(status_code=..., detail=...)`，由 FastAPI 返回 `{detail, code}` 结构（`main.py` 全局 500 处理器也保持该格式，只额外带 `message`）。

```json
{
  "detail": "错误描述",
  "code": "ERROR_CODE"
}
```

> ⚠️ **不要**在错误分支里套用 `{code:0, data, message}` 成功外壳——错误必须用 HTTP 状态码表达。

## 三、HTTP 状态码约定

| 状态码 | 含义 | 使用场景 |
|---|---|---|
| 200 | 成功 | 查询 / 更新成功 |
| 201 | 创建 | 新增资源 |
| 400 | 参数错误 | 参数缺失 / 格式错误 / 非法枚举 |
| 401 | 未认证 | 未携带 / 无效 Token |
| 403 | 无权限 | 已认证但无权访问资源 |
| 404 | 不存在 | 资源不存在（数据源、字段、记录） |
| 500 | 服务器错误 | 未处理异常（全局处理器兜底，不暴露内部堆栈） |

## 四、业务错误码（`detail` 内 `code` 字段）

错误响应中的 `code` 字段当前为字符串标识，用于前端识别错误类型。约定命名：

- `INVALID_PARAMS` — 参数校验失败
- `UNAUTHORIZED` — 未认证
- `FORBIDDEN` — 无权限
- `NOT_FOUND` — 资源不存在
- `CONFLICT` — 数据冲突（重复、状态不允许变更）
- `SYNC_BUSY` — 同步任务进行中（互斥锁占用）
- `INTERNAL_ERROR` — 服务器内部错误

## 五、新增接口自检清单

1. [ ] 成功路径是否返回 `{code:0, message:"ok", data}`？
2. [ ] 分页是否包含 `items/total/page/page_size` 四字段？
3. [ ] 错误路径是否走 `HTTPException`，而非拼装成功外壳？
4. [ ] 金额字段是否为整数（分），未用 float？
5. [ ] 时间字段是否 `AT TIME ZONE 'Asia/Shanghai'` 转换后返回？
