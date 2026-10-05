import { useCallback, useEffect, useMemo, useState } from "react";
import {
  App,
  Button,
  Card,
  DatePicker,
  Empty,
  Form,
  Image,
  Input,
  List,
  Modal,
  Popconfirm,
  Select,
  Space,
  Spin,
  Statistic,
  Tabs,
  Tag,
  Typography,
} from "antd";
import { Pencil, Plus, Search, Settings, Trash2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useServerTimezone } from "../../hooks/useServerTimezone";
import PageShell from "../../layouts/PageShell";
import { formatServerDateTime } from "../../utils/formatMessageTime";
import AccessPanel from "./AccessPanel";
import AssetsPanel from "./AssetsPanel";
import FileManagerPanel from "./FileManagerPanel";
import GovernancePanel from "./GovernancePanel";
import PhotoIntelligencePanel from "./PhotoIntelligencePanel";
import TaskPanel from "./TaskPanel";

import {
  homeMindFamilyApi,
  type FamilyAlbum,
  type FamilyAsset,
  type FamilyEvent,
  type FamilyMember,
  type FamilyMemory,
  type FamilyRelationship,
  type FamilySearchResult,
  type FamilyTask,
  type HomeMindFamily,
} from "../../api/modules/homeMindFamily";
import styles from "./index.module.less";

const { Text, Title } = Typography;

