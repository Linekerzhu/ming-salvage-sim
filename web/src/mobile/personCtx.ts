import { createContext, useContext } from "react";

// 人物详情打开器（点头像→看详情）。独立模块，避免 Portrait↔Person 循环依赖。
export type PersonFocus = "intrigue";
export type PersonOpenTarget = string | { name: string; focus?: PersonFocus };
export type PersonOpen = (target: PersonOpenTarget) => void;

export const PersonCtx = createContext<PersonOpen>(() => {});
export const usePerson = () => useContext(PersonCtx);
