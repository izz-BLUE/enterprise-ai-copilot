# React + Vite

本模板提供了在 Vite 中运行 React 的最小配置，包含 HMR 和部分 ESLint 规则。

目前有两个官方插件可用：

- [@vitejs/plugin-react](https://github.com/vitejs/vite-plugin-react/blob/main/packages/plugin-react) 使用 [Oxc](https://oxc.rs)。
- [@vitejs/plugin-react-swc](https://github.com/vitejs/vite-plugin-react/blob/main/packages/plugin-react-swc) 使用 [SWC](https://swc.rs/)。

## React Compiler

由于会影响开发和构建性能，本模板未启用 React Compiler。如需添加，请参阅[此文档](https://react.dev/learn/react-compiler/installation)。

## 扩展 ESLint 配置

如果你正在开发生产应用，建议启用带类型感知 lint 规则的 TypeScript。关于如何在项目中集成 TypeScript 和 [`typescript-eslint`](https://typescript-eslint.io)，请参阅 [TS template](https://github.com/vitejs/vite/tree/main/packages/create-vite/template-react-ts)。
