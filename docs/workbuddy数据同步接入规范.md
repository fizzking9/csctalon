# workbuddy 投诉数据同步接入规范

> 版本: v1.0 · 2026-08-20
> 适用: workbuddy 定时将 `315投诉处理登记表.xlsx` 等文件内容推送到客服中心管理系统

---

## 1. 背景与目的

系统重新启用了两张投诉补充表(此前曾停用):

| 数据表 | 说明 | 对应 Sheet |
|---|---|---|
| `external_complaints` | 315/监督局等**外部投诉**明细登记 | `监督局-315投诉` |
| `card_complaints` | **卡商群投诉**明细登记 | `卡商群投诉` |

- 投诉的**主台账**是 `service_tickets` 服务工单(外部投诉类工单,如工信部/12315/消协等平台投诉)。
- 这两张补充表是对具体投诉**更深入、更详细的描述补充**(投诉问题/解决方案/结果/跟进人/处理明细等),与工单按**设备SN**关联合并展示。
- 工单的外部投诉更新时间比这两张表晚,因此保留两张补充表,提供投诉统计与分析的完整细节。

**同步方式**: workbuddy 每 20 分钟检查文件是否变化,变化时将该文件两个 Sheet 的明细以 **JSON** 通过 HTTP 推送过来。

---

## 2. 接入总览

| 项目 | 值 |
|---|---|
| 推送接口 | `POST {BASE_URL}/api/v1/sync/complaints/push` |
| 日志查询(内部) | `GET {BASE_URL}/api/v1/sync/complaints/logs` |
| 鉴权方式 | HTTP 请求头 `Authorization: Bearer <WORKBUDDY_SYNC_TOKEN>`(或 `X-Sync-Token` 头) |
| 数据格式 | `application/json` |
| 建议频率 | 每 20 分钟检查文件;文件变化才推送(全量推送文件内全部行即可,系统按指纹去重) |
| 幂等性 | 支持。同一行重复推送不会产生重复数据(按身份指纹 upsert) |

`BASE_URL` 为系统对外访问地址(如 `https://csctalon.example.com`),由系统管理员提供。

---

## 3. 鉴权

1. 系统管理员在 `.env` 配置:
   ```
   WORKBUDDY_SYNC_TOKEN=<随机长字符串,建议 openssl rand -hex 32>
   ```
2. workbuddy 推送时携带请求头:
   ```
   Authorization: Bearer <WORKBUDDY_SYNC_TOKEN>
   ```
3. 未配置 token → 接口返回 `503`;token 错误/缺失 → 返回 `401`。

---

## 4. 推送接口规范

### 4.1 请求

```
POST /api/v1/sync/complaints/push
Content-Type: application/json
Authorization: Bearer <token>
```

### 4.2 请求体结构

```json
{
  "source_file": "315投诉处理登记表.xlsx",
  "external_complaints": [
    { "投诉日期": "2026-08-19", "投诉编号": "502609010000000000000", "投诉人": "张三", "...": "..." }
  ],
  "card_complaints": [
    { "投诉日期": "2026-08-19", "投诉群": "卡商A群", "投诉人": "李四", "...": "..." }
  ]
}
```

- `source_file`: 可选,来源文件名(记录到同步日志)。
- `external_complaints` / `card_complaints`: 至少提供一项;为数组,元素为对象,字段名与 xlsx 列名一致(见 4.3 字段映射),也可直接使用英文字段名。
- 每次推送建议携带**文件内全部行**(系统按身份指纹去重更新,全量推送最简单可靠)。

### 4.3 字段映射(监督局-315投诉 Sheet)

