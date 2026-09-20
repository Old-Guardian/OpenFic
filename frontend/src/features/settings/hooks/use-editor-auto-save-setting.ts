/**
 * useEditorAutoSaveSetting Hook
 *
 * 封装编辑器全局自动保存设置的读取、加载与错误策略。
 * 行为契约：
 * 1. 设置首次加载完成前（isLoading），isReady=false, enabled=false，不启动自动保存，手动保存仍可用；
 * 2. 读取失败时（isError），isReady=false, enabled=false，不凭空开启自动保存；
 * 3. 成功读取时（isSuccess），isReady=true, enabled=Boolean(settings.editorAutoSave)（缺失时兼容旧后端默认 true）。
 */

import { useQuery } from "@tanstack/react-query";

import { fetchSettings } from "../lib/settings-api";

export interface EditorAutoSaveSettingResult {
  /** 设置是否已就绪（成功加载且存在设置数据） */
  isReady: boolean;
  /** 自动保存是否开启（仅在就绪且配置为 true 时为 true） */
  enabled: boolean;
  /** 设置查询是否正在加载中 */
  isLoading: boolean;
  /** 设置查询是否出错 */
  isError: boolean;
  /** 错误信息 */
  error: unknown;
  /** 重试拉取设置 */
  refetch: () => void;
}

export function useEditorAutoSaveSetting(): EditorAutoSaveSettingResult {
  const { data, isSuccess, isLoading, isError, error, refetch } = useQuery({
    queryKey: ["settings"],
    queryFn: fetchSettings,
  });

  const isReady = isSuccess && Boolean(data);
  const enabled = isReady ? Boolean(data?.editorAutoSave) : false;

  return {
    isReady,
    enabled,
    isLoading,
    isError,
    error,
    refetch,
  };
}
