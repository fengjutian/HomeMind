const fs = require('fs');
const { execSync } = require('child_process');

const files = [
  'dashboard/src/App.tsx',
  'dashboard/src/layouts/Header.tsx',
  'dashboard/src/layouts/Sidebar.tsx',
  'dashboard/src/pages/Login/index.tsx',
  'dashboard/src/pages/Setup/index.tsx',
  'dashboard/src/layouts/sidebarNav.tsx',
];

function gitShow(ref, file) {
  return execSync(`git show ${ref}:${file}`, { encoding: 'buffer' }).toString('utf8');
}

for (const f of files) {
  const U = gitShow('upstream/develop', f).split(/\r?\n/);
  const B = gitShow('588d35ad', f).split(/\r?\n/);
  const upSet = new Set(U.map((l) => l.trim()).filter(Boolean));
  const bSet = new Set(B.map((l) => l.trim()).filter(Boolean));
  const onlyB = B.filter((l) => l.trim() && !upSet.has(l.trim()));
  const onlyU = U.filter((l) => l.trim() && !bSet.has(l.trim()));
  console.log('=== ' + f.replace('dashboard/src/', '') + ' ===');
  console.log('  lines up/before:', U.length, B.length);
  console.log('  local-only:', onlyB.length, ' upstream-only:', onlyU.length);
  onlyB.slice(0, 8).forEach((l) => console.log('    CUR ' + l.trim().slice(0, 80)));
}
