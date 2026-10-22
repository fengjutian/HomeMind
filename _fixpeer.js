const fs = require('fs');
const { execSync } = require('child_process');

const f = 'dashboard/src/pages/Settings/AdvancedSettings/bridgePeerLabel.ts';
const up = execSync(`git show upstream/develop:${f}`, { encoding: 'buffer' })
  .toString('utf8')
  .split(/\r?\n/);
const cur = fs.readFileSync(f, 'utf8').split(/\r?\n/);

const i = up.findIndex((l) => l.includes('export function peerHostLabel'));
let j = i + 1;
while (j < up.length && !up[j].startsWith('export function')) j++;
const block = up.slice(i, j);

const anchor = cur.findIndex((l) => l.includes('export function inboundPeerAddress'));
const out = cur.slice(0, anchor).concat(block, cur.slice(anchor));

fs.writeFileSync(f, out.join('\n'), 'utf8');
console.log('peerHostLabel refs:', (out.join('\n').match(/peerHostLabel/g) || []).length);
