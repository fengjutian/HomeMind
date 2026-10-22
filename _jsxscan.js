const fs = require('fs');
const path = process.argv[2];
const t = fs.readFileSync(path, 'utf8');
const NL = String.fromCharCode(10);
const CR = String.fromCharCode(13);
const lines = t.split(new RegExp(CR + '?' + NL));

const VOID = new Set([
  'area','base','br','col','embed','hr','img','input','link','meta',
  'param','source','track','wbr',
]);

const stack = [];
const problems = [];
const re = new RegExp('</?([A-Za-z][A-Za-z0-9_.]*)((?:[^<>"\' ]|"[^"]*"|\'[^\']*\')*?)(/?)>', 'g');
let m;
while ((m = re.exec(t))) {
  const raw = m[0];
  const name = m[1];
  const selfClose = m[3] === '/';
  const isClose = raw.charAt(1) === '/';
  const line = t.slice(0, m.index).split(new RegExp(CR + '?' + NL)).length;
  if (VOID.has(name) || selfClose) continue;
  if (!isClose) {
    stack.push({ name, line });
  } else {
    if (stack.length === 0) {
      problems.push('L' + line + ': unexpected </' + name + '>');
      continue;
    }
    const top = stack[stack.length - 1];
    if (top.name !== name) {
      problems.push(
        'L' + line + ': </' + name + '> but <' + top.name + '> opened at L' + top.line + ' still open',
      );
      const names = stack.map((s) => s.name);
      const idx = names.lastIndexOf(name);
      if (idx >= 0) stack.length = idx;
      else stack.pop();
    } else {
      stack.pop();
    }
  }
}

console.log('=== unclosed at EOF ===');
stack.slice(-15).forEach((s) => console.log('  <' + s.name + '> opened L' + s.line));
console.log('=== mismatches (first 15) ===');
problems.slice(0, 15).forEach((p) => console.log('  ' + p));
console.log('total mismatches:', problems.length);
