import {
  memo,
  useEffect,
  useRef,
  useState,
  useCallback,
  useMemo,
  type CSSProperties,
  type ReactNode,
  type AnchorHTMLAttributes,
  type TableHTMLAttributes,
  type ImgHTMLAttributes,
} from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { useTranslation } from "react-i18next";
import { Maximize2, Minimize2, Scan, ZoomIn, ZoomOut } from "lucide-react";
import {
  AuthFileDownloadLink,
  isAuthDownloadHref,
} from "../AuthFileDownloadLink";
import { isShellLanguage } from "../../utils/shellCodeBlock";
import { copyText } from "../../utils/copyText";
import { loadMermaid } from "./mermaidLoader";
import { HighlightedCode } from "./syntaxHighlight";
import { useMathPlugins } from "./mathPlugins";
import { stabilizeStreamingMarkdown } from "./stabilizeStreamingMarkdown";
import styles from "./index.module.less";

/* ---- Mermaid block component ---- */
function MermaidBlock({
  code,
  onRegenerate,
}: {
  code: string;
  onRegenerate?: (code: string) => void;
}) {
  const { t } = useTranslation();
  const blockRef = useRef<HTMLDivElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const [svg, setSvg] = useState<string>("");
  const [aspectRatio, setAspectRatio] = useState<string>();
  const [error, setError] = useState(false);
  const [zoom, setZoom] = useState(1);
  const [isFullscreen, setIsFullscreen] = useState(false);

  useEffect(() => {
    const handleFullscreenChange = () => {
      setIsFullscreen(document.fullscreenElement === blockRef.current);
    };
    document.addEventListener("fullscreenchange", handleFullscreenChange);
    return () =>
      document.removeEventListener("fullscreenchange", handleFullscreenChange);
  }, []);

  const selectDiagram = useCallback(() => {
    const container = containerRef.current;
    const selection = window.getSelection();
    if (!container || !selection) return;
    const range = document.createRange();
    range.selectNodeContents(container);
    selection.removeAllRanges();
    selection.addRange(range);
  }, []);

  const toggleFullscreen = useCallback(() => {
    if (document.fullscreenElement === blockRef.current) {
      void document.exitFullscreen().catch(() => undefined);
      return;
    }
    void blockRef.current?.requestFullscreen().catch(() => undefined);
  }, []);

  useEffect(() => {
    let cancelled = false;
    const trimmed = code.trim();
    if (!trimmed) return;
    setSvg("");
    setAspectRatio(undefined);

    const id = `mermaid-${Date.now()}-${Math.random()
      .toString(36)
      .slice(2, 8)}`;

    const cleanup = () => {
      // mermaid.render() inserts elements into the DOM on failure;
      // aggressively remove any leftover nodes it may have created.
      document.getElementById(id)?.remove();
      document.getElementById("d" + id)?.remove();
      // Mermaid v11 may create <style> or <svg> with the generated id
      document.querySelectorAll(`[id^="${id}"]`).forEach((el) => {
        if (!containerRef.current?.contains(el)) el.remove();
      });
    };

    (async () => {
      try {
        const mermaid = await loadMermaid();

        // Validate syntax first without rendering to DOM — this avoids
        // the visible error elements that mermaid.render() creates on failure.
        await mermaid.parse(trimmed);

        // Syntax is valid — now render. We need a temporary container in
        // the DOM for mermaid to render into.
        const tempContainer = document.createElement("div");
        tempContainer.id = id;
        tempContainer.style.position = "absolute";
        tempContainer.style.left = "-9999px";
        tempContainer.style.top = "-9999px";
        tempContainer.style.visibility = "hidden";
        document.body.appendChild(tempContainer);

        const { svg: rendered } = await mermaid.render(id, trimmed);
        if (!cancelled) {
          const document = new DOMParser().parseFromString(
            rendered,
            "image/svg+xml",
          );
          const viewBox = document.documentElement
            .getAttribute("viewBox")
            ?.trim()
            .split(/[ ,]+/)
            .map(Number);
          const width = viewBox?.[2];
          const height = viewBox?.[3];
          setSvg(rendered);
          setAspectRatio(
            width && height && width > 0 && height > 0
              ? `${width} / ${height}`
              : undefined,
          );
          setError(false);
        }
      } catch {
        if (!cancelled) {
          setError(true);
          setSvg("");
        }
      } finally {
        cleanup();
      }
    })();

    return () => {
      cancelled = true;
      cleanup();
    };
  }, [code]);

  if (error) {
    return (
      <div className={styles.mermaidError}>
        <div className={styles.mermaidErrorHeader}>
          <span>{t("chat.mermaidRenderFailed")}</span>
          {onRegenerate ? (
            <button
              type="button"
              className={styles.mermaidRegenerateBtn}
              onClick={() => onRegenerate(code)}
            >
              {t("chat.regenerateMermaid")}
            </button>
          ) : null}
        </div>
        <pre className={styles.codeBlock}>
          <code>{code}</code>
        </pre>
      </div>
    );
  }

  if (!svg) {
    return null;
  }

  return (
    <div ref={blockRef} className={styles.mermaidBlock}>
      <div className={styles.mermaidToolbar} role="toolbar">
        <button
          type="button"
          aria-label={t("chat.mermaidSelectAll")}
          title={t("chat.mermaidSelectAll")}
          onClick={selectDiagram}
        >
          <Scan size={16} />
        </button>
        <button
          type="button"
          aria-label={t("chat.mermaidZoomOut")}
          title={t("chat.mermaidZoomOut")}
          disabled={zoom <= 0.5}
          onClick={() => setZoom((value) => Math.max(0.5, value - 0.25))}
        >
          <ZoomOut size={16} />
        </button>
        <span className={styles.mermaidZoomLabel}>
          {Math.round(zoom * 100)}%
        </span>
        <button
          type="button"
          aria-label={t("chat.mermaidZoomIn")}
          title={t("chat.mermaidZoomIn")}
          disabled={zoom >= 3}
          onClick={() => setZoom((value) => Math.min(3, value + 0.25))}
        >
          <ZoomIn size={16} />
        </button>
        <button
          type="button"
          aria-label={
            isFullscreen
              ? t("chat.mermaidExitFullscreen")
              : t("chat.mermaidFullscreen")
          }
          title={
            isFullscreen
              ? t("chat.mermaidExitFullscreen")
              : t("chat.mermaidFullscreen")
          }
          onClick={toggleFullscreen}
        >
          {isFullscreen ? <Minimize2 size={16} /> : <Maximize2 size={16} />}
        </button>
      </div>
      <div className={styles.mermaidViewport}>
        <div
          ref={containerRef}
          className={styles.mermaidCanvas}
          style={{ width: `${zoom * 100}%`, aspectRatio }}
          dangerouslySetInnerHTML={{ __html: svg }}
        />
      </div>
    </div>
  );
}

