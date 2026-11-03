import { describe, expect, it } from 'vitest'

import {
  formatNumber,
  formatRelative,
  isEmptyAccount,
  roleLabel,
  statusLabel,
} from '@/lib/adminFormat'

describe('formatNumber', () => {
  it('按中文习惯加千分位', () => {
    expect(formatNumber(1234)).toBe('1,234')
  })

  it('空值当 0，不要显示 undefined', () => {
    expect(formatNumber(null)).toBe('0')
    expect(formatNumber(undefined)).toBe('0')
  })
})

describe('formatRelative', () => {
  const now = new Date('2026-11-03T12:00:00+08:00')

  it('没有登录过就说"还没用过"，不要空着', () => {
    expect(formatRelative(null, now)).toBe('还没用过')
  })

  it('一分钟内算刚刚', () => {
    expect(formatRelative('2026-11-03T11:59:30+08:00', now)).toBe('刚刚')
  })

  it('分钟 / 小时 / 天 三档', () => {
    expect(formatRelative('2026-11-03T11:48:00+08:00', now)).toBe('12 分钟前')
    expect(formatRelative('2026-11-03T09:00:00+08:00', now)).toBe('3 小时前')
    expect(formatRelative('2026-11-01T12:00:00+08:00', now)).toBe('2 天前')
  })

  it('超过 30 天直接给日期，别写"5 周前"', () => {
    expect(formatRelative('2026-10-01T12:00:00+08:00', now)).toBe('2026-10-01')
  })

  it('时间在未来（时区/时钟偏差）也不显示负数', () => {
    expect(formatRelative('2026-11-03T13:00:00+08:00', now)).toBe('刚刚')
  })

  it('字符串坏了不崩，给一句能看懂的话', () => {
    expect(formatRelative('not-a-date', now)).toBe('时间坏了')
  })
})

describe('roleLabel / statusLabel', () => {
  it('库里英文，界面说人话', () => {
    expect(roleLabel('owner')).toBe('管理员')
    expect(roleLabel('member')).toBe('成员')
    expect(statusLabel(true)).toBe('正常')
    expect(statusLabel(false)).toBe('已停用')
  })
})

describe('isEmptyAccount', () => {
  it('全 0 视为"还没用过"——后台要能一眼看出空账号', () => {
    expect(isEmptyAccount({ persons: 0, facts: 0, chat_messages: 0 })).toBe(true)
    expect(isEmptyAccount({ persons: 0, facts: 1 })).toBe(false)
  })
})
