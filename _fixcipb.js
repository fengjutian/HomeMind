const fs = require('fs');

const file = 'dashboard/src/pages/Chat/components/ChatInputPreviewBar.tsx';
const up = fs.readFileSync(process.env.TEMP + '/cipb_up.tsx', 'utf8').split(/\r?\n/);
const be = fs.readFileSync(process.env.TEMP + '/cipb_be.tsx', 'utf8').split(/\r?\n/);

// Extract the local upload-progress pieces from the local file.
const beUploadImport = be.find((l) => l.includes('import type { UploadProgress }'));

// formatRate helper: from "function formatRate" to its closing "}"
const frStart = be.findIndex((l) => l.includes('function formatRate'));
let frEnd = frStart;
{
  let depth = 0;
  for (let i = frStart; i < be.length; i++) {
    for (const ch of be[i]) {
      if (ch === '{') depth++;
      else if (ch === '}') depth--;
    }
    if (depth === 0 && be[i].trim().endsWith('}')) {
      frEnd = i;
      break;
    }
  }
}
const formatRate = be.slice(frStart, frEnd + 1);

const bePropIdx = be.findIndex((l) => l.includes('uploadProgress?: UploadProgress | null;'));
const beProp = be.slice(bePropIdx, bePropIdx + 1);
const bePropPrev = be[bePropIdx - 1];

const beDestrIdx = be.findIndex((l) => l.trim() === 'uploadProgress,');
const beDestr = be.slice(beDestrIdx, beDestrIdx + 1);
const beDestrPrev = be[beDestrIdx - 1];

// JSX block for the upload progress bar
const jsxIdx = be.findIndex((l) => l.includes('{uploadProgress && uploadProgress.totalBytes > 0 && ('));
const uploadJsx = [];
{
  let depth = 0;
  for (let i = jsxIdx; i < be.length; i++) {
    uploadJsx.push(be[i]);
    for (const ch of be[i]) {
      if (ch === '{') depth++;
      else if (ch === '}') depth--;
    }
    if (depth === 0 && be[i].trim().endsWith('}')) break;
  }
}

let out = up.slice();

// 1) import: UploadProgress type
{
  const idx = out.findIndex((l) => l.includes('from "lucide-react";'));
  out.splice(idx + 1, 0, beUploadImport);
}

// 2) formatRate helper: put it before the component that uses it.
{
  const anchor = out.findIndex((l) => l.includes('export interface ChatInputPreviewBarProps'));
  out.splice(anchor, 0, ...formatRate, '');
}

// 3) prop
{
  const anchor = out.findIndex((l) => l.includes('onKnowledgeBaseIdsChange?: (ids: string[]) => void;'));
  out.splice(anchor + 1, 0, ...beProp);
}

// 4) destructure
{
  const anchor = out.findIndex((l) => l.trim() === 'onModelChange,');
  out.splice(anchor + 1, 0, ...beDestr);
}

// 5) JSX block: put it right before the upload progress chip area (after the model chip)
{
  const anchor = out.findIndex((l) => l.includes('{showModelChip && ('));
  // find end of the showModelChip block
  let depth = 0;
  let end = anchor;
  for (let i = anchor; i < out.length; i++) {
    for (const ch of out[i]) {
      if (ch === '{') depth++;
      else if (ch === '}') depth--;
    }
    if (depth === 0 && out[i].trim().endsWith('}')) {
      end = i;
      break;
    }
  }
  out.splice(end + 1, 0, ...uploadJsx);
}

fs.writeFileSync(file, out.join('\n'), 'utf8');
const src = out.join('\n');
console.log('uploadProgress refs:', (src.match(/uploadProgress/g) || []).length);
console.log('selectedModel refs:', (src.match(/selectedModel/g) || []).length);
console.log('showModelChip refs:', (src.match(/showModelChip/g) || []).length);
console.log('formatRate refs:', (src.match(/formatRate/g) || []).length);
