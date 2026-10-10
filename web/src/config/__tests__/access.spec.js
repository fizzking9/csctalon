// 权限判据单源测试 (#260 B4-4, S-P1-4 访问控制单源化)
//
// 目的: 锁住 config/access.js 的判据语义 —— 档位别名/精确数组/null 三种声明
//   在 roleAllowed 下行为正确。router 守卫与 MenuTree 显隐都走这一个函数,
//   这里回归 = 全站权限判断回归。

import { describe, it, expect } from 'vitest'
import { ROLE_LADDER, roleAllowed } from '@/config/access'

describe('access - ROLE_LADDER 档位定义', () => {
  it('manager 档 = admin + supervisor + specialty_lead(嵌套包含 admin)', () => {
    expect(ROLE_LADDER.manager).toEqual(['admin', 'supervisor', 'specialty_lead'])
    expect(ROLE_LADDER.admin).toEqual(['admin'])
  })
})

describe('access - roleAllowed 声明形态', () => {
  it('null / [] / undefined = 全员可见', () => {
    expect(roleAllowed(null, 'agent')).toBe(true)
    expect(roleAllowed([], 'agent')).toBe(true)
    expect(roleAllowed(undefined, 'agent')).toBe(true)
  })

  it('档位别名: admin 档仅 admin 命中', () => {
    expect(roleAllowed('admin', 'admin')).toBe(true)
    expect(roleAllowed('admin', 'supervisor')).toBe(false)
    expect(roleAllowed('admin', 'agent')).toBe(false)
  })

  it('档位别名: manager 档覆盖 admin/supervisor/specialty_lead, 排除 specialist/agent', () => {
    expect(roleAllowed('manager', 'admin')).toBe(true)
    expect(roleAllowed('manager', 'supervisor')).toBe(true)
    expect(roleAllowed('manager', 'specialty_lead')).toBe(true)
    expect(roleAllowed('manager', 'specialist')).toBe(false)
    expect(roleAllowed('manager', 'agent')).toBe(false)
  })

  it('精确数组白名单: 逐字匹配, 不做档位展开', () => {
    expect(roleAllowed(['admin', 'supervisor'], 'admin')).toBe(true)
    expect(roleAllowed(['admin', 'supervisor'], 'specialty_lead')).toBe(false)
    expect(roleAllowed(['specialist'], 'specialist')).toBe(true)
  })

  it('未知档位别名(不在 ROLE_LADDER)按不可见处理, 不抛错', () => {
    expect(roleAllowed('no_such_tier', 'admin')).toBe(false)
  })
})

describe('access - 与 auth store 档位一致(单源派生)', () => {
  it('auth store 的 isAdmin/isManager 与 ROLE_LADDER 判定一致', async () => {
    const { createPinia, setActivePinia } = await import('pinia')
    const { useAuthStore } = await import('@/stores/auth')
    setActivePinia(createPinia())
    const auth = useAuthStore()

    for (const role of ['admin', 'supervisor', 'specialty_lead', 'specialist', 'agent', '']) {
      auth.user = { role, display_name: role }
      expect(auth.isAdmin, `admin 档 @${role}`).toBe(roleAllowed('admin', role))
      expect(auth.isManager, `manager 档 @${role}`).toBe(roleAllowed('manager', role))
    }
  })
})
