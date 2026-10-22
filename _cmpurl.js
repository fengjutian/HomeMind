const fs = require('fs');
const { execSync } = require('child_process');

const files = [
  'dashboard/src/layouts/Header.tsx',
  'dashboard/src/layouts/Sidebar.tsx',
  'dashboard/src/pages/Login/index.tsx',
  'dashboard/src/pages/Setup/index.tsx',
];

function gitShow(ref, file) {
  return execSync(`git show ${ref}:${file}`, { encoding: 'buffer' }).toString('utf8');
}

for (const f of files) {
  const up = gitShow('upstream/develop', f);
  const cur = fs.readFileSync(f, 'utf8');
  console.log('=== ' + f.replace('dashboard/src/', '') + ' ===');
  const upLines = up.split(/\r?\n/);
  const curLines = cur.split(/\r?\n/);
  upLines.forEach((l, i) => {
    if (l.includes('publicUrl(')) console.log('  UP ' + (i + 1) + ': ' + l.trim().slice(0, 95));
  });
  curLines.forEach((l, i) => {
    if (l.includes('publicUrl')) console.log('  CU ' + (i + 1) + ': ' + l.trim().slice(0, 95));
  });
}
