const fs = require('fs');
const { execSync } = require('child_process');

const f = 'dashboard/src/api/modules/memoryDashboard.ts';
const up = execSync(`git show upstream/develop:${f}`, { encoding: 'buffer' })
  .toString('utf8')
  .split(/\r?\n/);
const cur = fs.readFileSync(f, 'utf8').split(/\r?\n/);

const upSet = new Set(up.map((l) => l.trim()).filter(Boolean));
const curSet = new Set(cur.map((l) => l.trim()).filter(Boolean));

const onlyCur = cur.filter((l) => l.trim() && !upSet.has(l.trim()));
const onlyUp = up.filter((l) => l.trim() && !curSet.has(l.trim()));

console.log('local-only:', onlyCur.length);
onlyCur.forEach((l) => console.log('  CUR ' + l.trim().slice(0, 90)));
console.log('upstream-only:', onlyUp.length);
onlyUp.slice(0, 40).forEach((l) => console.log('  UP  ' + l.trim().slice(0, 90)));
