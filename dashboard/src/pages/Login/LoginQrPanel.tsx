import { useEffect, useState } from "react";
import { QRCodeSVG } from "qrcode.react";
import { useTranslation } from "react-i18next";
import { authApi } from "../../api/modules/auth";
import styles from "./LoginQrPanel.module.less";

const LOOPBACK_HOSTS = new Set(["localhost", "127.0.0.1", "::1", "[::1]"]);

/**
 * Build the URL a phone should open when scanning this QR code.
 *
 * The QR always points at the **vite dev server**, injected at build time as
 * ``DEV_SERVER_PORT``. It deliberately ignores the page's own origin: the
 * login page is frequently opened through the API on a different port, and
 * the phone needs the dev server to render the current source.
 *
 * The host is the LAN address detected server-side, because ``localhost`` on
 * a phone resolves to the phone itself. Falls back to the current origin when
 * no LAN address could be detected.
 */
export function buildScanUrl(lanHost: string | null, loc = window.location): string {
  const onLoopback = LOOPBACK_HOSTS.has(loc.hostname);
  if (!onLoopback && !lanHost) return loc.origin;
  const host = lanHost ?? loc.hostname;
  return `${loc.protocol}//${host}:${DEV_SERVER_PORT}`;
}

/**
 * QR code pointing at the frontend dev server, so a phone on the same network
 * can open the login page by scanning instead of typing an IP.
 */
export default function LoginQrPanel() {
  const { t } = useTranslation();
  const [url, setUrl] = useState<string | null>(null);
  const [resolved, setResolved] = useState(false);

  useEffect(() => {
    let cancelled = false;
    authApi
      .getServerAddress()
      .then((info) => {
        if (cancelled) return;
        setUrl(buildScanUrl(info.is_lan ? info.host : null));
        setResolved(info.is_lan);
      })
      .catch(() => {
        if (cancelled) return;
        // Backend unreachable or predating this endpoint — show where we are.
        setUrl(buildScanUrl(null));
        setResolved(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (!url) return null;

  return (
    <div className={styles.panel} data-testid="login-qr-panel">
      <p className={styles.title}>{t("login.qrTitle")}</p>
      <div className={styles.frame}>
        <QRCodeSVG value={url} size={168} />
      </div>
      <span className={styles.url}>{url}</span>
      <p className={styles.hint}>
        {resolved ? t("login.qrHint") : t("login.qrHintFallback")}
      </p>
    </div>
  );
}