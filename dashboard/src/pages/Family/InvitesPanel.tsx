import { useCallback, useEffect, useState } from "react";
import {
  App,
  Button,
  Card,
  Empty,
  Form,
  Input,
  InputNumber,
  List,
  Popconfirm,
  Select,
  Space,
  Tag,
  Typography,
} from "antd";
import { Copy, Plus, RefreshCw, Trash2 } from "lucide-react";
import { useTranslation } from "react-i18next";

import {
  homeMindFamilyApi,
  type FamilyInvite,
  type FamilyInviteCreateResponse,
} from "../../api/modules/homeMindFamily";
import { useServerTimezone } from "../../hooks/useServerTimezone";
import { formatServerDateTime } from "../../utils/formatMessageTime";
import styles from "./index.module.less";

interface InvitesPanelProps {
  familyId: string;
}

export default function InvitesPanel({ familyId }: InvitesPanelProps) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const timezone = useServerTimezone();
  const [invites, setInvites] = useState<FamilyInvite[]>([]);
  const [includeRedeemed, setIncludeRedeemed] = useState(false);
  const [loading, setLoading] = useState(false);
  const [creating, setCreating] = useState(false);
  const [latest, setLatest] = useState<FamilyInviteCreateResponse | null>(
    null,
  );

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const rows = await homeMindFamilyApi.listInvites(
        familyId,
        includeRedeemed,
      );
      setInvites(rows);
    } catch (error) {
      message.error(error instanceof Error ? error.message : String(error));
    } finally {
      setLoading(false);
    }
  }, [familyId, includeRedeemed, message]);

  useEffect(() => {
    void load();
  }, [load]);

  const create = async (values: {
    display_name: string;
    role: string;
    ttl_seconds: number;
  }) => {
    setCreating(true);
    try {
      const result = await homeMindFamilyApi.createInvite(familyId, {
        display_name: values.display_name,
        role: values.role,
        ttl_seconds: values.ttl_seconds,
      });
      setLatest(result);
      message.success(t("family.inviteCreated", "邀请已创建"));
      await load();
    } catch (error) {
      message.error(error instanceof Error ? error.message : String(error));
    } finally {
      setCreating(false);
    }
  };

  const revoke = async (invite: FamilyInvite) => {
    try {
      await homeMindFamilyApi.revokeInvite(familyId, invite.id);
      message.success(t("family.inviteRevoked", "邀请已撤销"));
      await load();
    } catch (error) {
      message.error(error instanceof Error ? error.message : String(error));
    }
  };

  const copyToken = async (token: string) => {
    try {
      await navigator.clipboard.writeText(token);
      message.success(t("family.tokenCopied", "令牌已复制"));
    } catch {
      message.warning(t("family.copyFailed", "复制失败，请手动复制"));
    }
  };

  return (
    <div className={styles.twoColumnGrid}>
      <Card
        title={t("family.createInvite", "创建邀请")}
        className={styles.panelCard}
      >
        <Form
          layout="vertical"
          onFinish={create}
          initialValues={{ role: "MEMBER", ttl_seconds: 604800 }}
        >
          <Form.Item
            name="display_name"
            label={t("family.inviteeDisplayName", "被邀请人称呼")}
            rules={[
              {
                required: true,
                min: 1,
                max: 100,
                message: t(
                  "family.inviteeDisplayNameRequired",
                  "请输入 1-100 字的称呼",
                ),
              },
            ]}
          >
            <Input placeholder={t("family.inviteePlaceholder", "如：小宝")} />
          </Form.Item>
          <Form.Item name="role" label={t("family.role", "角色")}>
            <Select
              options={[
                { value: "MEMBER", label: t("family.role.member", "成员") },
                { value: "CHILD", label: t("family.role.child", "小孩") },
                { value: "ELDER", label: t("family.role.elder", "长辈") },
                { value: "ADMIN", label: t("family.role.admin", "管理员") },
              ]}
            />
          </Form.Item>
          <Form.Item
            name="ttl_seconds"
            label={t("family.inviteTtl", "有效期（秒）")}
            rules={[{ required: true, type: "number", min: 60, max: 2592000 }]}
          >
            <InputNumber min={60} max={2592000} step={3600} style={{ width: "100%" }} />
          </Form.Item>
          <Button
            type="primary"
            htmlType="submit"
            icon={<Plus size={16} />}
            loading={creating}
          >
            {t("family.createInvite", "创建邀请")}
          </Button>
        </Form>
        {latest && (
          <div className={styles.inviteTokenBox}>
            <Typography.Text type="secondary">
              {t("family.tokenShownOnce", "令牌仅展示一次，请立刻复制")}
            </Typography.Text>
            <Space.Compact style={{ width: "100%", marginTop: 8 }}>
              <Input value={latest.token} readOnly />
              <Button
                type="default"
                icon={<Copy size={16} />}
                onClick={() => void copyToken(latest.token)}
              >
                {t("family.copy", "复制")}
              </Button>
            </Space.Compact>
          </div>
        )}
      </Card>

      <Card
        title={t("family.invites", "邀请列表")}
        className={styles.panelCard}
        extra={
          <Space>
            <Select
              size="small"
              value={includeRedeemed ? "all" : "pending"}
              onChange={(value) => setIncludeRedeemed(value === "all")}
              options={[
                { value: "pending", label: t("family.invitesPending", "未使用") },
                { value: "all", label: t("family.invitesAll", "全部") },
              ]}
              style={{ width: 100 }}
            />
            <Button
              type="text"
              loading={loading}
              icon={<RefreshCw size={16} />}
              onClick={load}
            />
          </Space>
        }
      >
        <List
          dataSource={invites}
          locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} /> }}
          renderItem={(invite) => (
            <List.Item
              actions={
                invite.redeemed_at === null
                  ? [
                      <Popconfirm
                        key="revoke"
                        title={t("family.revokeInvite", "撤销该邀请？")}
                        okText={t("family.confirm", "确认")}
                        cancelText={t("family.cancel", "取消")}
                        onConfirm={() => void revoke(invite)}
                      >
                        <Button
                          type="text"
                          danger
                          icon={<Trash2 size={16} />}
                        >
                          {t("family.revoke", "撤销")}
                        </Button>
                      </Popconfirm>,
                    ]
                  : undefined
              }
            >
              <List.Item.Meta
                title={
                  <Space>
                    <span>{invite.display_name}</span>
                    <Tag color="blue">{invite.role}</Tag>
                    {invite.redeemed_at !== null ? (
                      <Tag color="green">
                        {t("family.redeemed", "已兑换")}
                      </Tag>
                    ) : (
                      <Tag color="gold">
                        {t("family.pending", "待使用")}
                      </Tag>
                    )}
                  </Space>
                }
                description={
                  <Space direction="vertical" size={2}>
                    <Typography.Text type="secondary">
                      {t("family.expiresAt", "过期时间")}：
                      {formatServerDateTime(invite.expires_at, timezone)}
                    </Typography.Text>
                    {invite.redeemed_at !== null && (
                      <Typography.Text type="secondary">
                        {t("family.redeemedAt", "兑换时间")}：
                        {formatServerDateTime(invite.redeemed_at, timezone)}
                      </Typography.Text>
                    )}
                  </Space>
                }
              />
            </List.Item>
          )}
        />
      </Card>
    </div>
  );
}
