// The desktop app (Tauri, src-tauri/): the same GUI in the system webview. What a browser page does
// with Web Serial, downloads and fetch goes through the app's own commands there.
import { t } from "./i18n/index.svelte";

/** running inside the Tauri app */
export const isNative = () => typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;

export async function invoke<T>(cmd: string, args?: Record<string, unknown>): Promise<T> {
  const core = await import("@tauri-apps/api/core");
  return core.invoke<T>(cmd, args);
}

/** a file in the native "save as" dialog; false if cancelled. The app shows the dialog and writes
 * the file itself: the page cannot name a path (Review 19.11.2026). */
export async function nativeSave(data: Uint8Array, name: string): Promise<boolean> {
  return invoke<boolean>("save_file", { name, title: t("common.saveAs"), data: Array.from(data) });
}

/** GET from GitHub through the app (no CORS there) */
export async function nativeGet(url: string): Promise<Uint8Array> {
  return new Uint8Array(await invoke<number[]>("http_get", { url }));
}
