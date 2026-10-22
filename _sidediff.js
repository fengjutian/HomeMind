const fs = require('fs');
const { execSync } = require('child_process');

const file = process.argv[2];
const from = parseInt(process.argv[3], 10);
const to = parseInt(process.argv[4], 10);

const up = execSync(`git show upstream/develop:${file}`, { encoding: 'buffer' })
  .toString('utf8')
  .split(/\r?\n/);
const cur = fs.readFileSync(file, 'utf8').split(/\r?\n/);

console.log('--- upstream ' + from + '-' + to + ' vs current ---');
const upSlice = up.slice(from - 1, to);
const curSlice = cur.slice(from - 1, to);
const max = Math.max(upSlice.length, curSlice.length);
for (let i = 0; i < max; i++) {
  const a = upSlice[i];
  const b = curSlice[i];
  const same = a === b ? '   ' : ' !!';
  console.log(`${same} UP:${(a === undefined ? '' : a).slice(0, 78)}`);
  if (a !== b) console.log(`    CU:${(b === undefined ? '' : b).slice(0, 78)}`);
}
