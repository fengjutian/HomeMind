/**
 * Soft-close incomplete Markdown constructs while a reply is still streaming.
 * Without this, an open fence (` ``` `) makes remark swallow the rest of the
 * document as a code block until the closing fence arrives — causing a big
 * layout jump ("jitter") on every few tokens.
 */
export function stabilizeStreamingMarkdown(content: string): string {
  if (!content) return content;

  let result = content;

  // Unclosed fenced code block (odd number of fence openers at line start).
  const fenceOpens = result.match(/^ {0,3}(`{3,}|~{3,})/gm);
  if (fenceOpens && fenceOpens.length % 2 === 1) {
    const last = fenceOpens[fenceOpens.length - 1] ?? "```";
    const marker = last.trim()[0] === "~" ? "~~~" : "```";
    result += result.endsWith("\n") ? marker : `\n${marker}`;
  }

  return result;
}

/**
 * Repair a high-confidence malformed directory tree produced by a missing
 * opening fence. In that case the intended closing fence is parsed as an
 * opener, so the tree collapses into prose and everything after it becomes a
 * code block.
 */
export function repairOrphanedDirectoryTreeFence(content: string): string {
  if (!content) return content;

  const lines = content.split("\n");
  const bareFenceIndexes = lines.flatMap((line, index) =>
    /^ {0,3}(`{3,}|~{3,})\s*$/.test(line) ? [index] : [],
  );
  const allFenceCount = lines.filter((line) =>
    /^ {0,3}(`{3,}|~{3,})/.test(line),
  ).length;

  // Limit repair to one unambiguous orphan fence. Multiple fences may be
  // intentional nested examples and are unsafe to reinterpret heuristically.
  if (allFenceCount !== 1 || bareFenceIndexes.length !== 1) return content;

  const fenceIndex = bareFenceIndexes[0];
  if (fenceIndex === undefined) return content;

  let blockEnd = fenceIndex - 1;
  while (blockEnd >= 0 && lines[blockEnd]?.trim() === "") blockEnd -= 1;
  if (blockEnd < 0) return content;

  let blockStart = blockEnd;
  while (blockStart > 0 && lines[blockStart - 1]?.trim() !== "") {
    blockStart -= 1;
  }

  const treeLines = lines
    .slice(blockStart, blockEnd + 1)
    .filter((line) => /(?:├|└|│).*[─-]/u.test(line));
  if (treeLines.length < 2) return content;

  lines.splice(blockStart, 0, "```text");
  return lines.join("\n");
}
