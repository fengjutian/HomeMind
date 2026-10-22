const fs = require('fs');
const file = process.argv[2];
const src = fs.readFileSync(file, 'utf8');
const lines = src.split(/\r?\n/);

// Track JSX-ish brace/paren balance outside of strings is hard; instead walk
// the file tracking every '{' '(' '<' as tokens with a simple lexer that skips
// strings, template literals, comments, and regex-ish content.
let i = 0;
const stack = [];
let line = 1;
const problems = [];
let inJsxText = false;

function isIdentChar(c) {
  return /[A-Za-z0-9_$]/.test(c);
}

while (i < src.length) {
  const c = src[i];
  if (c === '\n') { line++; i++; continue; }
  // comments
  if (c === '/' && src[i + 1] === '/') { while (i < src.length && src[i] !== '\n') i++; continue; }
  if (c === '/' && src[i + 1] === '*') { i += 2; while (i < src.length && !(src[i] === '*' && src[i + 1] === '/')) { if (src[i] === '\n') line++; i++; } i += 2; continue; }
  // strings
  if (c === '"' || c === "'") {
    const q = c; i++;
    while (i < src.length && src[i] !== q) { if (src[i] === '\\') i++; if (src[i] === '\n') line++; i++; }
    i++; continue;
  }
  // template literal
  if (c === '`') {
    i++;
    let depth = 0;
    while (i < src.length) {
      if (src[i] === '\\') { i += 2; continue; }
      if (src[i] === '$' && src[i + 1] === '{') { depth++; i += 2; continue; }
      if (src[i] === '}' && depth > 0) { depth--; i++; continue; }
      if (src[i] === '`' && depth === 0) break;
      if (src[i] === '\n') line++;
      i++;
    }
    i++; continue;
  }
  if (c === '(' || c === '{' || c === '[') { stack.push({ c, line }); i++; continue; }
  if (c === ')' || c === '}' || c === ']') {
    const want = c === ')' ? '(' : c === '}' ? '{' : '[';
    const top = stack[stack.length - 1];
    if (!top) { problems.push(`L${line}: unexpected ${c}`); i++; continue; }
    if (top.c !== want) {
      problems.push(`L${line}: ${c} but ${top.c} opened at L${top.line} still open`);
      // try to recover
      const idx = stack.map((s) => s.c).lastIndexOf(want);
      if (idx >= 0) stack.length = idx; else stack.pop();
    } else {
      stack.pop();
    }
    i++; continue;
  }
  i++;
}

console.log('=== unmatched closers (first 12) ===');
problems.slice(0, 12).forEach((p) => console.log('  ' + p));
console.log('=== still open at EOF (last 12) ===');
stack.slice(-12).forEach((s) => console.log(`  ${s.c} opened L${s.line}`));
console.log('problems:', problems.length, ' open:', stack.length);
