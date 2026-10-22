const fs = require('fs');
const { execSync } = require('child_process');

const file = 'dashboard/src/pages/Chat/components/ChatInputActionsRow.tsx';
let up = execSync(`git show upstream/develop:${file}`, { encoding: 'buffer' }).toString('utf8');
const lines = up.split(/\r?\n/);
const out = [];

// 1) state: add modelPopoverOpen next to uploadOpen
for (const l of lines) {
  out.push(l);
  if (l.trim() === 'const [uploadOpen, setUploadOpen] = useState(false);') {
    out.push('  const [modelPopoverOpen, setModelPopoverOpen] = useState(false);');
  }
}

// 2) closeModelMenus helper next to closePlusMenu definition
const withHelper = [];
for (const l of out) {
  withHelper.push(l);
  if (l.trim() === 'const closePlusMenu = () => {') {
    // helper goes after the whole closePlusMenu body; handled below by marker
  }
}

// Find the end of closePlusMenu body and insert closeModelMenus after it.
const final = [];
let i = 0;
while (i < withHelper.length) {
  final.push(withHelper[i]);
  if (withHelper[i].trim() === 'const closePlusMenu = () => {') {
    // copy until the closing "};" at the same indent
    let j = i + 1;
    const indent = withHelper[i].match(/^\s*/)[0];
    while (j < withHelper.length && withHelper[j] !== indent + '};') {
      final.push(withHelper[j]);
      j++;
    }
    final.push(withHelper[j]); // the "};"
    final.push('');
    final.push('  const closeModelMenus = () => {');
    final.push('    setModelPopoverOpen(false);');
    final.push('    closePlusMenu();');
    final.push('  };');
    i = j + 1;
    continue;
  }
  i++;
}

let src = final.join('\n');

// 3) use closeModelMenus where the local fork used it (model menu entries)
src = src.replace(
  /onClick=\{\(\) => \{\n(\s*)onModelChange\?\.\(null\);\n\s*closePlusMenu\(\);/g,
  (m, ind) => m.replace('closePlusMenu();', 'closeModelMenus();'),
);
src = src.replace(
  /onClick=\{\(\) => \{\n(\s*)onModelChange\?\.\(active \? null : value\);\n\s*closePlusMenu\(\);/g,
  (m) => m.replace('closePlusMenu();', 'closeModelMenus();'),
);

// 4) the independent model picker popover before the closing of renderSecondaryActions
const pickerBlock = [
  '        {showModelPicker ? (',
  '          <Popover',
  '            trigger="click"',
  '            placement="topLeft"',
  '            arrow={false}',
  '            open={modelPopoverOpen}',
  '            onOpenChange={setModelPopoverOpen}',
  '            overlayClassName={styles.skillPickerPopover}',
  '            content={modelMenu}',
  '          >',
  '            <button',
  '              type="button"',
  '              className={styles.composerModelPicker}',
  '              data-testid="composer-model-picker"',
  '              aria-label={t("chat.selectModel", "Model")}',
  '            >',
  '              <Cpu size={14} strokeWidth={2} aria-hidden />',
  '              <span>{selectedModelTriggerLabel}</span>',
  '              <ChevronRight size={14} aria-hidden />',
  '            </button>',
  '          </Popover>',
  '        ) : null}',
];

// Insert before the "      </>\n    );\n  };" that closes renderSecondaryActions.
const anchor = '      </>\n    );\n  };\n';
const idx = src.indexOf(anchor);
if (idx < 0) {
  console.log('anchor not found');
  process.exit(1);
}
src = src.slice(0, idx) + pickerBlock.join('\n') + '\n' + src.slice(idx);

fs.writeFileSync(file, src, 'utf8');
console.log('closeModelMenus refs:', (src.match(/closeModelMenus/g) || []).length);
console.log('modelPopoverOpen refs:', (src.match(/modelPopoverOpen/g) || []).length);
