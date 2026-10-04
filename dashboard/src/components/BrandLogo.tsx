interface BrandLogoProps {
  compact?: boolean;
  size?: number;
}

export default function BrandLogo({
  compact = false,
  size = 36,
}: BrandLogoProps) {
  return (
    <span
      aria-label="HomeMind"
      style={{
        alignItems: "center",
        display: "inline-flex",
        flexShrink: 0,
        gap: compact ? 0 : Math.max(7, Math.round(size * 0.24)),
      }}
    >
      <img
        src="/pwa-192.png"
        alt=""
        style={{
          borderRadius: Math.round(size * 0.24),
          display: "block",
          height: size,
          width: size,
        }}
      />
      {!compact && (
        <span
          style={{
            color: "var(--fn-text-primary)",
            fontSize: Math.max(19, Math.round(size * 0.62)),
            fontWeight: 750,
            letterSpacing: "-0.04em",
            lineHeight: 1,
            whiteSpace: "nowrap",
          }}
        >
          HomeMind
        </span>
      )}
    </span>
  );
}
