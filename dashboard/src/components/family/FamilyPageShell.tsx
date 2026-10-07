/**
 * Shared scaffold for the standalone HomeMind pages.
 *
 * Every page in Stage 8 needs the same three things: the active family
 * (so switching families in the header re-renders it), a "no family
 * selected" prompt, and a data-loading shell. Duplicating that in eight
 * files is how pages drift apart, so it lives here once.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { Alert, Button, Empty, Space, Spin, Typography } from "antd";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";

import { useActiveFamily } from "../../hooks/useActiveFamily";
import { homeMindFamilyApi } from "../../api/modules/homeMindFamily";

const { Text } = Typography;

export interface FamilyPageShellProps {
  title: string;
  subtitle?: string;
  actions?: React.ReactNode;
  /** When set, the shell renders this instead of the default prompt. */
  emptyHint?: string;
  /** Page body. Pages read the active family from the same hook. */
  children: React.ReactNode;
}

/**
 * Renders `children` with the active family id, or a prompt when the
 * user has not chosen one. Pages must never guess a family — the
 * backend refuses to, and so does the UI.
 */
export default function FamilyPageShell({
  title,
  subtitle,
  actions,
  emptyHint,
  children,
}: FamilyPageShellProps) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { familyId, loading } = useActiveFamily();

  return (
    <div style={{ padding: 16 }}>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          marginBottom: 12,
        }}
      >
        <div>
          <Typography.Title level={4} style={{ margin: 0 }}>
            {title}
          </Typography.Title>
          {subtitle ? (
            <Text type="secondary">{subtitle}</Text>
          ) : null}
        </div>
        <Space>{actions}</Space>
      </div>
      {loading ? (
        <div style={{ textAlign: "center", padding: 48 }}>
          <Spin />
        </div>
      ) : familyId === null ? (
        <Empty description={emptyHint ?? t("family.noActiveFamily", "尚未选择家庭")}>
          <Button type="primary" onClick={() => navigate("/family")}>
            {t("family.selectFamily", "去选择家庭")}
          </Button>
        </Empty>
      ) : (
        children
      )}
    </div>
  );
}

/**
 * Minimal async-data hook with a stale-response guard.
 *
 * Every HomeMind page refetches when the active family changes, and a
 * slow response from the previous family must not overwrite the new
 * one — the request counter is what makes that safe.
 */
export function useFamilyQuery<T>(
  familyId: string | null,
  fetcher: (familyId: string) => Promise<T>,
): {
  data: T | undefined;
  loading: boolean;
  error: Error | null;
  reload: () => void;
} {
  const [data, setData] = useState<T | undefined>(undefined);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const [nonce, setNonce] = useState(0);
  const requestId = useRef(0);

  useEffect(() => {
    if (familyId === null) {
      setData(undefined);
      return;
    }
    const current = ++requestId.current;
    setLoading(true);
    fetcher(familyId)
      .then((next) => {
        if (current !== requestId.current) return;
        setData(next);
        setError(null);
      })
      .catch((err: unknown) => {
        if (current !== requestId.current) return;
        setError(err instanceof Error ? err : new Error(String(err)));
      })
      .finally(() => {
        if (current === requestId.current) setLoading(false);
      });
  }, [familyId, nonce, fetcher]);

  const reload = useCallback(() => setNonce((n) => n + 1), []);
  return { data, loading, error, reload };
}

export function QueryError({ error }: { error: Error | null }) {
  if (error === null) return null;
  return <Alert type="error" message={error.message} showIcon style={{ marginBottom: 12 }} />;
}

export { homeMindFamilyApi };
