const STORAGE_KEY = "octop:chat-expert-order";

export function readExpertOrder(storage: Storage): string[] {
  try {
    const value: unknown = JSON.parse(storage.getItem(STORAGE_KEY) ?? "[]");
    if (!Array.isArray(value)) return [];
    return value.filter(
      (item, index): item is string =>
        typeof item === "string" &&
        item.length > 0 &&
        value.indexOf(item) === index,
    );
  } catch {
    return [];
  }
}

export function writeExpertOrder(storage: Storage, order: string[]): void {
  storage.setItem(STORAGE_KEY, JSON.stringify(order));
}

export function mergeExpertOrder(saved: string[], current: string[]): string[] {
  const available = new Set(current);
  const kept = saved.filter((id) => available.has(id));
  const known = new Set(kept);
  return [...kept, ...current.filter((id) => !known.has(id))];
}

export function moveExpert(
  order: string[],
  sourceId: string,
  targetId: string,
): string[] {
  if (sourceId === targetId) return order;
  const sourceIndex = order.indexOf(sourceId);
  const targetIndex = order.indexOf(targetId);
  if (sourceIndex < 0 || targetIndex < 0) return order;

  const next = [...order];
  const [source] = next.splice(sourceIndex, 1);
  const nextTargetIndex = next.indexOf(targetId);
  next.splice(nextTargetIndex, 0, source);
  return next;
}
