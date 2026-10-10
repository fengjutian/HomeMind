import { lazy, Suspense, type CSSProperties } from "react";

const MonacoEditor = lazy(() => import("@monaco-editor/react"));

const LINE_HEIGHT = 20;
const VERTICAL_PADDING = 20;
const MIN_HEIGHT = 60;
const MAX_HEIGHT = 520;

const plainCodeStyle: CSSProperties = {
  margin: 0,
  borderRadius: "0 0 8px 8px",
  fontSize: 13,
  lineHeight: `${LINE_HEIGHT}px`,
  padding: "10px 16px",
  overflow: "auto",
};

export function monacoLanguageFor(language: string): string {
  switch (language.toLowerCase()) {
    case "vue":
      return "html";
    case "tsx":
      return "typescript";
    case "jsx":
      return "javascript";
    case "bash":
    case "sh":
    case "zsh":
      return "shell";
    case "docker":
      return "dockerfile";
    case "md":
      return "markdown";
    case "yml":
      return "yaml";
    case "text":
    case "txt":
      return "plaintext";
    default:
      return language.toLowerCase();
  }
}

export function codeEditorHeight(code: string): number {
  const lines = code.split("\n").length;
  return Math.min(
    MAX_HEIGHT,
    Math.max(MIN_HEIGHT, lines * LINE_HEIGHT + VERTICAL_PADDING),
  );
}

interface HighlightedCodeProps {
  language: string;
  code: string;
  isDark: boolean;
  /** Keep streaming output lightweight; mount Monaco only after completion. */
  plain?: boolean;
}

function PlainCode({ code }: { code: string }) {
  return (
    <pre style={plainCodeStyle}>
      <code>{code}</code>
    </pre>
  );
}

export function HighlightedCode({
  language,
  code,
  isDark,
  plain = false,
}: HighlightedCodeProps) {
  if (plain) return <PlainCode code={code} />;

  return (
    <Suspense fallback={<PlainCode code={code} />}>
      <MonacoEditor
        height={codeEditorHeight(code)}
        language={monacoLanguageFor(language)}
        theme={isDark ? "vs-dark" : "light"}
        value={code}
        options={{
          readOnly: true,
          domReadOnly: true,
          minimap: { enabled: false },
          lineNumbers: "on",
          lineNumbersMinChars: 3,
          glyphMargin: false,
          folding: false,
          lineDecorationsWidth: 8,
          overviewRulerLanes: 0,
          overviewRulerBorder: false,
          hideCursorInOverviewRuler: true,
          scrollBeyondLastLine: false,
          automaticLayout: true,
          wordWrap: "off",
          renderLineHighlight: "none",
          renderWhitespace: "selection",
          selectionHighlight: false,
          occurrencesHighlight: "off",
          contextmenu: false,
          links: false,
          fontSize: 13,
          lineHeight: LINE_HEIGHT,
          padding: { top: 10, bottom: 10 },
          scrollbar: {
            verticalScrollbarSize: 8,
            horizontalScrollbarSize: 8,
            alwaysConsumeMouseWheel: false,
          },
        }}
      />
    </Suspense>
  );
}
