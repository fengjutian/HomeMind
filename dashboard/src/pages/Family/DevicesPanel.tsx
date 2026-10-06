import { useCallback, useEffect, useState } from "react";
import {
  App,
  Button,
  Card,
  Empty,
  Form,
  Input,
  List,
  Modal,
  Popconfirm,
  Select,
  Space,
  Tag,
  Tooltip,
  Typography,
} from "antd";
import { Copy, Plus, RefreshCw, RotateCw, Trash2, Zap } from "lucide-react";
import { useTranslation } from "react-i18next";

import {
  homeMindFamilyApi,
  type FamilyDevice,
  type FamilyDeviceCommand,
  type FamilyDevicePairingResponse,
} from "../../api/modules/homeMindFamily";
import { useServerTimezone } from "../../hooks/useServerTimezone";
import { formatServerDateTime } from "../../utils/formatMessageTime";
import styles from "./index.module.less";

interface DevicesPanelProps {
  familyId: string;
}

const statusColor = (status: string) => {
  if (status === "ONLINE") return "green";
  if (status === "BUSY") return "gold";
  if (status === "ERROR") return "red";
  return "default";
};

export default function DevicesPanel({ familyId }: DevicesPanelProps) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const timezone = useServerTimezone();
  const [devices, setDevices] = useState<FamilyDevice[]>([]);
  const [loading, setLoading] = useState(false);
  const [creating, setCreating] = useState(false);
  const [pairing, setPairing] = useState<FamilyDevicePairingResponse | null>(
    null,
  );
  const [rotatedToken, setRotatedToken] = useState<{ deviceId: string; token: string } | null>(
    null,
  );
  const [commands, setCommands] = useState<FamilyDeviceCommand[]>([]);
  const [commandDeviceId, setCommandDeviceId] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const rows = await homeMindFamilyApi.listDevices(familyId);
      setDevices(rows);
    } catch (error) {
      message.error(error instanceof Error ? error.message : String(error));
    } finally {
      setLoading(false);
    }
  }, [familyId, message]);

  useEffect(() => {
    void load();
  }, [load]);

  const createPairing = async (values: {
    name: string;
    device_type: string;
    platform: string;
    capabilities: string;
  }) => {
    setCreating(true);
    try {
      const result = await homeMindFamilyApi.createDevicePairing(familyId, {
        name: values.name,
        device_type: values.device_type,
        platform: values.platform || undefined,
        capabilities: values.capabilities
          .split(",")
          .map((entry) => entry.trim())
          .filter(Boolean),
      });
      setPairing(result);
      message.success(t("family.devicePairingCreated", "配对码已生成"));
      await load();
    } catch (error) {
      message.error(error instanceof Error ? error.message : String(error));
    } finally {
      setCreating(false);
    }
  };

  const removeDevice = async (device: FamilyDevice) => {
    try {
      await homeMindFamilyApi.deleteDevice(familyId, device.id);
      message.success(t("family.deviceDeleted", "设备已删除"));
      await load();
    } catch (error) {
      message.error(error instanceof Error ? error.message : String(error));
    }
  };

  const rotate = async (device: FamilyDevice) => {
    try {
      const result = await homeMindFamilyApi.rotateDeviceToken(
        familyId,
        device.id,
      );
      setRotatedToken({ deviceId: device.id, token: result.token });
      message.success(t("family.deviceTokenRotated", "令牌已轮换"));
    } catch (error) {
      message.error(error instanceof Error ? error.message : String(error));
    }
  };

  const revoke = async (device: FamilyDevice) => {
    try {
      await homeMindFamilyApi.revokeDeviceToken(familyId, device.id);
      message.success(t("family.deviceTokenRevoked", "令牌已撤销"));
    } catch (error) {
      message.error(error instanceof Error ? error.message : String(error));
    }
  };

  const showCommands = async (device: FamilyDevice) => {
    setCommandDeviceId(device.id);
    try {
      const rows = await homeMindFamilyApi.listDeviceCommands(
        familyId,
        device.id,
      );
      setCommands(rows);
    } catch (error) {
      message.error(error instanceof Error ? error.message : String(error));
    }
  };

  const copyText = async (text: string, messageText: string) => {
    try {
      await navigator.clipboard.writeText(text);
      message.success(messageText);
    } catch {
      message.warning(t("family.copyFailed", "复制失败，请手动复制"));
    }
  };

  return (
    <div className={styles.twoColumnGrid}>
      <Card
        title={t("family.devices", "已配对设备")}
        className={styles.panelCard}
        extra={
          <Button
            type="text"
            loading={loading}
            icon={<RefreshCw size={16} />}
            onClick={load}
          />
        }
      >
        <List
          dataSource={devices}
          locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} /> }}
          renderItem={(device) => (
            <List.Item
              actions={[
                <Tooltip
                  key="commands"
                  title={t("family.viewCommands", "查看最近命令")}
                >
                  <Button
                    type="text"
                    icon={<Zap size={16} />}
                    onClick={() => void showCommands(device)}
                  />
                </Tooltip>,
                <Tooltip
                  key="rotate"
                  title={t("family.rotateToken", "轮换令牌")}
                >
                  <Button
                    type="text"
                    icon={<RotateCw size={16} />}
                    onClick={() => void rotate(device)}
                  />
                </Tooltip>,
                <Popconfirm
                  key="revoke"
                  title={t("family.revokeTokenConfirm", "撤销该设备的活跃令牌？")}
                  okText={t("family.confirm", "确认")}
                  cancelText={t("family.cancel", "取消")}
                  onConfirm={() => void revoke(device)}
                >
                  <Button type="text" icon={<Trash2 size={16} />} />
                </Popconfirm>,
                <Popconfirm
                  key="delete"
                  title={t("family.deleteDeviceConfirm", "永久删除该设备？")}
                  okText={t("family.confirm", "确认")}
                  cancelText={t("family.cancel", "取消")}
                  onConfirm={() => void removeDevice(device)}
                >
                  <Button type="text" danger icon={<Trash2 size={16} />} />
                </Popconfirm>,
              ]}
            >
              <List.Item.Meta
                title={
                  <Space>
                    <span>{device.name}</span>
                    <Tag>{device.device_type}</Tag>
                    {device.platform && <Tag color="blue">{device.platform}</Tag>}
                    <Tag color={statusColor(device.status)}>
                      {device.status}
                    </Tag>
                  </Space>
                }
                description={
                  <Space direction="vertical" size={2}>
                    <Typography.Text type="secondary">
                      {t("family.lastSeen", "最后心跳")}：
                      {device.last_seen !== null
                        ? formatServerDateTime(device.last_seen, timezone)
                        : t("family.neverSeen", "从未上线")}
                    </Typography.Text>
                    <Space size={4} wrap>
                      {device.capabilities.map((capability) => (
                        <Tag key={capability}>{capability}</Tag>
                      ))}
                    </Space>
                  </Space>
                }
              />
            </List.Item>
          )}
        />
        {rotatedToken && (
          <div className={styles.inviteTokenBox}>
            <Typography.Text type="secondary">
              {t("family.tokenShownOnce", "令牌仅展示一次，请立刻复制")}
            </Typography.Text>
            <Space.Compact style={{ width: "100%", marginTop: 8 }}>
              <Input value={rotatedToken.token} readOnly />
              <Button
                icon={<Copy size={16} />}
                onClick={() =>
                  void copyText(
                    rotatedToken.token,
                    t("family.tokenCopied", "令牌已复制"),
                  )
                }
              >
                {t("family.copy", "复制")}
              </Button>
            </Space.Compact>
            <Button
              size="small"
              type="link"
              onClick={() => setRotatedToken(null)}
              style={{ marginTop: 4 }}
            >
              {t("family.close", "关闭")}
            </Button>
          </div>
        )}
      </Card>

      <Card
        title={t("family.addDevice", "配对新设备")}
        className={styles.panelCard}
      >
        <Form
          layout="vertical"
          onFinish={createPairing}
          initialValues={{ device_type: "phone", platform: "" }}
        >
          <Form.Item
            name="name"
            label={t("family.deviceName", "设备名称")}
            rules={[{ required: true, min: 1, max: 100 }]}
          >
            <Input placeholder={t("family.deviceNamePlaceholder", "如：厨房平板")} />
          </Form.Item>
          <Form.Item name="device_type" label={t("family.deviceType", "设备类型")}>
            <Select
              options={[
                { value: "phone", label: t("family.deviceType.phone", "手机") },
                { value: "tablet", label: t("family.deviceType.tablet", "平板") },
                { value: "speaker", label: t("family.deviceType.speaker", "音箱") },
                { value: "display", label: t("family.deviceType.display", "显示屏") },
                { value: "rpi", label: t("family.deviceType.rpi", "树莓派") },
              ]}
            />
          </Form.Item>
          <Form.Item name="platform" label={t("family.platform", "平台（可选）")}>
            <Input placeholder={t("family.platformPlaceholder", "android / ios / linux")} />
          </Form.Item>
          <Form.Item
            name="capabilities"
            label={t("family.capabilities", "能力列表（逗号分隔）")}
            rules={[{ required: true, min: 1 }]}
          >
            <Input placeholder="display.show, audio.play" />
          </Form.Item>
          <Button
            type="primary"
            htmlType="submit"
            icon={<Plus size={16} />}
            loading={creating}
          >
            {t("family.createPairing", "生成配对码")}
          </Button>
        </Form>
        {pairing && (
          <div className={styles.inviteTokenBox}>
            <Typography.Text type="secondary">
              {t("family.pairingShownOnce", "配对码仅展示一次，请立刻交付给设备")}
            </Typography.Text>
            <Space.Compact style={{ width: "100%", marginTop: 8 }}>
              <Input value={pairing.code} readOnly />
              <Button
                icon={<Copy size={16} />}
                onClick={() =>
                  void copyText(
                    pairing.code,
                    t("family.pairingCopied", "配对码已复制"),
                  )
                }
              >
                {t("family.copy", "复制")}
              </Button>
            </Space.Compact>
            <Button
              size="small"
              type="link"
              onClick={() => setPairing(null)}
              style={{ marginTop: 4 }}
            >
              {t("family.close", "关闭")}
            </Button>
          </div>
        )}
      </Card>

      <Modal
        title={t("family.recentCommands", "最近命令")}
        open={commandDeviceId !== null}
        onCancel={() => {
          setCommandDeviceId(null);
          setCommands([]);
        }}
        footer={null}
        width={640}
      >
        <List
          dataSource={commands}
          locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} /> }}
          renderItem={(command) => (
            <List.Item>
              <List.Item.Meta
                title={
                  <Space>
                    <span>{command.capability}</span>
                    <Tag color={statusColor(command.status)}>
                      {command.status}
                    </Tag>
                  </Space>
                }
                description={
                  <Space direction="vertical" size={2}>
                    <Typography.Text type="secondary">
                      {formatServerDateTime(command.created_at, timezone)}
                    </Typography.Text>
                    {command.error && (
                      <Typography.Text type="danger">
                        {command.error}
                      </Typography.Text>
                    )}
                    {command.result && (
                      <Typography.Text code ellipsis>
                        {JSON.stringify(command.result)}
                      </Typography.Text>
                    )}
                  </Space>
                }
              />
            </List.Item>
          )}
        />
      </Modal>
    </div>
  );
}
