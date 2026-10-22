const fs = require('fs');
const { execSync } = require('child_process');

const file = 'dashboard/src/pages/Chat/components/AssistantTurnView.tsx';
let up = execSync(`git show upstream/develop:${file}`, { encoding: 'buffer' }).toString('utf8');
const lines = up.split(/\r?\n/);
const out = [];

let typePropsAdded = 0;
let destructureAdded = 0;
let jsxPropAdded = 0;
let passThroughAdded = 0;

for (let i = 0; i < lines.length; i++) {
  const l = lines[i];

  // props interface entries: "  onRegenerate?: (messageId: string) => void;"
  if (l.trim() === 'onRegenerate?: (messageId: string) => void;') {
    out.push(l);
    out.push('  onRegenerateMermaid?: (code: string) => void;');
    typePropsAdded++;
    continue;
  }

  // destructured params: "  onRegenerate," (exact)
  if (l.trim() === 'onRegenerate,') {
    out.push(l);
    out.push('  onRegenerateMermaid,');
    destructureAdded++;
    continue;
  }

  // JSX prop: "onRegenerate={onRegenerate}"
  if (l.trim() === 'onRegenerate={onRegenerate}') {
    const indent = l.slice(0, l.length - l.trimStart().length);
    out.push(l);
    out.push(indent + 'onRegenerateMermaid={onRegenerateMermaid}');
    jsxPropAdded++;
    continue;
  }

  out.push(l);
}

up = out.join('\n');
fs.writeFileSync(file, up, 'utf8');
console.log(
  `typeProps=${typePropsAdded} destructure=${destructureAdded} jsxProp=${jsxPropAdded} passThrough=${passThroughAdded}`,
);
console.log('total onRegenerateMermaid:', (up.match(/onRegenerateMermaid/g) || []).length);
