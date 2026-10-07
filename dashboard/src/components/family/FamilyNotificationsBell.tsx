/**
 * The notification bell (Stage 2).
 *
 * The unread count is fetched from the server on every open rather than
 * tracked from pushed events. That is deliberate: the database is the
 * truth, so a badge that was missed while the socket was down — or that
 * never arrived because the browser was closed — still appears the next
 * time the bell is opened.
 *
 * Copy is resolved locally from the stored `title_key` / `body_key`, so
 * the same notification reads in the reader's language rather than the
 * language of whoever happened to trigger it.
 */
import { useCallback, useEffect, useState } from "react";
import { App, Badge, Button, Drawer, Empty, List, Segmented, Space, Typography } from "antd";
import { BellOutlined } from "@ant-design/icons";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";

import {
  homeMindNotificationApi,
  type FamilyNotification,
} from "../../api/modules/homeMindNotifications";
import {
  collectPages,
  homeMindMobileApi,
} from "../../api/modules/homeMindMobile";
import { useActiveFamily } from "../../hooks/useActiveFamily";
import { useServerTimezone } from "../../hooks/useServerTimezone";
import { formatMessageTime } from "../../utils/formatMessageTime";

const { Text } = Typography;

type Filter = "ALL" | "UNREAD";

/**
 * Best-effort deep link for a notification's target.
 *
 * Returns `null` when the target has no page, in which case the entry
 * stays read-only rather than sending the user somewhere arbitrary.
 */
function targetRoute(notification: FamilyNotification): string | null {
  switch (notification.target_type) {
    case "TASK":
      return "/family-tasks";
    case "APPROVAL":
      return "/approvals";
    case "CALENDAR_EVENT":
      return "/calendar";
    case "DEVICE":
      return "/devices";
    default:
      return null;
  }
}

export default function FamilyNotificationsBell() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { message } = App.useApp();
  const { familyId } = useActiveFamily();
  const timezone = useServerTimezone();

  const [open, setOpen] = useState(false);
  const [filter, setFilter] = useState<Filter>("ALL");
  const [unread, setUnread] = useState(0);
  const [items, setItems] = useState<FamilyNotification[]>([]);
  const [loading, setLoading] = useState(false);

  const loadCount = useCallback(async () => {
    if (familyId === null) {
      setUnread(0);
      return;
    }
    try {
      const result = await homeMindNotificationApi.unreadCount(familyId);
      setUnread(result.unread);
    } catch {
      // A badge that cannot be fetched shows zero rather than a stale
      // number that lies about the inbox.
      setUnread(0);
    }
  }, [familyId]);

  const loadList = useCallback(async () => {
    if (familyId === null) {
      setItems([]);
      return;
    }
    setLoading(true);
    try {
      // Cursor paging rather than a bare offset list: a notification
      // arriving while the drawer is open shifts an offset page, and
      // the reader silently misses one.
      const collected = await collectPages<FamilyNotification>(
        (cursor) =>
          homeMindMobileApi.notificationsPage(familyId, {
            cursor,
            limit: 20,
            unreadOnly: filter === "UNREAD",
          }),
        { maxPages: 5 },
      );
      setItems(collected);
    } catch (err) {
      message.error(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, [familyId, filter, message]);

  useEffect(() => {
    void loadCount();
  }, [loadCount]);

  useEffect(() => {
    if (open) void loadList();
  }, [open, loadList]);

  const openOne = async (notification: FamilyNotification) => {
    try {
      if (notification.read_at === null) {
        await homeMindNotificationApi.markRead(familyId as string, notification.id);
        await loadCount();
        await loadList();
      }
    } catch (err) {
      message.error(err instanceof Error ? err.message : String(err));
    }
    const route = targetRoute(notification);
    if (route !== null) {
      setOpen(false);
      navigate(route);
    }
  };

  const readAll = async () => {
    if (familyId === null) return;
    try {
      await homeMindNotificationApi.markAllRead(familyId);
      await loadCount();
      await loadList();
    } catch (err) {
      message.error(err instanceof Error ? err.message : String(err));
    }
  };

  return (
    <>
      <Badge count={unread} size="small" offset={[-2, 2]}>
        <Button
          type="text"
          icon={<BellOutlined />}
          aria-label={t("notificationsBell.title", "通知")}
          onClick={() => setOpen(true)}
        />
      </Badge>
      <Drawer
        open={open}
        onClose={() => setOpen(false)}
        title={t("notificationsBell.title", "通知")}
        width={420}
        extra={
          <Space>
            <Segmented
              size="small"
              value={filter}
              onChange={(value) => setFilter(String(value) as Filter)}
              options={[
                { label: t("notificationsBell.all", "全部"), value: "ALL" },
                { label: t("notificationsBell.unread", "未读"), value: "UNREAD" },
              ]}
            />
            <Button size="small" onClick={() => void readAll()} disabled={unread === 0}>
              {t("notificationsBell.readAll", "全部已读")}
            </Button>
          </Space>
        }
      >
        <List
          loading={loading}
          dataSource={items}
          locale={{
            emptyText: <Empty description={t("notificationsBell.empty", "暂无通知")} />,
          }}
          renderItem={(notification) => {
            const title = t(notification.title_key, notification.type);
            const body = t(notification.body_key, "", notification.params);
            return (
              <List.Item
                onClick={() => void openOne(notification)}
                style={{
                  cursor: targetRoute(notification) !== null ? "pointer" : "default",
                  opacity: notification.read_at === null ? 1 : 0.55,
                }}
              >
                <List.Item.Meta
                  title={
                    <Space>
                      <Text strong={notification.read_at === null}>{title}</Text>
                      {notification.severity !== "INFO" ? (
                        <Text type={notification.severity === "CRITICAL" ? "danger" : "warning"}>
                          {notification.severity}
                        </Text>
                      ) : null}
                    </Space>
                  }
                  description={
                    <Space direction="vertical" size={0}>
                      <Text type="secondary">{body}</Text>
                      <Text type="secondary" style={{ fontSize: 12 }}>
                        {formatMessageTime(notification.created_at * 1000, timezone)}
                      </Text>
                    </Space>
                  }
                />
              </List.Item>
            );
          }}
        />
      </Drawer>
    </>
  );
}