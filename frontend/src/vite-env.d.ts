/// <reference types="vite/client" />

// `import x from './f.ts?raw'` 的类型。vite/client 覆盖了多数 `?raw`,
// 但不含 `.ts` 后缀的那种(它本就是可执行模块)。布局门要读 vite.config.ts
// 的**源文本**,故在此声明。
declare module '*.ts?raw' {
  const src: string
  export default src
}
