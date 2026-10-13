import type { DashboardPushEvent } from "./dashboardPushToast";

type WailsWindow = Window & {
  _wails?: { invoke?: (name: string) => unknown };
};

export function sendDesktopNotification(
  event: DashboardPushEvent,
  title: string,
): boolean {
  const invoke = (window as WailsWindow)._wails?.invoke;
  if (typeof invoke !== "function") return false;
  const id = event.notification_id?.trim();
  if (!id) return false;
  const payload = JSON.stringify({
    id,
    title,
    body: event.text,
    agent_id: event.agent_id,
    thread_id: event.thread_id,
  });
  invoke(
    `wails:event:emit:desktop:notification:${encodeURIComponent(payload)}`,
  );
  return true;
}

export function hasDesktopNotificationBridge(): boolean {
  return typeof (window as WailsWindow)._wails?.invoke === "function";
}
