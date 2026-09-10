// vitest 全局测试 setup
//
// 背景(2026-09-10 实测): 本项目 jsdom 30.0.1 下 **window.localStorage 为 undefined**,
// 而 `stores/auth.js` 在 setup 阶段就读取 localStorage → 任何 mount 组件的测试
// 都会报 `Cannot read properties of undefined (reading 'getItem')`。
//
// 这里补一个最小可用的内存实现, 只覆盖测试需要的 getItem/setItem/removeItem/clear。
// 不去引 jsdom 的完整实现(其 localStorage 依赖非 opaque origin, 配置更绕)。

class MemoryStorage {
  constructor() {
    this._map = new Map()
  }

  getItem(key) {
    const k = String(key)
    return this._map.has(k) ? this._map.get(k) : null
  }

  setItem(key, value) {
    this._map.set(String(key), String(value))
  }

  removeItem(key) {
    this._map.delete(String(key))
  }

  clear() {
    this._map.clear()
  }

  key(index) {
    return Array.from(this._map.keys())[index] ?? null
  }

  get length() {
    return this._map.size
  }
}

if (typeof globalThis.localStorage === 'undefined') {
  const storage = new MemoryStorage()
  Object.defineProperty(globalThis, 'localStorage', {
    value: storage,
    configurable: true,
    writable: true,
  })
  if (typeof globalThis.window !== 'undefined') {
    Object.defineProperty(globalThis.window, 'localStorage', {
      value: storage,
      configurable: true,
      writable: true,
    })
  }
}

if (typeof globalThis.sessionStorage === 'undefined') {
  const storage = new MemoryStorage()
  Object.defineProperty(globalThis, 'sessionStorage', {
    value: storage,
    configurable: true,
    writable: true,
  })
}
