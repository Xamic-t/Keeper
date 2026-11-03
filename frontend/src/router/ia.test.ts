/**
 * 信息架构的可执行约束。
 *
 * 这不是"测代码"，是防止 IA 再次退化的护栏：**每条路由都必须被明确归类**——
 * 要么在一级菜单里，要么在 SECONDARY_ROUTES 里写清从哪进，
 * 要么老实待在 ORPHAN_ROUTES 里（会被单独报出来，逼着清理）。
 *
 * 背景：2026-11-03 用户连续两次指出层级问题——助理和账号管理都出现了"页面加了、
 * 旧入口没删"（设置页里两张卡片）。靠人记没用，靠测试红。
 */

import { describe, expect, it } from 'vitest'

import router from '@/router'
import { BARE_LAYOUT_ROUTES, BUSINESS_NAV, ORPHAN_ROUTES, SECONDARY_ROUTES } from '@/nav'

function routeNames(): string[] {
  return router
    .getRoutes()
    .map((record) => String(record.name ?? ''))
    .filter((name) => name && name !== 'pathMatch' && !name.startsWith('pathMatch'))
}

describe('信息架构', () => {
  it('每条路由都被明确归类（一级菜单 / 二级入口 / 孤儿）', () => {
    const classified = new Set([
      ...BUSINESS_NAV.map((item) => item.name),
      ...Object.keys(SECONDARY_ROUTES),
      ...ORPHAN_ROUTES,
      'login',
    ])
    const unclassified = routeNames().filter((name) => !classified.has(name))
    expect(unclassified, `这些页面没有归到任何一层，请先在 nav.ts 归类：${unclassified.join(', ')}`).toEqual([])
  })

  it('一级菜单里的页面必须真的存在（改名了要同步）', () => {
    const names = new Set(routeNames())
    const missing = BUSINESS_NAV.map((item) => item.name).filter((name) => !names.has(name))
    expect(missing, `导航指向了不存在的路由：${missing.join(', ')}`).toEqual([])
  })

  it('一级菜单不许有重复项（同一页挂两次是 IA 事故）', () => {
    const names = BUSINESS_NAV.map((item) => item.name)
    expect(new Set(names).size).toBe(names.length)
  })

  it('设置为二级页面：它不该出现在一级菜单里', () => {
    expect(BUSINESS_NAV.map((item) => item.name)).not.toContain('settings')
  })

  it('管理后台不在业务菜单里，且只有 owner 能进', () => {
    expect(BUSINESS_NAV.map((item) => item.name)).not.toContain('admin')
    const record = router.getRoutes().find((item) => item.name === 'admin')
    expect(record?.meta.ownerOnly).toBe(true)
    expect(record?.meta.layout).toBe('admin')
  })

  it('孤儿路由只减不增：新增必须显式写进 ORPHAN_ROUTES 并说明原因', () => {
    // 这条现在只锁"已知的两个"（login 不算孤儿）。想让 /chat 复活，就把它接进入口、
    // 从 ORPHAN_ROUTES 删掉；想删页面，也一并从这里删。
    expect(ORPHAN_ROUTES).toEqual(['chat'])
  })

  it('裸布局（无业务外壳）的页面是名单里的那几个', () => {
    const bare = router
      .getRoutes()
      .filter((record) => record.meta.layout === 'admin')
      .map((record) => String(record.name))
    expect(bare.sort()).toEqual(['admin'])
    expect(BARE_LAYOUT_ROUTES).toContain('login')
  })
})