function AlbumAsset({
  familyId,
  asset,
}: {
  familyId: string;
  asset: FamilyAsset;
}) {
  const [src, setSrc] = useState<string>();

  useEffect(() => {
    if (asset.asset_type !== "PHOTO") return;
    let objectUrl: string | undefined;
    let cancelled = false;
    void homeMindFamilyApi
      .getAssetContent(familyId, asset.id)
      .then((blob) => {
        if (cancelled) return;
        objectUrl = URL.createObjectURL(blob);
        setSrc(objectUrl);
      })
      .catch(() => {
        if (!cancelled) setSrc(undefined);
      });
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [asset.asset_type, asset.id, familyId]);

  if (asset.asset_type !== "PHOTO") return <>{asset.name}</>;
  return (
    <Space>
      <Image width={56} height={56} src={src} alt={asset.name} />
      <Text>{asset.name}</Text>
    </Space>
  );
}

export default function FamilyPage() {
  const { t } = useTranslation();
  const serverTimezone = useServerTimezone();
  const { message } = App.useApp();
  const [families, setFamilies] = useState<HomeMindFamily[]>([]);
  const [familyId, setFamilyId] = useState("");
  const [members, setMembers] = useState<FamilyMember[]>([]);
  const [albums, setAlbums] = useState<FamilyAlbum[]>([]);
  const [tasks, setTasks] = useState<FamilyTask[]>([]);
  const [memories, setMemories] = useState<FamilyMemory[]>([]);
  const [relationships, setRelationships] = useState<FamilyRelationship[]>([]);
  const [events, setEvents] = useState<FamilyEvent[]>([]);
  const [assets, setAssets] = useState<FamilyAsset[]>([]);
  const [albumAssetIds, setAlbumAssetIds] = useState<Record<string, string[]>>(
    {},
  );
  const [results, setResults] = useState<FamilySearchResult[]>([]);
  const [loading, setLoading] = useState(true);
  const [creatingFamily, setCreatingFamily] = useState(false);
  const [familyEditorOpen, setFamilyEditorOpen] = useState(false);
  const [editingMember, setEditingMember] = useState<FamilyMember | null>(null);
  const [familyForm] = Form.useForm();
  const [memberForm] = Form.useForm();

  const loadFamilies = useCallback(async () => {
    setLoading(true);
    try {
      const rows = await homeMindFamilyApi.listFamilies();
      setFamilies(rows);
      setFamilyId((current) =>
        current && rows.some((row) => row.id === current)
          ? current
          : rows[0]?.id ?? "",
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
      const [
        nextMembers,
        nextAlbums,
        nextTasks,
        nextMemories,
        nextRelationships,
        nextEvents,
        nextAssets,
      ] = await Promise.all([
        homeMindFamilyApi.listMembers(familyId),
        homeMindFamilyApi.listAlbums(familyId),
        homeMindFamilyApi.listTasks(familyId),
        homeMindFamilyApi.listMemories(familyId),
        homeMindFamilyApi.listRelationships(familyId),
        homeMindFamilyApi.listEvents(familyId),
        homeMindFamilyApi.listAssets(familyId),
      ]);
      setMembers(nextMembers);
      setAlbums(nextAlbums);
      setTasks(nextTasks);
      setMemories(nextMemories);
      setRelationships(nextRelationships);
      setEvents(nextEvents);
      setAssets(nextAssets);
      const entries = await Promise.all(
        nextAlbums.map(
          async (album) =>
            [
              album.id,
              await homeMindFamilyApi.listAlbumAssets(familyId, album.id),
            ] as const,
        ),
      );
      setAlbumAssetIds(Object.fromEntries(entries));
    } catch (error) {
      message.error(error instanceof Error ? error.message : String(error));
    } finally {
      setLoading(false);
    }
  }, [familyId, message]);

  const refreshAssets = useCallback(async () => {
    if (!familyId) return;
    setAssets(await homeMindFamilyApi.listAssets(familyId));
  }, [familyId]);

  useEffect(() => void loadFamilies(), [loadFamilies]);
  useEffect(() => void loadFamilyData(), [loadFamilyData]);

  const activeFamily = useMemo(
    () => families.find((family) => family.id === familyId),
    [families, familyId],
  );

  const memberName = (memberId: string) =>
    members.find((member) => member.id === memberId)?.display_name ?? memberId;

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
    return (
      <PageShell
        title={t("family.title", "家庭中心")}
        subtitle={t("family.subtitle", "管理家庭成员、相册、任务与共同记忆")}
      >
        <div className={styles.loading}>
          <Spin />
        </div>
      </PageShell>
    );
  }

  if (families.length === 0) {
    return (
      <PageShell
        title={t("family.title", "家庭中心")}
        subtitle={t("family.subtitle", "管理家庭成员、相册、任务与共同记忆")}
      >
        <div className={styles.emptyWrap}>
          <Card className={styles.emptyCard}>
            <Empty description={t("family.empty", "还没有家庭空间")} />
            <Form layout="vertical" onFinish={createFamily}>
              <Form.Item
                name="name"
                label={t("family.name", "家庭名称")}
                rules={[{ required: true }]}
              >
                <Input
                  placeholder={t("family.namePlaceholder", "例如：幸福之家")}
                />
              </Form.Item>
              <Button
                type="primary"
                htmlType="submit"
                loading={creatingFamily}
                block
              >
                {t("family.create", "创建家庭")}
              </Button>
            </Form>
          </Card>
        </div>
      </PageShell>
    );
  }

  const listCard = <T extends { id: string }>(
    rows: T[],
    title: (row: T) => React.ReactNode,
    description?: (row: T) => React.ReactNode,
  ) => (
    <List
      className={styles.dataList}
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
    <>
      <PageShell.FillTabs
        title={t("family.title", "家庭中心")}
        subtitle={t("family.subtitle", "管理家庭成员、相册、任务与共同记忆")}
        actions={
          <Space>
            <Select
              value={familyId}
              className={styles.familySelect}
              options={families.map((family) => ({
                value: family.id,
                label: family.name,
              }))}
              onChange={setFamilyId}
            />
            <Button
              icon={<Settings size={16} />}
              onClick={() => {
                familyForm.setFieldsValue(activeFamily);
                setFamilyEditorOpen(true);
              }}
            >
              {t("family.manage", "管理家庭")}
            </Button>
          </Space>
        }
      >
        <Tabs
          className={styles.tabs}
          tabPosition="top"
          items={[
            {
              key: "home",
              label: t("family.tabs.home", "首页"),
              children: (
                <div className={styles.overview}>
                  <Title level={4} className={styles.familyName}>
                    {activeFamily?.name}
                  </Title>
                  <div className={styles.statsGrid}>
                    <Card className={styles.statCard}>
                      <Statistic
                        title={t("family.members", "成员")}
                        value={members.length}
                      />
                    </Card>
                    <Card className={styles.statCard}>
                      <Statistic
                        title={t("family.albums", "相册")}
                        value={albums.length}
                      />
                    </Card>
                    <Card className={styles.statCard}>
                      <Statistic
                        title={t("family.tasks", "任务")}
                        value={tasks.length}
                      />
                    </Card>
                    <Card className={styles.statCard}>
                      <Statistic
                        title={t("family.memories", "记忆")}
                        value={memories.length}
                      />
                    </Card>
                  </div>
                </div>
              ),
            },
            {
              key: "members",
              label: t("family.tabs.members", "成员"),
              children: (
                <Card className={styles.panelCard}>
                  <Form
                    className={styles.quickForm}
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
                    <Button
                      icon={<Plus size={16} />}
                      type="primary"
                      htmlType="submit"
                    >
                      {t("common.add", "添加")}
                    </Button>
                  </Form>
                  <List
                    className={styles.dataList}
                    dataSource={members}
                    locale={{
                      emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} />,
                    }}
                    renderItem={(row) => (
                      <List.Item
                        actions={[
                          <Button
                            key="edit"
                            type="text"
                            icon={<Pencil size={16} />}
                            onClick={() => {
                              memberForm.setFieldsValue(row);
                              setEditingMember(row);
                            }}
                          />,
                          <Popconfirm
                            key="delete"
                            title={t(
                              "family.deleteMemberConfirm",
                              "确定删除该成员吗？",
                            )}
                            onConfirm={async () => {
                              await homeMindFamilyApi.deleteMember(
                                familyId,
                                row.id,
                              );
                              await loadFamilyData();
                            }}
                          >
                            <Button
                              type="text"
                              danger
                              icon={<Trash2 size={16} />}
                            />
                          </Popconfirm>,
                        ]}
                      >
                        <List.Item.Meta
                          title={row.display_name}
                          description={
                            <Space>
                              <Tag>{row.role}</Tag>
                              {row.birthday && (
                                <Text type="secondary">{row.birthday}</Text>
                              )}
                            </Space>
                          }
                        />
                      </List.Item>
                    )}
                  />
                  <Title level={5}>
                    {t("family.relationships", "成员关系")}
                  </Title>
                  <Form
                    className={styles.quickForm}
                    layout="inline"
                    onFinish={async (values) => {
                      await homeMindFamilyApi.createRelationship(
                        familyId,
                        values,
                      );
                      await loadFamilyData();
                    }}
                  >
                    <Form.Item
                      name="from_member_id"
                      rules={[{ required: true }]}
                    >
                      <Select
                        placeholder={t("family.fromMember", "成员")}
                        className={styles.inlineSelect}
                        options={members.map((member) => ({
                          value: member.id,
                          label: member.display_name,
                        }))}
                      />
                    </Form.Item>
                    <Form.Item
                      name="relationship_type"
                      rules={[{ required: true }]}
                    >
                      <Select
                        placeholder={t("family.relationship", "关系")}
                        className={styles.inlineSelect}
                        options={[
                          "SPOUSE",
                          "PARENT",
                          "CHILD",
                          "SIBLING",
                          "GRANDPARENT",
                          "GRANDCHILD",
                          "OTHER",
                        ].map((value) => ({ value, label: value }))}
                      />
                    </Form.Item>
                    <Form.Item name="to_member_id" rules={[{ required: true }]}>
                      <Select
                        placeholder={t("family.toMember", "关联成员")}
                        className={styles.inlineSelect}
                        options={members.map((member) => ({
                          value: member.id,
                          label: member.display_name,
                        }))}
                      />
                    </Form.Item>
                    <Button
                      type="primary"
                      htmlType="submit"
                      icon={<Plus size={16} />}
                    >
                      {t("common.add", "添加")}
                    </Button>
                  </Form>
                  <List
                    size="small"
                    dataSource={relationships}
                    renderItem={(row) => (
                      <List.Item
                        actions={[
                          <Popconfirm
                            key="delete"
                            title={t("common.confirmDelete", "确定删除吗？")}
                            onConfirm={async () => {
                              await homeMindFamilyApi.deleteRelationship(
                                familyId,
                                row.id,
                              );
                              await loadFamilyData();
                            }}
                          >
                            <Button
                              type="text"
                              danger
                              icon={<Trash2 size={15} />}
                            />
                          </Popconfirm>,
                        ]}
                      >
                        {memberName(row.from_member_id)}{" "}
                        <Tag>{row.relationship_type}</Tag>{" "}
                        {memberName(row.to_member_id)}
                      </List.Item>
                    )}
                  />
                </Card>
              ),
            },
            {
              key: "albums",
              label: t("family.tabs.albums", "相册"),
              children: (
                <Card className={styles.panelCard}>
                  <Form
                    className={styles.quickForm}
                    layout="inline"
                    onFinish={async (values: { name: string }) => {
                      await homeMindFamilyApi.createAlbum(familyId, values);
                      await loadFamilyData();
                    }}
                  >
                    <Form.Item name="name" rules={[{ required: true }]}>
                      <Input placeholder={t("family.albumName", "相册名称")} />
                    </Form.Item>
                    <Button
                      icon={<Plus size={16} />}
                      type="primary"
                      htmlType="submit"
                    >
                      {t("common.create", "创建")}
                    </Button>
                  </Form>
                  <div className={styles.albumGrid}>
                    {albums.map((album) => {
                      const selectedIds = albumAssetIds[album.id] ?? [];
                      return (
                        <Card
                          key={album.id}
                          size="small"
                          title={album.name}
                          extra={
                            <Text type="secondary">{selectedIds.length}</Text>
                          }
                        >
                          {album.description && (
                            <Typography.Paragraph type="secondary">
                              {album.description}
                            </Typography.Paragraph>
                          )}
                          <Select
                            className={styles.assetPicker}
                            placeholder={t(
                              "family.addIndexedAsset",
                              "添加已索引照片或文件",
                            )}
                            showSearch
                            optionFilterProp="label"
                            options={assets
                              .filter(
                                (asset) => !selectedIds.includes(asset.id),
                              )
                              .map((asset) => ({
                                value: asset.id,
                                label: asset.name,
                              }))}
                            onSelect={async (assetId: string) => {
                              await homeMindFamilyApi.addAlbumAsset(
                                familyId,
                                album.id,
                                assetId,
                              );
                              await loadFamilyData();
                            }}
                          />
                          <List
                            size="small"
                            dataSource={selectedIds}
                            locale={{
                              emptyText: t(
                                "family.albumEmpty",
                                "相册中还没有内容",
                              ),
                            }}
                            renderItem={(assetId) => {
                              const asset = assets.find(
                                (item) => item.id === assetId,
                              );
                              return (
                                <List.Item
                                  actions={[
                                    <Button
                                      key="remove"
                                      type="text"
                                      danger
                                      icon={<Trash2 size={14} />}
                                      onClick={async () => {
                                        await homeMindFamilyApi.removeAlbumAsset(
                                          familyId,
                                          album.id,
                                          assetId,
                                        );
                                        await loadFamilyData();
                                      }}
                                    />,
                                  ]}
                                >
                                  {asset ? (
                                    <AlbumAsset
                                      familyId={familyId}
                                      asset={asset}
                                    />
                                  ) : (
                                    assetId
                                  )}
                                </List.Item>
                              );
                            }}
                          />
                        </Card>
                      );
                    })}
                  </div>
                </Card>
              ),
            },
            {
              key: "tasks",
              label: t("family.tabs.tasks", "任务"),
              children: (
                <TaskPanel
                  familyId={familyId}
                  members={members}
                  tasks={tasks}
                  timezone={serverTimezone}
                  reload={loadFamilyData}
                />
              ),
            },
            {
              key: "events",
              label: t("family.tabs.events", "家庭日程"),
              children: (
                <Card className={styles.panelCard}>
                  <Form
                    className={styles.quickForm}
                    layout="inline"
                    onFinish={async (values: {
                      title: string;
                      range: [{ unix: () => number }, { unix: () => number }];
                      location?: string;
                    }) => {
                      await homeMindFamilyApi.createEvent(familyId, {
                        event_type: "FAMILY",
                        title: values.title,
                        start_at: values.range[0].unix(),
                        end_at: values.range[1].unix(),
                        location: values.location,
                      });
                      await loadFamilyData();
                    }}
                  >
                    <Form.Item name="title" rules={[{ required: true }]}>
                      <Input placeholder={t("family.eventTitle", "日程标题")} />
                    </Form.Item>
                    <Form.Item name="range" rules={[{ required: true }]}>
                      <DatePicker.RangePicker showTime />
                    </Form.Item>
                    <Form.Item name="location">
                      <Input placeholder={t("family.location", "地点")} />
                    </Form.Item>
                    <Button
                      type="primary"
                      htmlType="submit"
                      icon={<Plus size={16} />}
                    >
                      {t("common.create", "创建")}
                    </Button>
                  </Form>
                  <List
                    className={styles.dataList}
                    dataSource={events}
                    locale={{
                      emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} />,
                    }}
                    renderItem={(row) => (
                      <List.Item
                        actions={[
                          <Popconfirm
                            key="delete"
                            title={t("common.confirmDelete", "确定删除吗？")}
                            onConfirm={async () => {
                              await homeMindFamilyApi.deleteEvent(
                                familyId,
                                row.id,
                              );
                              await loadFamilyData();
                            }}
                          >
                            <Button
                              type="text"
                              danger
                              icon={<Trash2 size={16} />}
                            />
                          </Popconfirm>,
                        ]}
                      >
                        <List.Item.Meta
                          title={row.title}
                          description={
                            <Space wrap>
                              <Text>
                                {formatServerDateTime(
                                  row.start_at,
                                  serverTimezone,
                                )}{" "}
                                –{" "}
                                {formatServerDateTime(
                                  row.end_at,
                                  serverTimezone,
                                )}
                              </Text>
                              {row.location && <Tag>{row.location}</Tag>}
                            </Space>
                          }
                        />
                      </List.Item>
                    )}
                  />
                </Card>
              ),
            },
            {
              key: "memories",
              label: t("family.tabs.memories", "家庭记忆"),
              children: (
                <Card className={styles.panelCard}>
                  <Form
                    className={styles.quickForm}
                    layout="inline"
                    onFinish={async (values: { content: string }) => {
                      await homeMindFamilyApi.createMemory(
                        familyId,
                        values.content,
                      );
                      await loadFamilyData();
                    }}
                  >
                    <Form.Item
                      name="content"
                      rules={[{ required: true }]}
                      style={{ flex: 1 }}
                    >
                      <Input
                        placeholder={t(
                          "family.memoryContent",
                          "记录一件家庭共同记忆",
                        )}
                      />
                    </Form.Item>
                    <Button
                      icon={<Plus size={16} />}
                      type="primary"
                      htmlType="submit"
                    >
                      {t("common.add", "添加")}
                    </Button>
                  </Form>
                  {listCard(
                    memories,
                    (row) => row.content,
                    (row) => (
                      <Tag>{row.memory_type}</Tag>
                    ),
                  )}
                </Card>
              ),
            },
            {
              key: "assets",
              label: t("family.tabs.assets", "家庭资产"),
              children: (
                <AssetsPanel
                  familyId={familyId}
                  onAssetsChanged={refreshAssets}
                />
              ),
            },
            {
              key: "access",
              label: t("family.tabs.access", "空间与权限"),
              children: <AccessPanel familyId={familyId} members={members} />,
            },
            {
              key: "photos",
              label: t("family.tabs.photos", "照片智能"),
              children: (
                <PhotoIntelligencePanel familyId={familyId} members={members} />
              ),
            },
            {
              key: "files",
              label: t("family.tabs.files", "家庭文件"),
              children: <FileManagerPanel familyId={familyId} />,
            },
            {
              key: "governance",
              label: t("family.tabs.governance", "审批与审计"),
              children: <GovernancePanel familyId={familyId} />,
            },
            {
              key: "search",
              label: t("family.tabs.search", "统一搜索"),
              children: (
                <Card className={styles.panelCard}>
                  <Input.Search
                    enterButton={<Search size={16} />}
                    placeholder={t(
                      "family.searchPlaceholder",
                      "搜索成员、事件、记忆和资产",
                    )}
                    onSearch={async (query) =>
                      setResults(
                        await homeMindFamilyApi.search(familyId, query),
                      )
                    }
                  />
                  {listCard(
                    results,
                    (row) => row.title,
                    (row) => (
                      <Space direction="vertical" size={2}>
                        <Tag>{row.kind}</Tag>
                        <Text type="secondary">{row.snippet}</Text>
                      </Space>
                    ),
                  )}
                </Card>
              ),
            },
          ]}
        />
      </PageShell.FillTabs>
      <Modal
        title={t("family.manage", "管理家庭")}
        open={familyEditorOpen}
        okText={t("common.save", "保存")}
        onCancel={() => setFamilyEditorOpen(false)}
        onOk={() => familyForm.submit()}
        footer={(_, { OkBtn, CancelBtn }) => (
          <Space className={styles.modalFooter}>
            <Popconfirm
              title={t("family.deleteConfirm", "确定删除这个家庭吗？")}
              onConfirm={async () => {
                await homeMindFamilyApi.deleteFamily(familyId);
                setFamilyEditorOpen(false);
                await loadFamilies();
              }}
            >
              <Button danger icon={<Trash2 size={16} />}>
                {t("common.delete", "删除")}
              </Button>
            </Popconfirm>
            <Space>
              <CancelBtn />
              <OkBtn />
            </Space>
          </Space>
        )}
      >
        <Form
          form={familyForm}
          layout="vertical"
          onFinish={async (values) => {
            await homeMindFamilyApi.updateFamily(familyId, values);
            setFamilyEditorOpen(false);
            await loadFamilies();
          }}
        >
          <Form.Item
            name="name"
            label={t("family.name", "家庭名称")}
            rules={[{ required: true }]}
          >
            <Input />
          </Form.Item>
          <Form.Item
            name="timezone"
            label={t("family.timezone", "时区")}
            rules={[{ required: true }]}
          >
            <Input />
          </Form.Item>
          <Form.Item name="locale" label={t("family.locale", "语言")}>
            <Select
              options={[
                { value: "zh", label: "中文" },
                { value: "en", label: "English" },
              ]}
            />
          </Form.Item>
        </Form>
      </Modal>
      <Modal
        title={t("family.editMember", "编辑成员")}
        open={editingMember !== null}
        onCancel={() => setEditingMember(null)}
        onOk={() => memberForm.submit()}
      >
        <Form
          form={memberForm}
          layout="vertical"
          onFinish={async (values) => {
            if (!editingMember) return;
            await homeMindFamilyApi.updateMember(
              familyId,
              editingMember.id,
              values,
            );
            setEditingMember(null);
            await loadFamilyData();
          }}
        >
          <Form.Item
            name="display_name"
            label={t("family.memberName", "成员姓名")}
            rules={[{ required: true }]}
          >
            <Input />
          </Form.Item>
          <Form.Item name="role" label={t("family.role", "角色")}>
            <Select
              options={["ADMIN", "MEMBER", "CHILD", "GUEST"].map((value) => ({
                value,
                label: value,
              }))}
            />
          </Form.Item>
          <Form.Item name="birthday" label={t("family.birthday", "生日")}>
            <Input placeholder="YYYY-MM-DD" />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}
