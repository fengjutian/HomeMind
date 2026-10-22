const fs = require('fs');
const { execSync } = require('child_process');

const file = 'dashboard/src/pages/KnowledgeBases/index.tsx';
const up = execSync(`git show upstream/develop:${file}`, { encoding: 'buffer' })
  .toString('utf8')
  .split(/\r?\n/);
const cur = fs.readFileSync(file, 'utf8').split(/\r?\n/);

// Upstream block: gridToolbar div open .. the gridCount span .. gridToolbarRight open
// Upstream lines 2309..2373 (1-based):
//   2309 <div
//   2310   className={...gridToolbar ... docsToolbar}
//   2311 >
//   2312 <div className={styles.toolbarStart}>  ... 2372 </div>
//   2373 <div className={skillStyles.gridToolbarRight}>
// Replace the current file's region that nests gridToolbar inside gridToolbar.

const upStart = 2308; // 0-based index of upstream line 2309
const upEnd = 2372; // 0-based index of upstream line 2373 (exclusive-ish)

// Locate in current file: the gridToolbar open whose next line is gridCount.
let curStart = -1;
for (let i = 0; i < cur.length - 3; i++) {
  if (
    cur[i].includes('styles.docsToolbar') &&
    cur[i + 1] !== undefined &&
    cur[i + 1].trim() === '>' &&
    cur[i + 2] !== undefined &&
    cur[i + 2].includes('gridCount')
  ) {
    curStart = i - 1; // include the "<div" line
    break;
  }
}
if (curStart < 0) {
  console.log('current: gridToolbar+gridCount not found');
  process.exit(1);
}
console.log('current gridToolbar open at L' + (curStart + 1));

// Find where the current gridCount span ends (its closing </span>) so we can
// replace [curStart .. end of gridCount span] with the upstream toolbarStart block.
let spanEnd = -1;
for (let i = curStart; i < cur.length; i++) {
  if (cur[i].trim() === '</span>' && cur[i - 1] !== undefined && cur[i - 1].includes('gridCount')) {
    spanEnd = i;
    break;
  }
  if (cur[i].includes('</span>') && cur.slice(curStart, i).join('\n').includes('gridCount')) {
    // first </span> after the gridCount opening
    spanEnd = i;
    break;
  }
}
if (spanEnd < 0) {
  console.log('current: gridCount span end not found');
  process.exit(1);
}
console.log('current gridCount span ends at L' + (spanEnd + 1));

const replacement = up.slice(upStart, upEnd); // upstream lines 2309..2373
const next = cur.slice(0, curStart).concat(replacement, cur.slice(spanEnd + 1));
fs.writeFileSync(file, next.join('\n'), 'utf8');
console.log('replaced ' + (spanEnd + 1 - curStart) + ' current lines with ' + replacement.length + ' upstream lines');
