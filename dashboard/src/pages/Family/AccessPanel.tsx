import { useCallback, useEffect, useState } from "react";
import {
  App,
  Button,
  Card,
  Empty,
  Form,
  Input,
  List,
  Popconfirm,
  Select,
  Space,
  Tag,
} from "antd";
import { Plus, Trash2 } from "lucide-react";
import { useTranslation } from "react-i18next";

import {
  homeMindFamilyApi,
  type FamilyMember,
  type FamilyPermission,
  type FamilySpace,
} from "../../api/modules/homeMindFamily";
import styles from "./index.module.less";

interface AccessPanelProps {
  familyId: string;
  members: FamilyMember[];
}

export default function AccessPanel({ familyId, members }: AccessPanelProps) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const [spaces, setSpaces] = useState<FamilySpace[]>([]);
  const [permissions, setPermissions] = useState<FamilyPermission[]>([]);

  const load = useCallback(async () => {
    try {
      const [nextSpaces, nextPermissions] = await Promise.all([
        homeMindFamilyApi.listSpaces(familyId),
        homeMindFamilyApi.listPermissions(familyId),
      ]);
      setSpaces(nextSpaces);
      setPermissions(nextPermissions);
    } catch (error) {
      message.error(error instanceof Error ? error.message : String(error));
    }
  }, [familyId, message]);

  useEffect(() => void load(), [load]);

  const memberName = (id: string | null) =>
    id
      ? members.find((member) => member.id === id)?.display_name ?? id
      : t("family.allMembers", "全部成员");
  const spaceName = (id: string | null) =>
    id
      ? spaces.find((space) => space.id === id)?.name ?? id
      : t("family.allSpaces", "全部空间");

  return (
    <div className={styles.twoColumnGrid}>
      <Card title={t("family.spaces", "家庭空间")} className={styles.panelCard}>
        <Form
          className={styles.quickForm}
          layout="vertical"
          onFinish={async (values) => {
            await homeMindFamilyApi.createSpace(familyId, values);
            await load();
          }}
        >
          <Space wrap align="start">
            <Form.Item name="name" rules={[{ required: true }]}>
              <Input placeholder={t("family.spaceName", "空间名称")} />
            </Form.Item>
            <Form.Item
              name="space_type"
              initialValue="SHARED"
              rules={[{ required: true }]}
            >
              <Select
                className={styles.inlineSelect}
                options={["SHARED", "PRIVATE", "ARCHIVE"].map((value) => ({
                  value,
                  label: value,
                }))}
              />
            </Form.Item>
            <Form.Item name="owner_member_id">
              <Select
                allowClear
                className={styles.inlineSelect}
                placeholder={t("family.spaceOwner", "空间所有者")}
                options={members.map((member) => ({
                  value: member.id,
                  label: member.display_name,
                }))}
              />
            </Form.Item>
            <Button type="primary" htmlType="submit" icon={<Plus size={16} />}>
              {t("common.create", "创建")}
            </Button>
          </Space>
        </Form>
        <List
          dataSource={spaces}
          locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} /> }}
          renderItem={(space) => (
            <List.Item
              actions={[
                <Popconfirm
                  key="delete"
                  title={t("common.confirmDelete", "确定删除吗？")}
                  onConfirm={async () => {
                    await homeMindFamilyApi.deleteSpace(familyId, space.id);
                    await load();
                  }}
                >
                  <Button type="text" danger icon={<Trash2 size={16} />} />
                </Popconfirm>,
              ]}
            >
              <List.Item.Meta
                title={space.name}
                description={
                  <Space>
                    <Tag>{space.space_type}</Tag>
                    {space.owner_member_id && memberName(space.owner_member_id)}
                  </Space>
                }
              />
            </List.Item>
          )}
        />
      </Card>

      <Card
        title={t("family.permissions", "权限规则")}
        className={styles.panelCard}
      >
        <Form
          className={styles.quickForm}
          layout="vertical"
          onFinish={async (values) => {
            await homeMindFamilyApi.createPermission(familyId, values);
            await load();
          }}
        >
          <div className={styles.permissionFormGrid}>
            <Form.Item name="subject_member_id">
              <Select
                allowClear
                placeholder={t("family.allMembers", "全部成员")}
                options={members.map((member) => ({
                  value: member.id,
                  label: member.display_name,
                }))}
              />
            </Form.Item>
            <Form.Item name="space_id">
              <Select
                allowClear
                placeholder={t("family.allSpaces", "全部空间")}
                options={spaces.map((space) => ({
                  value: space.id,
                  label: space.name,
                }))}
              />
            </Form.Item>
            <Form.Item name="action" rules={[{ required: true }]}>
              <Input placeholder="photo.read" />
            </Form.Item>
            <Form.Item
              name="effect"
              initialValue="ALLOW"
              rules={[{ required: true }]}
            >
              <Select
                options={["ALLOW", "DENY", "REQUIRE_CONFIRMATION"].map(
                  (value) => ({ value, label: value }),
                )}
              />
            </Form.Item>
          </div>
          <Button type="primary" htmlType="submit" icon={<Plus size={16} />}>
            {t("common.add", "添加")}
          </Button>
        </Form>
        <List
          dataSource={permissions}
          locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} /> }}
          renderItem={(permission) => (
            <List.Item
              actions={[
                <Popconfirm
                  key="delete"
                  title={t("common.confirmDelete", "确定删除吗？")}
                  onConfirm={async () => {
                    await homeMindFamilyApi.deletePermission(
                      familyId,
                      permission.id,
                    );
                    await load();
                  }}
                >
                  <Button type="text" danger icon={<Trash2 size={16} />} />
                </Popconfirm>,
              ]}
            >
              <List.Item.Meta
                title={
                  <Space>
                    <span>{permission.action}</span>
                    <Tag color={permission.effect === "DENY" ? "red" : "green"}>
                      {permission.effect}
                    </Tag>
                  </Space>
                }
                description={`${memberName(
                  permission.subject_member_id,
                )} · ${spaceName(permission.space_id)}`}
              />
            </List.Item>
          )}
        />
      </Card>
    </div>
  );
}
