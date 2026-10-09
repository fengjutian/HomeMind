import { useCallback, useEffect, useState } from "react";
import { Alert, Button, Descriptions, Space, Tag, Typography } from "antd";
import { Copy, RefreshCw } from "lucide-react";
import { useTranslation } from "react-i18next";

import { bridgeApi, type BridgeDiagnostics } from "../../../api/modules/bridge";
import styles from "./BridgeDiagnostics.module.less";

const { Text } = Typography;

/** States only a human can clear; everything else is worth another try. */
const NEEDS_HUMAN: Record<string, "error" | "warning" | "success" | "default"> = {
  ONLINE: "success",
  DEGRADED: "warning",
  CONNECTING: "warning",
  AUTHENTICATING: "warning",
  DISCONNECTED: "default",
  REAUTH_REQUIRED: "error",
  INCOMPATIBLE: "error",
  DISABLED: "default",
};

function formatUptime(seconds: number | null): string {
  if (!seconds || seconds <= 0) return "-";
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  if (days > 0) return `${days}d ${hours}h`;
  if (hours > 0) return `${hours}h ${minutes}m`;
  return `${minutes}m`;
}

export default function BridgeDiagnosticsPanel() {
  const { t } = useTranslation();
  const [data, setData] = useState<BridgeDiagnostics | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      setData(await bridgeApi.diagnostics());
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const copy = useCallback(async () => {
    if (!data) return;
    const text = JSON.stringify(data, null, 2);
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      // Clipboard can be denied; the panel still shows the data on screen.
    }
  }, [data]);

  if (error) {
    return (
      <Alert
        type="warning"
        showIcon
        message={t("advancedSettings.bridge.diagnostics.loadFailed")}
        description={error}
      />
    );
  }

  return (
    <div className={styles.panel}>
      <Space className={styles.toolbar} wrap>
        <Button
          icon={<RefreshCw size={14} />}
          onClick={() => void load()}
          loading={busy}
          size="small"
        >
          {t("advancedSettings.bridge.diagnostics.refresh")}
        </Button>
        <Button
          icon={<Copy size={14} />}
          onClick={() => void copy()}
          disabled={!data}
          size="small"
        >
          {t("advancedSettings.bridge.diagnostics.copy")}
        </Button>
        {data && (
          <Text type="secondary" className={styles.summary}>
            {t("advancedSettings.bridge.diagnostics.summary", {
              online: data.connections_online,
              total: data.connections_total,
            })}
          </Text>
        )}
      </Space>

      {data && data.connections_needing_human.length > 0 && (
        <Alert
          type="warning"
          showIcon
          className={styles.alert}
          message={t("advancedSettings.bridge.diagnostics.needsHuman", {
            count: data.connections_needing_human.length,
          })}
        />
      )}

      {data && (
        <>
          <Descriptions
            size="small"
            column={2}
            bordered
            className={styles.descriptions}
            title={t("advancedSettings.bridge.diagnostics.instance")}
          >
            <Descriptions.Item label={t("advancedSettings.bridge.diagnostics.protocol")}>
              {data.local_protocol}
              {data.connections.some((c) => c.peer_protocol) && (
                <Text type="secondary">
                  {" "}
                  ·{" "}
                  {t("advancedSettings.bridge.diagnostics.peer")}{" "}
                  {data.connections
                    .map((c) => c.peer_protocol ?? "?")
                    .filter((v, i, a) => a.indexOf(v) === i)
                    .join(", ")}
                </Text>
              )}
            </Descriptions.Item>
            <Descriptions.Item label={t("advancedSettings.bridge.diagnostics.capabilities")}>
              {data.local_capabilities.join(", ") || "-"}
            </Descriptions.Item>
            <Descriptions.Item label={t("advancedSettings.bridge.diagnostics.instanceId")}>
              <Text code copyable>
                {data.instance_id || "-"}
              </Text>
            </Descriptions.Item>
            <Descriptions.Item
              label={t("advancedSettings.bridge.diagnostics.negotiated")}
            >
              {data.connections
                .flatMap((c) => c.capabilities)
                .filter((v, i, a) => a.indexOf(v) === i)
                .join(", ") || "-"}
            </Descriptions.Item>
          </Descriptions>

          <table className={styles.table}>
            <thead>
              <tr>
                <th>{t("advancedSettings.bridge.diagnostics.connection")}</th>
                <th>{t("advancedSettings.bridge.diagnostics.state")}</th>
                <th>{t("advancedSettings.bridge.diagnostics.uptime")}</th>
                <th>{t("advancedSettings.bridge.diagnostics.reconnects")}</th>
                <th>{t("advancedSettings.bridge.diagnostics.lastError")}</th>
              </tr>
            </thead>
            <tbody>
              {data.connections.map((c) => (
                <tr key={c.connection_id}>
                  <td>
                    <div>{c.display_name}</div>
                    <Text type="secondary" className={styles.peer}>
                      {c.peer_base_url}
                    </Text>
                  </td>
                  <td>
                    <Tag color={NEEDS_HUMAN[c.state] ?? "default"}>
                      {c.state}
                    </Tag>
                  </td>
                  <td>{formatUptime(c.online_seconds)}</td>
                  <td>{c.reconnect_attempts}</td>
                  <td className={styles.errorCell}>
                    {c.last_error ? (
                      <Text type="danger" className={styles.errorText}>
                        {c.last_error}
                      </Text>
                    ) : (
                      "-"
                    )}
                  </td>
                </tr>
              ))}
              {data.connections.length === 0 && (
                <tr>
                  <td colSpan={5} className={styles.empty}>
                    {t("advancedSettings.bridge.diagnostics.empty")}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </>
      )}
    </div>
  );
}
