import '@testing-library/jest-dom'

// jsdom **无布局引擎**,也没有 ResizeObserver —— Recharts 的
// ResponsiveContainer 在 mount 时就要用它。这里打一个最小桩。
//
// ⚠ 这正是 blindSpots:桩让 ResponsiveContainer 不崩,但它测不出真实浏览器
// 里「父容器无高度 → 零宽白图」那种**字面意义的白屏**。那只能靠人工门。
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
globalThis.ResizeObserver =
  globalThis.ResizeObserver ?? (ResizeObserverStub as unknown as typeof ResizeObserver)
