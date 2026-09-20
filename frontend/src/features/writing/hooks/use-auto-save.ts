/**
 * Auto Save Hook
 *
 * 统一重定向到全局共享的 useAutoSave 调度模块，
 * 移除重复的 beforeunload 监听与冗余定时器。
 */

export * from "@/hooks/use-auto-save";
