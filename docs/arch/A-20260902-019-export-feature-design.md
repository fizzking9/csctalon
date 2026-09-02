# 报表/报告导出功能设计(规划稿)

**状态**：LOCKED（v1.0）
**锁定日期**：20260902
**锁定脚本**：scripts/lock-arch-docs.sh（手动编号 019/020）


> 状态:**已实现(2026-09-02 复核)**
> 说明:原文档标注"设计规划中,占位未实现"。经 2026-09-02 代码复核,**导出功能已在多个模块实际落地**:
> 见下方"实现情况说明"。
> 创建:2026-07-26 · 复核:2026-09-02

## 0. 实现情况说明(2026-09-02 复核补充)

**原规划稿的三种导出形式,实际已通过多个模块落地:**

| 模块 | 导出形式 | 实现 |
|---|---|---|
| 分析/退费/投诉 | Excel 数据导出 | `analytics.py` / `complaints.py`(backlog/export) |
| 数据源浏览 | Excel 导出 | `data_sources.py`(EXPORT_MAX_ROWS 限制) |
| HR/任职 | Excel 导出 | `staff_assignments.py`(export) |
| 发票 | Excel 导出 | `invoices.py` |
| 前端 | 导出按钮/下载 | 各管理页 download/export |

**与规划差异**:原规划设想的"报告导出给领导看"形态,因报告/周报模块(editable-report-kanban)另行实现,Excel 导出主要面向明细数据下沉;整体目标(对外呈现、快照)不变。

## 1. 定位与目标

满足管理层「偶尔需要导出报表数据或报告给领导查看」的需求(A1)。系统作为数据分析辅助平台,导出能力服务于**对外呈现**(给非本系统用户的领导查看),而非系统内部操作。

三种形式并存,覆盖不同场景:

| 形式 | 适用场景 | 受众 | 时效性 |
|------|----------|------|--------|
| Excel 数据导出 | 明细数据下沉、领导自行二次透视 | 需要原始数据的管理者/财务 | 一次性快照 |
| 只读链接分享 | 远程查看、免登录、即时分享 | 不常登录系统的领导 | 链接有效期内的实时数据 |
| PDF 报告 | 正式归档、打印、会议汇报 | 正式场合的领导 | 一次性快照、排版固定 |

## 2. 前置依赖(为何暂不实现)

导出质量取决于上游数据视图的成熟度,当前未达可导出标准:

1. **分析视图体系尚未完整**:ViewBuilder 已可构建自定义视图,但面向领导的「标准报告视图」(如月度运营概览、现金流汇总、退款挽回率)尚未沉淀为预设。
2. **组合条件与解读配置未完善**:导出需要把"字段语义解读"和"分类规则"固化进报告,目前规则引擎刚落地,典型组合未沉淀。
3. **报告内容编排未定义**:PDF/链接形式的报告需要章节结构(标题/摘要/图表/表格/说明文字),这部分元数据模型尚不存在。
4. **图表快照能力缺失**:PDF 报告需要把 ECharts 图表渲染为静态图,需引入服务端渲染或前端截图。

> 结论:先占位,待上述 1~4 项在后续迭代中完善,再按本设计落地。

## 3. 架构设计

### 3.1 总体

导出能力**依附于分析视图(AnalysisView)**,不独立建数据源。即:先在 ViewBuilder 配好一个视图(含维度/度量/筛选/图表),导出动作 = 把该视图的执行结果以三种载体之一输出。

```
AnalysisView(视图配置)
   │ execute_view() → 结构化结果(rows + chart_spec)
   ├─→ Excel 导出:  rows → openpyxl 工作簿
   ├─→ 只读链接:    view_code + token → 免登录渲染页
   └─→ PDF 报告:    report_template(章节+视图+说明) → weasyprint
```

### 3.2 后端 API 设计(占位,待实现)

```
# Excel 数据导出(单视图明细)
GET  /api/v1/exports/views/{view_code}/excel?time_start=&time_end=&filters=
  → 返回 .xlsx 文件流
  实现:openpyxl,基于 execute_view 的 rows 生成表,首行表头+冻结+筛选

# 只读链接(生成免登录分享 token)
POST /api/v1/exports/share-links
  body: { view_code, expires_in_hours, time_range, filters, title }
  → 返回 { link_id, url, expires_at }
GET  /api/v1/exports/share/{link_id}   (免登录,公开)
  → 返回视图执行结果 + 元信息,供公开页渲染
GET  /api/v1/exports/share-links       (管理:列出已生成链接)
DELETE /api/v1/exports/share-links/{link_id}

# PDF 报告(基于报告模板)
POST /api/v1/exports/reports/pdf
  body: { template_code, time_range, params }
  → 返回 .pdf 文件流
GET  /api/v1/exports/report-templates          (报告模板 CRUD)
POST /api/v1/exports/report-templates
PUT  /api/v1/exports/report-templates/{code}
```

### 3.3 数据模型(待实现)

