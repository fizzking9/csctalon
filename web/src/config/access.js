/**
 * 权限判据唯一来源 (#260 B4-4, S-P1-4 访问控制单源化)
 *
 * ⚠️ 本文件是**角色档位与可见性判据的唯一定义处**:
 *   - stores/auth.js 的 isAdmin/isManager 从 ROLE_LADDER 派生(不再手写名单);
 *   - router 守卫三级判断(meta.admin / meta.manager / meta.roles)统一走 roleAllowed;
 *   - MenuTree 菜单显隐(roleVisible)统一走 roleAllowed。
 *   档位名单要调整, 只改 ROLE_LADDER —— 三处自动一致, 禁止在别处再写角色数组。
 *
 * 设计要点:
 *  - 档位是**嵌套包含**语义: admin ⊂ manager ⊂ 全员(五角色:
 *    admin/supervisor/specialty_lead/specialist/agent)。
 *  - decl(声明)支持两种形态:
 *      'admin' / 'manager'  —— 档位别名(映射 ROLE_LADDER)
 *      ['admin','supervisor'] —— 精确白名单(逐字匹配角色)
 *      null / []            —— 全员
 *  - 本文件只做**前端判据**; 数据/写操作权限仍由后端 API 权限锁定
 *    (A-20260910-004 §四: 前端显隐不构成安全边界)。
 */

/** 角色档位名单(唯一来源) */
export const ROLE_LADDER = {
  admin: ['admin'],
  // 管理层: admin + supervisor + specialty_lead(可看决策看板/管理闭环, 规划设计 v0.5 §二)
  manager: ['admin', 'supervisor', 'specialty_lead'],
}

/**
 * 统一可见性判据。
 * @param {string|Array|null} decl 菜单/路由的 roles 声明(档位别名、精确数组或 null)
 * @param {string} role 当前用户角色
 * @returns {boolean}
 */
export function roleAllowed(decl, role) {
  if (decl == null) return true
  const whitelist = typeof decl === 'string' ? ROLE_LADDER[decl] : decl
  if (!whitelist || !whitelist.length) {
    // 字符串别名查不到名单 = 写错的档位名 → fail-closed(不可见), 防止手误泄漏页面;
    // 显式空数组 [] 仍视为全员(菜单语义: roles: [] = 不限)。
    return typeof decl !== 'string'
  }
  return whitelist.includes(role)
}
