import { useCallback, useEffect, useMemo, useState } from "react";
import {
  App,
  Breadcrumb,
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
  Typography,
} from "antd";
import {
  Copy,
  File,
  Folder,
  FolderOpen,
  Move,
  Pencil,
  Trash2,
} from "lucide-react";
import { useTranslation } from "react-i18next";

import {
  homeMindFamilyApi,
  type FamilyAssetSource,
  type FamilyFilesystemEntry,
} from "../../api/modules/homeMindFamily";
import styles from "./index.module.less";

interface FileManagerPanelProps {
  familyId: string;
}

type MutationAction =
  | "filesystem.copy"
  | "filesystem.move"
  | "filesystem.rename";

const parentPath = (path: string) => {
  const parts = path.split("/").filter(Boolean);
  parts.pop();
  return parts.join("/") || ".";
};

const formatBytes = (bytes: number | null) => {
  if (bytes === null) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
};

export default function FileManagerPanel({ familyId }: FileManagerPanelProps) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const [sources, setSources] = useState<FamilyAssetSource[]>([]);
  const [sourceId, setSourceId] = useState<string>();
  const [path, setPath] = useState(".");
  const [entries, setEntries] = useState<FamilyFilesystemEntry[]>([]);
  const [preview, setPreview] = useState<{
    path: string;
    content: string;
  } | null>(null);
  const [mutation, setMutation] = useState<{
    entry: FamilyFilesystemEntry;
    action: MutationAction;
  } | null>(null);
  const [mutationForm] = Form.useForm();

  useEffect(() => {
    void homeMindFamilyApi
      .listAssetSources(familyId)
      .then((rows) => {
        setSources(rows);
        setSourceId((current) =>
          current && rows.some((row) => row.id === current)
            ? current
            : rows[0]?.id,
        );
      })
      .catch((error) =>
        message.error(error instanceof Error ? error.message : String(error)),
      );
  }, [familyId, message]);

  const load = useCallback(async () => {
    if (!sourceId) {
      setEntries([]);
      return;
    }
    try {
      setEntries(
        await homeMindFamilyApi.listDirectory(familyId, sourceId, path),
      );
    } catch (error) {
      message.error(error instanceof Error ? error.message : String(error));
    }
  }, [familyId, message, path, sourceId]);

  useEffect(() => void load(), [load]);

  const breadcrumbs = useMemo(() => {
    const parts = path === "." ? [] : path.split("/").filter(Boolean);
    return [
      {
        title: (
          <Button type="link" size="small" onClick={() => setPath(".")}>
            {t("family.rootDirectory", "根目录")}
          </Button>
        ),
      },
      ...parts.map((part, index) => ({
        title: (
          <Button
            type="link"
            size="small"
            onClick={() => setPath(parts.slice(0, index + 1).join("/"))}
          >
            {part}
          </Button>
        ),
      })),
    ];
  }, [path, t]);

  const reportMutation = async (
    result: Awaited<ReturnType<typeof homeMindFamilyApi.mutateFile>>,
  ) => {
    if (result.approval) {
      message.info(
        t("family.awaitingApproval", "操作已提交，等待家庭管理员审批"),
      );
    } else if (result.transaction.status === "COMPLETED") {
      message.success(t("family.operationComplete", "操作已完成"));
      await load();
    } else {
      message.warning(result.transaction.error ?? result.transaction.status);
    }
  };

  if (sources.length === 0) {
    return (
      <Card className={styles.panelCard}>
        <Empty
          description={t(
            "family.noAssetSources",
            "请先在家庭资产中扫描一个本地目录",
          )}
        />
      </Card>
    );
  }

  return (
    <Card
      className={styles.fileManagerCard}
      title={t("family.fileManager", "家庭文件")}
    >
      <div className={styles.fileToolbar}>
        <Select
          className={styles.sourceSelect}
          value={sourceId}
          options={sources.map((source) => ({
            value: source.id,
            label: source.directory_uri,
          }))}
          onChange={(value) => {
            setSourceId(value);
            setPath(".");
          }}
        />
        <Input.Search
          allowClear
          placeholder={t("family.searchCurrentSource", "搜索当前目录源")}
          onSearch={async (query) => {
            if (!sourceId) return;
            setEntries(
              query
                ? await homeMindFamilyApi.searchDirectory(
                    familyId,
                    sourceId,
                    query,
                    path,
                  )
                : await homeMindFamilyApi.listDirectory(
                    familyId,
                    sourceId,
                    path,
                  ),
            );
          }}
        />
      </div>
      <Breadcrumb className={styles.fileBreadcrumb} items={breadcrumbs} />
      {path !== "." && (
        <Button
          type="text"
          icon={<FolderOpen size={16} />}
          onClick={() => setPath(parentPath(path))}
        >
          {t("family.parentDirectory", "返回上级")}
        </Button>
      )}
      <List
        dataSource={entries}
        locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} /> }}
        renderItem={(entry) => (
          <List.Item
            actions={
              entry.kind === "file"
                ? [
                    <Button
                      key="read"
                      type="text"
                      onClick={async () => {
                        if (sourceId)
                          setPreview(
                            await homeMindFamilyApi.readFile(
                              familyId,
                              sourceId,
                              entry.path,
                            ),
                          );
                      }}
                    >
                      {t("family.previewText", "预览文本")}
                    </Button>,
                    <Button
                      key="copy"
                      type="text"
                      icon={<Copy size={15} />}
                      onClick={() => {
                        mutationForm.setFieldValue("destination", entry.path);
                        setMutation({ entry, action: "filesystem.copy" });
                      }}
                    >
                      {t("family.copy", "复制")}
                    </Button>,
                    <Button
                      key="move"
                      type="text"
                      icon={<Move size={15} />}
                      onClick={() => {
                        mutationForm.setFieldValue("destination", entry.path);
                        setMutation({ entry, action: "filesystem.move" });
                      }}
                    >
                      {t("family.move", "移动")}
                    </Button>,
                    <Button
                      key="rename"
                      type="text"
                      icon={<Pencil size={15} />}
                      onClick={() => {
                        mutationForm.setFieldValue("destination", entry.path);
                        setMutation({ entry, action: "filesystem.rename" });
                      }}
                    >
                      {t("family.rename", "重命名")}
                    </Button>,
                    <Popconfirm
                      key="delete"
                      title={t(
                        "family.safeDeleteConfirm",
                        "文件将移入 HomeMind 回收区，是否继续？",
                      )}
                      onConfirm={async () => {
                        if (sourceId)
                          await reportMutation(
                            await homeMindFamilyApi.mutateFile(familyId, {
                              action: "filesystem.delete",
                              source_id: sourceId,
                              path: entry.path,
                            }),
                          );
                      }}
                    >
                      <Button type="text" danger icon={<Trash2 size={15} />} />
                    </Popconfirm>,
                  ]
                : undefined
            }
          >
            <List.Item.Meta
              avatar={
                entry.kind === "directory" ? (
                  <Folder size={20} />
                ) : (
                  <File size={20} />
                )
              }
              title={
                <Button
                  type="link"
                  disabled={entry.kind !== "directory"}
                  onClick={() =>
                    entry.kind === "directory" && setPath(entry.path)
                  }
                >
                  {entry.path.split("/").pop()}
                </Button>
              }
              description={formatBytes(entry.size_bytes)}
            />
          </List.Item>
        )}
      />

      <Modal
        title={preview?.path}
        open={preview !== null}
        footer={null}
        width={760}
        onCancel={() => setPreview(null)}
      >
        <Typography.Paragraph className={styles.filePreview} copyable>
          {preview?.content}
        </Typography.Paragraph>
      </Modal>

      <Modal
        title={t(
          `family.${mutation?.action.split(".")[1] ?? "move"}`,
          "文件操作",
        )}
        open={mutation !== null}
        onCancel={() => setMutation(null)}
        onOk={() => mutationForm.submit()}
      >
        <Form
          form={mutationForm}
          layout="vertical"
          onFinish={async ({ destination }) => {
            if (!mutation || !sourceId) return;
            const result = await homeMindFamilyApi.mutateFile(familyId, {
              action: mutation.action,
              source_id: sourceId,
              path: mutation.entry.path,
              destination,
            });
            setMutation(null);
            await reportMutation(result);
          }}
        >
          <Form.Item label={t("family.sourcePath", "源路径")}>
            <Input value={mutation?.entry.path} disabled />
          </Form.Item>
          <Form.Item
            name="destination"
            label={t("family.destinationPath", "目标路径")}
            rules={[{ required: true }]}
          >
            <Input />
          </Form.Item>
        </Form>
      </Modal>
    </Card>
  );
}
