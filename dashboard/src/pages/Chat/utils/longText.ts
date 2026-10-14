export const LONG_TEXT_CHARACTER_THRESHOLD = 2_000;
export const LONG_TEXT_LINE_THRESHOLD = 12;

export function isLongComposerText(value: string): boolean {
  return (
    value.length >= LONG_TEXT_CHARACTER_THRESHOLD ||
    value.split(/\r?\n/).length >= LONG_TEXT_LINE_THRESHOLD
  );
}
