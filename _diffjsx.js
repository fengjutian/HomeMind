const fs = require('fs');
const { execSync } = require('child_process');

function readGit(ref, file) {
  const out = execSync(`git show ${ref}:${file}`, { encoding: 'buffer' });
  return out.toString('utf8');
}

const file = process.argv[2];
const a = readGit('upstream/develop', file).split(/\r?\n/);
const b = fs.readFileSync(file, 'utf8').split(/\r?\n/);

console.log('upstream lines:', a.length, ' current lines:', b.length);

// Longest common subsequence diff (line level), coarse but enough to spot
// structural insertions.
const n = a.length;
const m = b.length;
// Use a simple LCS via DP but cap sizes to keep memory sane.
const MAX = 4000;
const useA = a.slice(0, MAX);
const useB = b.slice(0, MAX);
const dp = Array.from({ length: useA.length + 1 }, () => new Uint32Array(useB.length + 1));
for (let i = useA.length - 1; i >= 0; i--) {
  for (let j = useB.length - 1; j >= 0; j--) {
    dp[i][j] = useA[i] === useB[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
  }
}
let i = 0, j = 0;
const inserts = [];
while (i < useA.length && j < useB.length) {
  if (useA[i] === useB[j]) { i++; j++; continue; }
  if (dp[i + 1][j] >= dp[i][j + 1]) {
    // upstream line removed (not present in current)
    i++;
  } else {
    // current-only line
    const start = j;
    while (j < useB.length && dp[i][j + 1] >= dp[i][j]) j++;
    inserts.push({ upstreamLine: i + 1, currentStart: start + 1, currentEnd: j, count: j - start });
  }
}
console.log('=== current-only blocks (first 20) ===');
inserts.slice(0, 20).forEach((x) =>
  console.log(`  upstream@${x.upstreamLine}  current L${x.currentStart}-${x.currentEnd} (${x.count} lines)`),
);