```python
# 只读分享链接
class ShareLink(IDMixin, TimestampMixin, Base):
    __tablename__ = "export_share_links"
    link_id: Mapped[str]  # 随机 token,URL 中暴露
    view_code: Mapped[str]  # 关联分析视图
    title: Mapped[str]
    created_by: Mapped[str | None]
    expires_at: Mapped[datetime | None]  # None = 永久(可配置上限)
    time_range: Mapped[str | None]  # JSON: {start, end} 或相对偏移
    filters: Mapped[str | None]  # JSON: 视图执行时的额外筛选
    access_count: Mapped[int]  # 访问次数统计

# PDF 报告模板(章节编排)
class ReportTemplate(IDMixin, TimestampMixin, Base):
    __tablename__ = "export_report_templates"
    code: Mapped[str]  # 唯一
    name: Mapped[str]
    sections: Mapped[list]  # JSONB: [{title, type: view|text|chart, view_code, text, ...}]
    page_size: Mapped[str]  # A4/Letter
    orientation: Mapped[str]  # portrait/landscape
    header: Mapped[str | None]  # Markdown 抬头
    footer: Mapped[str | None]
```

## 4. 三种形式详细设计

### 4.1 Excel 数据导出

- **触发点**:分析视图渲染页(ViewRender.vue)右上角「导出 Excel」按钮
- **范围**:当前视图执行结果的全量 rows(不限分页)
- **内容**:首行表头(中文字段名)+ 数据行;冻结首行;自动列宽;首行筛选器
- **多 segment 视图**:每个 segment 一个 sheet,外加一个"合计"sheet
- **技术**:openpyxl(纯 Python,无系统依赖),StreamingWorksheet 处理大数据量
- **权限**:admin/supervisor 可导出;agent 不可
- **审计**:导出动作计入操作日志(action=报表.Excel导出,target=analysis_view)

### 4.2 只读链接分享

- **触发点**:视图渲染页「分享」按钮 → 弹窗设置标题/有效期/时间范围
- **链接形式**:`http://<host>/share/{link_id}`(前端独立路由,免登录)
- **渲染**:公开页复用 ViewRender 组件,数据走 `/exports/share/{link_id}` 免鉴权接口
- **有效期**:默认 7 天,可选 1/7/30 天/永久;过期返回"链接已失效"
- **安全**:
  - link_id 用 32 字节随机 token(URL-safe)
  - 不暴露原始 view_code 的编辑能力,只读
  - 访问计入 access_count,可在管理页查看访问记录
  - 可随时吊销(DELETE)
- **适用**:领导微信/邮件点开即看,无需账号

### 4.3 PDF 报告

- **定位**:正式、排版固定、可打印归档;基于「报告模板」而非单视图
- **报告模板**:由多个 section 编排而成
  - `type=view`:嵌入一个分析视图的执行结果(表格 + 图表)
  - `type=chart`:仅图表
  - `type=text`:Markdown 文字说明(摘要、结论)
- **图表渲染**:前端 ECharts → 用 `echarts-server`(node)或 `pyecharts` 服务端渲染为 PNG,嵌入 PDF
- **PDF 生成**:`weasyprint`(HTML/CSS → PDF,支持中文,需装字体);或备选 `reportlab`
- **触发点**:「报表中心」页选择模板 + 时间范围 → 生成 PDF 下载
- **典型模板示例**:
  - 月度运营概览:现金流汇总表 + 退款分类饼图 + 人员操作排行 + 文字结论
  - 退款挽回专项:挽回率趋势 + 客服排行 + 明细 Top20

## 5. 前端交互设计(占位)

### 5.1 报表中心页(占位)

路由:`/report/center`(admin/supervisor 可见)
菜单:新增「报表中心」一级菜单(manager 可见,与策略发布同级结构)

占位页内容:
- 三个能力卡片(Excel 导出 / 只读链接 / PDF 报告),每张标注「规划中」
- 说明文字:前置依赖(数据视图完善后上线)
- 预留入口按钮(禁用态)

### 5.2 视图页导出按钮(占位)

ViewRender.vue 右上角预留「导出」「分享」按钮(禁用 + tooltip "功能规划中"),待实现时激活。

## 6. 实施路线图

| 阶段 | 内容 | 依赖 |
|------|------|------|
| P-1(前置) | 沉淀面向领导的标准预设视图(月度概览/现金流/挽回率等) | ViewBuilder 已就绪 |
| P-2(前置) | 报告内容编排元数据(ReportTemplate 模型)落地 | 本设计 3.3 |
| P3-1 | Excel 导出(最简,单视图 → xlsx) | openpyxl |
| P3-2 | 只读链接分享(ShareLink + 公开渲染页) | 独立路由 |
| P3-3 | PDF 报告(模板编排 + 图表渲染 + weasyprint) | 成本最高,最后做 |

## 7. 当前占位实现说明

本次仅落地:
1. 本设计文档
2. 前端「报表中心」占位页(`/report/center`),展示三种规划能力 + "规划中"标识
3. 菜单与路由接入(manager 可见)

不实现后端导出逻辑、不建表、不接入实际视图执行。
