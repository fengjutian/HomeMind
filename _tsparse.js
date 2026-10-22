const ts = require('D:/github/HomeMind/dashboard/node_modules/typescript');
const fs = require('fs');

const file = process.argv[2];
const text = fs.readFileSync(file, 'utf8');
const sf = ts.createSourceFile(file, text, ts.ScriptTarget.ESNext, true, ts.ScriptKind.TSX);

const diags = [];
sf.parseDiagnostics.forEach((d) => {
  const { line, character } = sf.getLineAndCharacterOfPosition(d.start);
  diags.push({
    line: line + 1,
    col: character + 1,
    msg: ts.flattenDiagnosticMessageText(d.messageText, ' '),
  });
});

diags.sort((a, b) => a.line - b.line);
console.log('parse diagnostics:', diags.length);
diags.slice(0, 20).forEach((d) => console.log(`  L${d.line}:${d.col}  ${d.msg}`));

// Show the source lines around the first diagnostic.
if (diags.length) {
  const lines = text.split(/\r?\n/);
  const first = diags[0].line;
  console.log('--- context around L' + first + ' ---');
  for (let i = Math.max(0, first - 12); i < Math.min(lines.length, first + 4); i++) {
    console.log((i + 1) + ': ' + lines[i]);
  }
}