| 字段名(推送用,与xlsx一致) | 库字段 | 必填 | 说明/清洗规则 |
|---|---|---|---|
| 投诉日期 | complaint_date | 建议 | 日期/时间;按北京时间解释后转UTC存储 |
| 投诉编号 | complaint_no | 否 | 剥离 `\n` 前缀;仅保留 6~64 位字母数字/连字符(垃圾编号如 `/`、`1.4403...E+24` 自动置空) |
| 平台 | platform | 否 | 字符串,超64字符截断 |
| 投诉账号 | complaint_account | 否 | |
| 公司主体 | company_entity | 否 | |
| 投诉店铺 | complaint_shop | 否 | |
| 购买订单编号 | purchase_order_no | 否 | |
| 投诉人 | complainant | 否 | 姓名清洗(纯数字/金额格式视为列错位 → 置空并记警告) |
| 投诉手机号 | complainant_phone | 否 | |
| 投诉设备号 | device_sn | 关联键 | **核心匹配键**;去括号备注(如 `188****5701（移动流量卡）`)、多SN(换行/逗号分隔)取首个 |
| 微信账户id | wechat_id | 否 | |
| 投诉问题 | problem | 否 | |
| 投诉关键词 | keyword | 否 | |
| 投诉分类 | complaint_category | 否 | |
| 解决方案 | solution | 否 | |
| 结果 | result | 否 | |
| 跟进人 | follower | 否 | 姓名清洗 |
| 处理时间 | handle_time | 否 | 日期时间清洗 |
| 处理明细 | handle_detail | 否 | |
| 内部分析 / 外部分析 | internal_analysis / external_analysis | 否 | |
| 接待客服 | reception_cs | 否 | 姓名清洗 |
| 套餐订单号 / 套餐名称 | package_order_no / package_name | 否 | |
| 套餐价格 / 实际支付金额 / 协商退款金额 | package_price / actual_pay_amount / negotiate_refund_amount | 否 | 数值清洗(货币符号/千分位/中文单位) |

### 4.4 字段映射(卡商群投诉 Sheet)

| 字段名(推送用,与xlsx一致) | 库字段 | 必填 | 说明/清洗规则 |
|---|---|---|---|
| 投诉日期 | complaint_date | 建议 | |
| 投诉群 | complaint_group | 否 | |
| 投诉人 | complainant | 否 | 姓名清洗 |
| 投诉手机号 | complainant_phone | 否 | |
| 设备号 | device_sn | 关联键 | **核心匹配键**;清洗规则同 315 |
| 投诉问题 | problem | 否 | |
| 投诉问题.1 | problem_category | 否 | 二级分类 |
| 结果 | result | 否 | |
| 解决方案 | solution | 否 | |
| 处理人 | handler | 否 | 姓名清洗 |
| 处理结果 | handle_result | 否 | |

### 4.5 响应

```json
{
  "code": 0,
  "message": "ok",
  "data": {
    "log_id": 10,
    "status": "success",
    "external": {
      "total": 2510, "inserted": 0, "updated": 2504,
      "failed": 0, "warnings": 2, "matched": 620,
      "errors": [ { "row": 1936, "level": "warning", "message": "...", "data": {} } ]
    },
    "card": {
      "total": 743, "inserted": 0, "updated": 743,
      "failed": 0, "warnings": 16, "matched": 100,
      "errors": []
    }
  }
}
```

| 字段 | 含义 |
|---|---|
| status | `success`=全部入库;`partial`=有行被丢弃(见 failed);`failed`=整体失败(需看服务端日志) |
| total / inserted / updated | 收到行数 / 新增 / 更新(重复推送时 inserted≈0) |
| failed | 被丢弃的行数(硬失败,如行解析异常),会记入 errors 并置 status=partial |
| warnings | 软警告数(行保留但某字段被清洗,如姓名列填了手机号 → 置空),仅记录不影响状态 |
| matched | 本批中按设备SN匹配到**外部投诉类工单**的行数(核心关联) |
| errors | 前50条 警告/失败明细 |

---

## 5. 数据清洗与匹配规则(系统侧自动处理)

workbuddy **无需自行清洗**,推送原始单元格内容即可,系统会:

