import type { LanguageCode } from "@/i18n";
import type { ThemeAppearance, ThemeVariables } from "@/lib/theme";

export interface DesktopAppearancePayload {
  appearance?: ThemeAppearance;
  fontFamily?: string;
  codeFontFamily?: string;
  themeVariables?: ThemeVariables;
  persist?: boolean;
}

export interface SocketDiagnosticPayload {
  event:
    | "connect-start"
    | "connect-error"
    | "reconnect-attempt"
    | "reconnect-failed"
    | "connected"
    | "disconnected"
    | "connection-timeout";
  active?: boolean;
  attempt?: number;
  durationMs?: number;
  message?: string;
  transport?: string;
  url?: string;
}

export interface CloseDecisionPayload {
  confirmed: boolean;
  reason?: string;
}

declare global {
  interface Window {
    openficDesktopHost?: {
      publishAppearance: (payload: DesktopAppearancePayload) => void;
      publishLanguage: (language: LanguageCode) => void;
      publishSocketDiagnostic: (payload: SocketDiagnosticPayload) => void;
      onRequestClose?: (handler: () => void | Promise<void>) => () => void;
      respondCloseDecision?: (decision: CloseDecisionPayload) => void;
    };
  }
}

export function publishDesktopAppearance(payload: DesktopAppearancePayload): void {
  window.openficDesktopHost?.publishAppearance(payload);
}

export function publishDesktopLanguage(language: LanguageCode): void {
  window.openficDesktopHost?.publishLanguage(language);
}

export function publishSocketDiagnostic(payload: SocketDiagnosticPayload): void {
  window.openficDesktopHost?.publishSocketDiagnostic?.(payload);
}

export function onDesktopRequestClose(handler: () => void | Promise<void>): (() => void) | undefined {
  return window.openficDesktopHost?.onRequestClose?.(handler);
}

export function respondDesktopCloseDecision(decision: CloseDecisionPayload): void {
  window.openficDesktopHost?.respondCloseDecision?.(decision);
}

