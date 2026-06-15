import { createContext, useContext } from "react";

// 人物详情打开器（点头像→看详情）。独立模块，避免 Portrait↔Person 循环依赖。
export const PersonCtx = createContext<(name: string) => void>(() => {});
export const usePerson = () => useContext(PersonCtx);
