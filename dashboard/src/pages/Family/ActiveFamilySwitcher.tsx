import { useEffect, useState } from "react";
import { App, Button, Dropdown, Spin, Tag } from "antd";
import { ChevronDown, RefreshCw, X } from "lucide-react";
import { useTranslation } from "react-i18next";

import {
  homeMindFamilyApi,
  type HomeMindFamily,
} from "../../api/modules/homeMindFamily";
import { useActiveFamily } from "../../hooks/useActiveFamily";
import styles from "./index.module.less";

interface ActiveFamilySwitcherProps {
  families: HomeMindFamily[];
  onChange?: (familyId: string | null) => void;
}

export default function ActiveFamilySwitcher({
  families,
  onChange,
}: ActiveFamilySwitcherProps) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const { familyId, loading, setFamily, clear, refresh } = useActiveFamily();
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (loading) return;
    onChange?.(familyId);
  }, [familyId, loading, onChange]);

  const switchTo = async (next: string) => {
    if (next === familyId) return;
    setBusy(true);
    try {
      await setFamily(next);
      message.success(t("family.activeFamily.switched", "已切换到当前家族"));
    } catch (error) {
      message.error(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  };

  const clearActive = async () => {
    setBusy(true);
    try {
      await clear();
      message.success(t("family.activeFamily.cleared", "已退出当前家族"));
    } catch (error) {
      message.error(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  };

  const refreshFamilies = async () => {
    setBusy(true);
    try {
      await refresh();
      await homeMindFamilyApi.listFamilies();
    } catch (error) {
      message.error(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  };

  const current = families.find((family) => family.id === familyId);

  return (
    <Dropdown
      trigger={["click"]}
      menu={{
        items: [
          ...families.map((family) => ({
            key: family.id,
            label: family.name,
            disabled: busy,
          })),
          { type: "divider" as const },
          {
            key: "__refresh",
            label: t("family.activeFamily.refresh", "刷新"),
            icon: <RefreshCw size={14} />,
            disabled: busy || loading,
          },
          {
            key: "__clear",
            label: t("family.activeFamily.exit", "退出当前家族"),
            icon: <X size={14} />,
            disabled: busy || familyId === null,
          },
        ],
        onClick: ({ key }) => {
          if (key === "__refresh") void refreshFamilies();
          else if (key === "__clear") void clearActive();
          else void switchTo(String(key));
        },
      }}
    >
      <Button className={styles.activeFamilyButton}>
        {loading || busy ? (
          <Spin size="small" />
        ) : current ? (
          <Tag color="blue" style={{ margin: 0 }}>
            {current.name}
          </Tag>
        ) : (
          <span>
            {t("family.activeFamily.none", "未选择家族")}
          </span>
        )}
        <ChevronDown size={14} />
      </Button>
    </Dropdown>
  );
}
