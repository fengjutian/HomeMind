const fs = require('fs');

const up = fs.readFileSync(process.env.TEMP + '/cipb_up.tsx', 'utf8').split(/\r?\n/);
const be = fs.readFileSync(process.env.TEMP + '/cipb_be.tsx', 'utf8').split(/\r?\n/);
const upSet = new Set(up.map((l) => l.trim()).filter(Boolean));
const beSet = new Set(be.map((l) => l.trim()).filter(Boolean));
const onlyBe = be.filter((l) => l.trim() && !upSet.has(l.trim()));
const onlyUp = up.filter((l) => l.trim() && !beSet.has(l.trim()));
console.log('local-only:', onlyBe.length);
onlyBe.slice(0, 20).forEach((l) => console.log('  ' + l.trim().slice(0, 85)));
console.log('upstream-only:', onlyUp.length);
onlyUp.slice(0, 20).forEach((l) => console.log('  UP ' + l.trim().slice(0, 85)));