/**
 * Convert a file:// URI or absolute path that lives inside the agent workspace
 * to a browser-accessible URL via the /api/workspace/media endpoint.
 *
 * Examples:
 *   file:///home/wally/.octop/workspace/foo.png  → /api/workspace/media?path=foo.png
 *   /home/wally/.octop/workspace/sub/bar.jpg     → /api/workspace/media?path=sub/bar.jpg
 *
 * Paths that are already HTTP(S) or data URIs are returned unchanged.
 * Paths outside the workspace are returned unchanged (will fail to load,
 * which is the correct behaviour — we don't want to expose arbitrary files).
 */
function resolveImageSrc(src: string): string {
  if (!src) return src;
  // Already a web URL or data URI – keep as-is
  if (
    /^https?:\/\//i.test(src) ||
    src.startsWith("data:") ||
    src.startsWith("/api/")
  ) {
    return src;
  }

  let filePath = src;
  // Decode file:// URI → absolute path
  if (src.startsWith("file://")) {
    try {
      filePath = decodeURIComponent(new URL(src).pathname);
    } catch {
      filePath = src.replace(/^file:\/\//, "");
    }
  }

  // Match workspace path: ends in /.octop/workspace/<rest>
  const workspaceMatch = filePath.match(/[/\\]\.octop[/\\]workspace[/\\](.*)/);
  if (workspaceMatch) {
    const relPath = workspaceMatch[1].replace(/\\/g, "/");
    return `/api/workspace/media?path=${encodeURIComponent(relPath)}`;
  }

  return src;
}

/** Markdown image with automatic file:// → /api/workspace/media conversion and error fallback. */
function MarkdownImage({
  src,
  alt,
  ...props
}: { src?: string; alt?: string } & ImgHTMLAttributes<HTMLImageElement>) {
  const resolved = resolveImageSrc(src || "");
  const [imgSrc, setImgSrc] = useState(resolved);
  const failed = useRef(false);

  const handleError = useCallback(() => {
    if (failed.current) return;
    failed.current = true;
    // If the resolved URL also fails (e.g. image not in workspace), show alt text gracefully
    setImgSrc("");
  }, []);

  if (!imgSrc) {
    // Render a subtle placeholder instead of a broken image icon
    return (
      <span style={{ color: "var(--fn-text-tertiary, #999)", fontSize: 13 }}>
        [{alt || "image"}]
      </span>
    );
  }

  return (
    <img
      src={imgSrc}
      alt={alt}
      onError={handleError}
      style={{ maxWidth: "100%", borderRadius: 8 }}
      {...props}
    />
  );
}

function ShellRunButton({
  code,
  onRun,
  disabled,
  disabledTitle,
  label,
}: {
  code: string;
  onRun?: (code: string) => void;
  disabled?: boolean;
  disabledTitle?: string;
  label: string;
}) {
  if (!onRun) return null;
  return (
    <button
      type="button"
      className={styles.codeRunBtn}
      onClick={() => onRun(code)}
      disabled={disabled}
      title={disabled ? disabledTitle : label}
    >
      {label}
    </button>
  );
}

/* ---- Copy button for code blocks ---- */
function CodeCopyButton({ code }: { code: string }) {
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    const ok = await copyText(code);
    if (!ok) return;
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <button className={styles.codeCopyBtn} onClick={handleCopy} title="Copy">
      {copied ? "✓" : "⎘"}
    </button>
  );
}