1. **SN 清洗**: 去括号备注、多SN取首个、去首尾空白;缺失 SN 的行仍入库(只是无法关联工单)。
2. **日期**: 统一按北京时间解释,转 UTC 存储;年份笔误(如 2225)自动纠错。
3. **姓名/金额防御**: 姓名列若是数字/金额(常见列错位)置空并记录警告;金额列支持 `¥1,234.56`、`50%`、`1.2万` 等格式。
4. **去重指纹**: 每行计算**身份指纹** `md5(投诉日期 | 投诉手机号 | 设备SN | 归一化投诉问题)`,
   同指纹 upsert 更新。**重复推送/重复行不会累积**。
   设计说明:
   - 指纹**不含投诉编号**(上游易丢失/改写,实测 53% 丢失), 避免同一投诉因编号变化被拆两条;
   - 指纹**包含归一化后的投诉问题**(去空白/标点), 解决"同手机+同SN+同日期 却是两个不同投诉"
     的误合并(手机号→多SN、SN→多投诉 等小概率场景), 实测误合并仅 15 行;
   - 归一化使文本微改(空格/标点变化)不影响指纹, 跨文件匹配仍 99.8%;
   - 内容实质更新(处理结果/明细变化不影响, 仅"投诉问题"本身改变才视为新投诉)。
5. **SN→工单关联**: 按 `device_sn` 与 `service_tickets` 中**外部投诉类工单**(工信部/12315/消协/监督局等)匹配,写回 `matched_ticket_no`;一个 SN 可对应多张工单(多次投诉),`matched_ticket_no` 记录最早一张作为代表,完整关联在明细查询时按 SN 合并展示。
6. **设备主数据维护**: 推送中出现的新 SN 自动在 `devices` 表建空壳,避免外键失败。

---

## 6. 错误码

| HTTP | 场景 |
|---|---|
| 401 | token 缺失或错误 |
| 400 | 请求体非 JSON 对象 / `external_complaints` 与 `card_complaints` 均未提供 |
| 413 | 请求体过大(默认受反向代理限制,建议单次 <50MB) |
| 500 | 服务端异常(数据已回滚,重试即可) |
| 503 | `WORKBUDDY_SYNC_TOKEN` 未配置,接口未启用 |

---

## 7. 建议与约定

1. **频率**: 每 20 分钟检查文件 mtime/内容变化,变化才推送;未变化可跳过(降低负载)。
2. **推送内容**: 建议全量推送(文件内全部行)。若文件很大(>5万行),可改按"最近更新日期"增量推送,但需保证每条记录身份字段稳定。
3. **幂等**: 同一行重复推送安全;内容更新(如处理明细变化)会覆盖旧值。
4. **注意事项**: 若同一投诉的**问题文本**被修改,身份指纹会变化 → 会产生新记录(旧记录保留为历史)。建议保持"投诉问题"列稳定,变更写入"处理明细/结果/解决方案"列。
5. **日志**: 每次推送可在系统内 `GET /api/v1/sync/complaints/logs`(需登录)或数据管理页查看,包含行数/新增/更新/失败/关联数与错误明细。

---

## 8. 本地联调示例

```bash
# 1. 配置 token 后启动系统
# 2. 构造 JSON(字段用 xlsx 列名)推送到本地
curl -X POST http://127.0.0.1:8000/api/v1/sync/complaints/push \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <WORKBUDDY_SYNC_TOKEN>" \
  -d '{
    "source_file": "315投诉处理登记表.xlsx",
    "external_complaints": [
      {
        "投诉日期": "2026-08-19",
        "投诉编号": "502609190000000000000",
        "投诉人": "张三",
        "投诉手机号": "138****0000",
        "投诉设备号": "67482454178206",
        "投诉问题": "套餐无法使用要求退款",
        "解决方案": "已退款",
        "结果": "和解结案",
        "跟进人": "陈玉红",
        "处理明细": "已按实退余额"
      }
    ],
    "card_complaints": [
      {
        "投诉日期": "2026-08-19",
        "投诉群": "卡商A群",
        "投诉人": "李四",
        "投诉手机号": "139****0000",
        "设备号": "67482454178206",
        "投诉问题": "网速太差",
        "处理人": "王五"
      }
    ]
  }'
```

成功返回 `"code": 0` 及同步统计;随后即可在系统「决策看板 → 投诉专项 → 补充明细」查看。
