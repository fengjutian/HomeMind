const fs = require('fs');
const { execSync } = require('child_process');

const files = [
  'dashboard/src/pages/SkillPackages/index.tsx',
  'dashboard/src/pages/Chat/components/AssistantTurnView.tsx',
  'dashboard/src/pages/Chat/components/ChatInputActionsRow.tsx',
];

function gitShow(ref, file) {
  return execSync(`git show ${ref}:${file}`, { encoding: 'buffer' }).toString('utf8');
}

for (const f of files) {
  const U = gitShow('upstream/develop', f).split(/\r?\n/);
  const B = gitShow('588d35ad', f).split(/\r?\n/);
  const bSet = new Set(B.map((l) => l.trim()).filter(Boolean));
  const uSet = new Set(U.map((l) => l.trim()).filter(Boolean));
  const onlyU = U.filter((l) => l.trim() && !bSet.has(l.trim()));
  const onlyB = B.filter((l) => l.trim() && !uSet.has(l.trim()));
  console.log('=== ' + f.replace('dashboard/src/pages/', '') + ' ===');
  console.log('  upstream lines:', U.length, ' before lines:', B.length);
  console.log('  upstream-only lines:', onlyU.length, ' local-only lines:', onlyB.length);
  console.log('  --- local-only sample ---');
  onlyB.slice(0, 12).forEach((l) => console.log('    ' + l.trim().slice(0, 90)));
}
