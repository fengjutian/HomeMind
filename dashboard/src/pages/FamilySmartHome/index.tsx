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
  Space,
  Tag,
  Typography,
} from "antd";
import { Plus, RefreshCw, Trash2, Wifi } from "lucide-react";
import { useTranslation } from "react-i18next";

import FamilyPageShell from "../../components/family/FamilyPageShell";
import { useActiveFamily } from "../../hooks/useActiveFamily";
import {
  homeMindFamilyApi,
  type SmartHomeEntity,
  type SmartHomeProvider,
} from "../../api/modules/homeMindFamily";

interface ProviderForm {
  name: string;
  base_url: string;
  credential: string;
}

export default function FamilySmartHome() {
  const { t } = useTranslation();
  const { familyId } = useActiveFamily();
  const { message } = App.useApp();
  const [form] = Form.useForm<ProviderForm>();
  const [providers, setProviders] = useState<SmartHomeProvider[]>([]);
  const [entities, setEntities] = useState<SmartHomeEntity[]>([]);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [open, setOpen] = useState(false);

  const load = useCallback(async () => {
    if (familyId === null) return;
    setLoading(true);
    try {
      const [nextProviders, nextEntities] = await Promise.all([
        homeMindFamilyApi.listSmartHomeProviders(familyId),
        homeMindFamilyApi.listSmartHomeEntities(familyId),
      ]);
      setProviders(nextProviders);
      setEntities(nextEntities);
    } catch (error) {
      message.error(error instanceof Error ? error.message : String(error));
    } finally {
      setLoading(false);
    }
  }, [familyId, message]);

  useEffect(() => {
    void load();
  }, [load]);

  const addProvider = async (values: ProviderForm) => {
    if (familyId === null) return;
    setSaving(true);
    try {
      const provider = await homeMindFamilyApi.createHomeAssistantProvider(
        familyId,
        values,
      );
      const probe = await homeMindFamilyApi.probeSmartHomeProvider(
        familyId,
        provider.id,
      );
      if (probe.ok) {
        await homeMindFamilyApi.syncSmartHomeProvider(familyId, provider.id);
        message.success(t("smartHome.connected"));
      } else {
        message.warning(t("smartHome.connectionFailed"));
      }
      setOpen(false);
      form.resetFields();
      await load();
    } catch (error) {
      message.error(error instanceof Error ? error.message : String(error));
    } finally {
      setSaving(false);
    }
  };

  const syncProvider = async (provider: SmartHomeProvider) => {
    if (familyId === null) return;
    try {
      const probe = await homeMindFamilyApi.probeSmartHomeProvider(
        familyId,
        provider.id,
      );
      if (!probe.ok) {
        message.error(t("smartHome.connectionFailed"));
        await load();
        return;
      }
      await homeMindFamilyApi.syncSmartHomeProvider(familyId, provider.id);
      message.success(t("smartHome.synced"));
      await load();
    } catch (error) {
      message.error(error instanceof Error ? error.message : String(error));
    }
  };

  const removeProvider = async (provider: SmartHomeProvider) => {
    if (familyId === null) return;
    try {
      await homeMindFamilyApi.deleteSmartHomeProvider(familyId, provider.id);
      message.success(t("smartHome.deleted"));
      await load();
    } catch (error) {
      message.error(error instanceof Error ? error.message : String(error));
    }
  };

  return (
    <FamilyPageShell
      title={t("smartHome.title")}
      subtitle={t("smartHome.subtitle")}
      actions={
        <Button
          type="primary"
          icon={<Plus size={16} />}
          onClick={() => setOpen(true)}
        >
          {t("smartHome.add")}
        </Button>
      }
    >
      <Space direction="vertical" size={16} style={{ width: "100%" }}>
        <Card title={t("smartHome.providers")} loading={loading}>
          <List
            dataSource={providers}
            locale={{
              emptyText: <Empty description={t("smartHome.noProviders")} />,
            }}
            renderItem={(provider) => (
              <List.Item
                actions={[
                  <Button
                    key="sync"
                    icon={<RefreshCw size={16} />}
                    onClick={() => void syncProvider(provider)}
                  >
                    {t("smartHome.sync")}
                  </Button>,
                  <Popconfirm
                    key="delete"
                    title={t("smartHome.deleteConfirm")}
                    onConfirm={() => void removeProvider(provider)}
                  >
                    <Button danger icon={<Trash2 size={16} />} />
                  </Popconfirm>,
                ]}
              >
                <List.Item.Meta
                  avatar={<Wifi size={22} />}
                  title={
                    <Space wrap>
                      <span>{provider.name}</span>
                      <Tag color={provider.last_error ? "red" : "green"}>
                        {provider.last_error ?? t("smartHome.ready")}
                      </Tag>
                    </Space>
                  }
                  description={provider.base_url}
                />
              </List.Item>
            )}
          />
        </Card>

        <Card title={t("smartHome.entities")} loading={loading}>
          <List
            dataSource={entities}
            locale={{
              emptyText: <Empty description={t("smartHome.noEntities")} />,
            }}
            renderItem={(entity) => (
              <List.Item>
                <List.Item.Meta
                  title={
                    <Space wrap>
                      <span>{entity.name}</span>
                      <Tag>{entity.domain}</Tag>
                      {entity.capabilities.map((capability) => (
                        <Tag key={capability} color="blue">
                          {capability}
                        </Tag>
                      ))}
                    </Space>
                  }
                  description={
                    <Typography.Text type="secondary">
                      {entity.external_entity_id} ·{" "}
                      {String(entity.state.state ?? "—")}
                    </Typography.Text>
                  }
                />
              </List.Item>
            )}
          />
        </Card>
      </Space>

      <Modal
        title={t("smartHome.add")}
        open={open}
        confirmLoading={saving}
        okText={t("smartHome.connect")}
        onCancel={() => setOpen(false)}
        onOk={() => void form.submit()}
        destroyOnHidden
      >
        <Form<ProviderForm>
          form={form}
          layout="vertical"
          onFinish={(values) => void addProvider(values)}
          initialValues={{ name: "Home Assistant" }}
        >
          <Form.Item
            name="name"
            label={t("smartHome.name")}
            rules={[{ required: true }]}
          >
            <Input />
          </Form.Item>
          <Form.Item
            name="base_url"
            label={t("smartHome.baseUrl")}
            rules={[{ required: true, type: "url" }]}
          >
            <Input placeholder="http://homeassistant.local:8123" />
          </Form.Item>
          <Form.Item
            name="credential"
            label={t("smartHome.token")}
            extra={t("smartHome.tokenHint")}
            rules={[{ required: true }]}
          >
            <Input.Password autoComplete="new-password" />
          </Form.Item>
        </Form>
      </Modal>
    </FamilyPageShell>
  );
}