/* ---- Main Markdown component ---- */
export interface MarkdownProps {
  content: string;
  className?: string;
  style?: CSSProperties;
  /** When true, mermaid blocks render as source code to avoid parse errors on incomplete content */
  isStreaming?: boolean;
  /** Run shell code blocks in the active terminal */
  onRunShellCommand?: (code: string) => void;
  shellCommandDisabled?: boolean;
  shellCommandDisabledTitle?: string;
  shellCommandLabel?: string;
  /** Ask the assistant to regenerate only a Mermaid block that failed to parse. */
  onRegenerateMermaid?: (code: string) => void;
}

const Markdown = memo(function Markdown({
  content,
  className,
  style,
  isStreaming,
  onRunShellCommand,
  shellCommandDisabled,
  shellCommandDisabledTitle,
  shellCommandLabel,
  onRegenerateMermaid,
}: MarkdownProps) {
  const { t } = useTranslation();
  const runLabel = shellCommandLabel ?? t("terminal.ai.execBtn");
  const renderContent = useMemo(
    () => (isStreaming ? stabilizeStreamingMarkdown(content) : content),
    [content, isStreaming],
  );
  const { remarkPlugins: mathRemark, rehypePlugins: mathRehype } =
    useMathPlugins(renderContent);
  const remarkPlugins = useMemo(() => [remarkGfm, ...mathRemark], [mathRemark]);
  const rehypePlugins = useMemo(() => [...mathRehype], [mathRehype]);
  const rendererOptionsRef = useRef({
    isStreaming,
    onRegenerateMermaid,
    onRunShellCommand,
    shellCommandDisabled,
    shellCommandDisabledTitle,
    runLabel,
  });
  rendererOptionsRef.current = {
    isStreaming,
    onRegenerateMermaid,
    onRunShellCommand,
    shellCommandDisabled,
    shellCommandDisabledTitle,
    runLabel,
  };

  const markdownComponents = useMemo(
    () => ({
      code(
        codeProps: React.ComponentPropsWithoutRef<"code"> & {
          className?: string;
        },
      ) {
        const {
          isStreaming,
          onRegenerateMermaid,
          onRunShellCommand,
          shellCommandDisabled,
          shellCommandDisabledTitle,
          runLabel,
        } = rendererOptionsRef.current;
        const { className: codeClassName, children, ...props } = codeProps;
        const match = /language-(\w+)/.exec(codeClassName || "");
        const codeString = String(children).replace(/\n$/, "");

        // Mermaid blocks — show source code while streaming to avoid parse errors
        if (match && match[1] === "mermaid") {
          if (isStreaming) {
            return (
              <div className={styles.codeBlockWrapper}>
                <div className={styles.codeBlockHeader}>
                  <span className={styles.codeBlockLang}>mermaid</span>
                  <CodeCopyButton code={codeString} />
                </div>
                <HighlightedCode language="text" code={codeString} plain />
              </div>
            );
          }
          return (
            <MermaidBlock
              code={codeString}
              onRegenerate={onRegenerateMermaid}
            />
          );
        }

        // Fenced code blocks with language
        if (match) {
          const shellLang = isShellLanguage(match[1]);
          return (
            <div className={styles.codeBlockWrapper}>
              <div className={styles.codeBlockHeader}>
                <span className={styles.codeBlockLang}>{match[1]}</span>
                <div className={styles.codeBlockActions}>
                  {shellLang && (
                    <ShellRunButton
                      code={codeString}
                      onRun={onRunShellCommand}
                      disabled={shellCommandDisabled}
                      disabledTitle={shellCommandDisabledTitle}
                      label={runLabel}
                    />
                  )}
                  <CodeCopyButton code={codeString} />
                </div>
              </div>
              <HighlightedCode
                language={match[1]}
                code={codeString}
                plain={!!isStreaming}
              />
            </div>
          );
        }

        // Fenced code blocks without language
        if (
          "node" in props &&
          (props as Record<string, unknown>).node &&
          codeString.includes("\n")
        ) {
          return (
            <div className={styles.codeBlockWrapper}>
              <div className={styles.codeBlockHeader}>
                <span className={styles.codeBlockLang}>text</span>
                <CodeCopyButton code={codeString} />
              </div>
              <HighlightedCode
                language="text"
                code={codeString}
                plain={!!isStreaming}
              />
            </div>
          );
        }

        // Inline code
        return (
          <code className={styles.inlineCode} {...props}>
            {children}
          </code>
        );
      },

      // Table styling
      table({
        children,
        ...props
      }: {
        children?: ReactNode;
      } & TableHTMLAttributes<HTMLTableElement>) {
        return (
          <div className={styles.tableWrapper}>
            <table {...props}>{children}</table>
          </div>
        );
      },

      // Auth-required workspace downloads use blob fetch; other links open
      // in a new tab (JWT lives in Authorization, not the browser navigation).
      a({
        children,
        href,
        ...props
      }: {
        children?: ReactNode;
        href?: string;
      } & AnchorHTMLAttributes<HTMLAnchorElement>) {
        if (isAuthDownloadHref(href)) {
          return (
            <AuthFileDownloadLink url={href!} style={{ color: "inherit" }}>
              {children}
            </AuthFileDownloadLink>
          );
        }
        return (
          <a href={href} target="_blank" rel="noreferrer" {...props}>
            {children}
          </a>
        );
      },

      // Images: convert file:// paths to accessible HTTP URLs
      img({
        src,
        alt,
        ...props
      }: {
        src?: string;
        alt?: string;
      } & ImgHTMLAttributes<HTMLImageElement>) {
        return <MarkdownImage src={src} alt={alt} {...props} />;
      },
    }),
    [],
  );

  return (
    <div
      className={`${styles.markdownBody} ${
        isStreaming ? styles.markdownStreaming : ""
      } ${className || ""}`}
      style={style}
    >
      <ReactMarkdown
        remarkPlugins={remarkPlugins}
        rehypePlugins={rehypePlugins}
        components={markdownComponents}
      >
        {renderContent}
      </ReactMarkdown>
    </div>
  );
});

export default Markdown;
