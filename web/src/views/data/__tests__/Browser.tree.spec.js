/**
 * 数据查看树渲染测试（工单 #100）
 *
 * 为什么必须挂载真实组件而不是只测数据：
 *   A-20260910-004 的教训 —— MenuTree 的 nodeChildren 漏合并 node.children，
 *   导致**菜单整块不渲染**，而数据层测试全绿（只测了配置没测渲染）。
 *   数据查看树有同类风险，且 #100 新引入了「投影」与「分组前缀 key」两处易错点：
 *     ① `:index` 若仍只用 code，同一 code 出现在两个分组时会同时点亮两项；
 *     ② 投影项若被当成普通项渲染，使用者会以为多了一张表（违反"禁止重复展示"）。
 *
 * 本测试直接读 Browser.vue 源码做静态断言（不挂载 —— 该组件依赖 element-plus
 * 全局注册与大量 API mock，挂载成本高且脆弱）。断言的是**必须存在的结构**，
 * 属于"防回归护栏"而非渲染快照。
 */

import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import path from 'node:path'
import { describe, expect, it } from 'vitest'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const SRC = readFileSync(path.resolve(__dirname, '../Browser.vue'), 'utf8')

describe('数据查看树 · 菜单 key（同一 code 多分组时不得同时点亮）', () => {
  it('menu-item 的 index 必须用 分组::code 复合 key', () => {
    expect(SRC).toMatch(/:index="menuKey\(grp\.category, src\.code\)"/)
  })

  it('必须提供 menuKey 函数拼接前缀', () => {
    expect(SRC).toMatch(/function menuKey\(category, code\)/)
    expect(SRC).toMatch(/return `\$\{category\}::\$\{code\}`/)
  })

  it('选中态必须同样带分组前缀（否则与 index 对不上，高亮失效）', () => {
    expect(SRC).toMatch(/const activeMenuKey = computed/)
    expect(SRC).toMatch(/menuKey\(currentSource\.value\.category, currentSource\.value\.code\)/)
  })

  it('选择回调必须按 (分组, code) 双条件定位', () => {
    expect(SRC).toMatch(/function handleMenuSelect\(key\)/)
    expect(SRC).toMatch(/s\.code === code && \(s\.category \|\| '其他'\) === category/)
  })
})

describe('数据查看树 · 分组顺序由服务端决定', () => {
  it('前端不得再硬编码 CATEGORY_ORDER（两份定义必然漂移）', () => {
    expect(SRC).not.toMatch(/const CATEGORY_ORDER\s*=/)
    expect(SRC).not.toMatch(/'现金流'\s*,\s*'工单与客服'/)
  })

  it('分组按后端返回顺序收集（Map 保持插入序）', () => {
    expect(SRC).toMatch(/const groups = new Map\(\)/)
    expect(SRC).toMatch(/for \(const src of sources\.value\)/)
  })
})

describe('数据查看树 · 投影必须声明真身（禁止无声明重复展示）', () => {
  it('投影项渲染「投影」标签', () => {
    expect(SRC).toMatch(/v-else-if="src\.is_projection"/)
    expect(SRC).toMatch(/>投影</)
  })

  it('投影必须展示真身分组与名称（AGENTS.md 界面纪律第 5 条）', () => {
    expect(SRC).toMatch(/src\.true_body_category/)
    expect(SRC).toMatch(/src\.true_body_name/)
  })

  it('投影说明必须讲清「为什么放在这里」而不只是「这是什么」', () => {
    expect(SRC).toMatch(/读取同一张表/)
    expect(SRC).toMatch(/只读/)
    expect(SRC).toMatch(/口径分裂/)
  })

  it('表头必须标注只读投影与真身位置', () => {
    expect(SRC).toMatch(/只读投影 · 真身/)
  })
})

describe('数据查看树 · 命名一致性可见化', () => {
  it('表头显示物理表名而非 code（人人能对照库）', () => {
    expect(SRC).toMatch(/\{\{ currentSource\.table_name \}\}/)
  })

  it('计数排除投影项（投影是别名，计入会让表数虚增）', () => {
    expect(SRC).toMatch(/s\.status !== 'placeholder' && !s\.is_projection/)
  })
})

describe('数据查看树 · 边界说明（消除"表是不是都在这里"）', () => {
  it('页头必须有 ⓘ 说明本页只列可下钻原始表', () => {
    expect(SRC).toMatch(/本页列出的是「可下钻的原始业务表」/)
  })

  it('必须说明未纳入表的去向并指向全量清单文档', () => {
    expect(SRC).toMatch(/工作流/)
    expect(SRC).toMatch(/配置与日志/)
    expect(SRC).toMatch(/维度主数据/)
    expect(SRC).toMatch(/中间过程表/)
    expect(SRC).toMatch(/database 表结构说明|数据库表结构说明/)
  })
})

describe('数据查看树 · 三层设计说明落地', () => {
  it('代码层：头部必须有「设计说明 (Design Rationale)」注释块', () => {
    expect(SRC).toMatch(/设计说明（Design Rationale）/)
  })

  it('代码层：五要素齐全（目的/逻辑/口径/取舍 + 工单号）', () => {
    for (const key of ['目的：', '逻辑：', '口径：', '取舍：']) {
      expect(SRC, `设计说明缺「${key}」`).toContain(key)
    }
    expect(SRC).toMatch(/#100/)
  })

  it('页面层：ⓘ 气泡必须含「取舍」性质的内容（不做什么及原因）', () => {
    expect(SRC).toMatch(/不在此列/)
    expect(SRC).toMatch(/不是「选一张表看数据」的用途/)
  })

  it('取舍段必须写明"全库表目录"是本页明确不做的', () => {
    expect(SRC).toMatch(/不做「全库表目录」/)
    expect(SRC).toMatch(/不为投诉工单另建数据源/)
    expect(SRC).toMatch(/不预置投诉筛选/)
  })
})
