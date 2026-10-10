import { useEffect, useState } from "react";
import { QRCodeSVG } from "qrcode.react";
import { useTranslation } from "react-i18next";
import { authApi } from "../../api/modules/auth";
import styles from "./LoginQrPanel.module.less";

/**
 * QR code showing this server's LAN address, so a phone on the same network can
 * open the login page by scanning instead of typing an IP.
 *
 * The browser's own origin is useless when the host opened the page via
 * `localhost` — a phone would resolve that to itself. So we ask the server for
 * its detected LAN address and fall back to the current origin only when that
 * request fails.
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
        setUrl(info.url);
        setResolved(info.is_lan);
      })
      .catch(() => {
        if (cancelled) return;
        // Backend unreachable or predating this endpoint — show where we are.
        setUrl(window.location.origin);
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
