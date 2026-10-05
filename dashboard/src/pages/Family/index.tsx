import { useCallback, useEffect, useMemo, useState } from "react";
import {
  App,
  Button,
  Card,
  Empty,
  Form,
  Input,
  List,
  Select,
  Space,
  Spin,
  Statistic,
  Tabs,
  Tag,
  Typography,
} from "antd";
import { Plus, Search } from "lucide-react";
import { useTranslation } from "react-i18next";

import {
  homeMindFamilyApi,
  type FamilyAlbum,
  type FamilyMember,
  type FamilyMemory,
  type FamilySearchResult,
  type FamilyTask,
  type HomeMindFamily,
} from "../../api/modules/homeMindFamily";

const { Paragraph, Text, Title } = Typography;

export default function FamilyPage() {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const [families, setFamilies] = useState<HomeMindFamily[]>([]);
  const [familyId, setFamilyId] = useState("");
  const [members, setMembers] = useState<FamilyMember[]>([]);
  const [albums, setAlbums] = useState<FamilyAlbum[]>([]);
  const [tasks, setTasks] = useState<FamilyTask[]>([]);
  const [memories, setMemories] = useState<FamilyMemory[]>([]);
  const [results, setResults] = useState<FamilySearchResult[]>([]);
  const [loading, setLoading] = useState(true);
  const [creatingFamily, setCreatingFamily] = useState(false);

  const loadFamilies = useCallback(async () => {
    setLoading(true);
    try {
      const rows = await homeMindFamilyApi.listFamilies();
      setFamilies(rows);
      setFamilyId((current) =>
        current && rows.some((row) => row.id === current)
          ? current
          : (rows[0]?.id ?? ""),
      );
    } catch (error) {
      message.error(error instanceof Error ? error.message : String(error));
    } finally {
      setLoading(false);
    }
  }, [message]);

  const loadFamilyData = useCallback(async () => {
    if (!familyId) return;
    setLoading(true);
    try {
      const [nextMembers, nextAlbums, nextTasks, nextMemories] =
        await Promise.all([
          homeMindFamilyApi.listMembers(familyId),
          homeMindFamilyApi.listAlbums(familyId),
          homeMindFamilyApi.listTasks(familyId),
          homeMindFamilyApi.listMemories(familyId),
        ]);
      setMembers(nextMembers);
      setAlbums(nextAlbums);
      setTasks(nextTasks);
      setMemories(nextMemories);
    } catch (error) {
      message.error(error instanceof Error ? error.message : String(error));
    } finally {
      setLoading(false);
    }
  }, [familyId, message]);

  useEffect(() => void loadFamilies(), [loadFamilies]);
  useEffect(() => void loadFamilyData(), [loadFamilyData]);

  const activeFamily = useMemo(
    () => families.find((family) => family.id === familyId),
    [families, familyId],
  );

  const createFamily = async ({ name }: { name: string }) => {
    setCreatingFamily(true);
    try {
      const family = await homeMindFamilyApi.createFamily({ name });
      setFamilies((rows) => [...rows, family]);
      setFamilyId(family.id);
      message.success(t("family.created", "家庭已创建"));
    } finally {
      setCreatingFamily(false);
    }
  };

  if (loading && families.length === 0) {
    return <Spin fullscreen />;
  }

  if (families.length === 0) {
    return (
      <Card style={{ maxWidth: 560, margin: "48px auto" }}>
        <Empty description={t("family.empty", "还没有家庭空间")} />
        <Form layout="vertical" onFinish={createFamily}>
          <Form.Item
            name="name"
            label={t("family.name", "家庭名称")}
            rules={[{ required: true }]}
          >
            <Input placeholder={t("family.namePlaceholder", "例如：幸福之家")} />
          </Form.Item>
          <Button type="primary" htmlType="submit" loading={creatingFamily} block>
            {t("family.create", "创建家庭")}
          </Button>
        </Form>
      </Card>
    );
  }

  const listCard = <T extends { id: string }>(
    rows: T[],
    title: (row: T) => React.ReactNode,
    description?: (row: T) => React.ReactNode,
  ) => (
    <List
      dataSource={rows}
      locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} /> }}
      renderItem={(row) => (
        <List.Item>
          <List.Item.Meta title={title(row)} description={description?.(row)} />
        </List.Item>
      )}
    />
  );

  return (
    <div style={{ maxWidth: 1180, margin: "0 auto", paddingBottom: 32 }}>
      <Space
        align="start"
        style={{ width: "100%", justifyContent: "space-between" }}
      >
        <div>
          <Title level={2} style={{ marginBottom: 4 }}>
            {t("family.title", "家庭中心")}
          </Title>
          <Paragraph type="secondary">
            {t("family.subtitle", "管理家庭成员、相册、任务与共同记忆")}
          </Paragraph>
        </div>
        <Select
          value={familyId}
          style={{ minWidth: 180 }}
          options={families.map((family) => ({
            value: family.id,
            label: family.name,
          }))}
          onChange={setFamilyId}
        />
      </Space>

      <Tabs
        items={[
          {
            key: "home",
            label: t("family.tabs.home", "首页"),
            children: (
              <>
                <Title level={3}>{activeFamily?.name}</Title>
                <Space wrap size={16}>
                  <Card><Statistic title={t("family.members", "成员")} value={members.length} /></Card>
                  <Card><Statistic title={t("family.albums", "相册")} value={albums.length} /></Card>
                  <Card><Statistic title={t("family.tasks", "任务")} value={tasks.length} /></Card>
                  <Card><Statistic title={t("family.memories", "记忆")} value={memories.length} /></Card>
                </Space>
              </>
            ),
          },
          {
            key: "members",
            label: t("family.tabs.members", "成员"),
            children: (
              <Card>
                <Form
                  layout="inline"
                  onFinish={async (values: { display_name: string }) => {
                    await homeMindFamilyApi.createMember(familyId, {
                      display_name: values.display_name,
                      role: "MEMBER",
                    });
                    await loadFamilyData();
                  }}
                >
                  <Form.Item name="display_name" rules={[{ required: true }]}>
                    <Input placeholder={t("family.memberName", "成员姓名")} />
                  </Form.Item>
                  <Button icon={<Plus size={16} />} type="primary" htmlType="submit">
                    {t("common.add", "添加")}
                  </Button>
                </Form>
                {listCard(members, (row) => row.display_name, (row) => <Tag>{row.role}</Tag>)}
              </Card>
            ),
          },
          {
            key: "albums",
            label: t("family.tabs.albums", "相册"),
            children: (
              <Card>
                <Form
                  layout="inline"
                  onFinish={async (values: { name: string }) => {
                    await homeMindFamilyApi.createAlbum(familyId, values);
                    await loadFamilyData();
                  }}
                >
                  <Form.Item name="name" rules={[{ required: true }]}>
                    <Input placeholder={t("family.albumName", "相册名称")} />
                  </Form.Item>
                  <Button icon={<Plus size={16} />} type="primary" htmlType="submit">
                    {t("common.create", "创建")}
                  </Button>
                </Form>
                {listCard(albums, (row) => row.name, (row) => row.description)}
              </Card>
            ),
          },
          {
            key: "tasks",
            label: t("family.tabs.tasks", "任务"),
            children: (
              <Card>
                <Form
                  layout="inline"
                  onFinish={async (values: { title: string }) => {
                    await homeMindFamilyApi.createTask(familyId, values);
                    await loadFamilyData();
                  }}
                >
                  <Form.Item name="title" rules={[{ required: true }]}>
                    <Input placeholder={t("family.taskTitle", "要完成的事情")} />
                  </Form.Item>
                  <Button icon={<Plus size={16} />} type="primary" htmlType="submit">
                    {t("common.create", "创建")}
                  </Button>
                </Form>
                {listCard(tasks, (row) => row.title, (row) => <Tag>{row.status}</Tag>)}
              </Card>
            ),
          },
          {
            key: "memories",
            label: t("family.tabs.memories", "家庭记忆"),
            children: (
              <Card>
                <Form
                  layout="inline"
                  onFinish={async (values: { content: string }) => {
                    await homeMindFamilyApi.createMemory(familyId, values.content);
                    await loadFamilyData();
                  }}
                >
                  <Form.Item name="content" rules={[{ required: true }]} style={{ flex: 1 }}>
                    <Input placeholder={t("family.memoryContent", "记录一件家庭共同记忆")} />
                  </Form.Item>
                  <Button icon={<Plus size={16} />} type="primary" htmlType="submit">
                    {t("common.add", "添加")}
                  </Button>
                </Form>
                {listCard(memories, (row) => row.content, (row) => <Tag>{row.memory_type}</Tag>)}
              </Card>
            ),
          },
          {
            key: "search",
            label: t("family.tabs.search", "统一搜索"),
            children: (
              <Card>
                <Input.Search
                  enterButton={<Search size={16} />}
                  placeholder={t("family.searchPlaceholder", "搜索成员、事件、记忆和资产")}
                  onSearch={async (query) => setResults(await homeMindFamilyApi.search(familyId, query))}
                />
                {listCard(results, (row) => row.title, (row) => (
                  <Space direction="vertical" size={2}>
                    <Tag>{row.kind}</Tag>
                    <Text type="secondary">{row.snippet}</Text>
                  </Space>
                ))}
              </Card>
            ),
          },
        ]}
      />
    </div>
  );
}
