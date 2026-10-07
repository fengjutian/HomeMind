/**
 * Files page (Stage 8).
 *
 * Source picker + breadcrumb + listing + safe delete. Every mutation
 * goes through the family transaction API, so a destructive action
 * lands in the approval queue rather than deleting immediately.
 */
import { useCallback, useState } from "react";
import {
  App,
  Breadcrumb,
  Button,
  Card,
  Empty,
  Input,
  Popconfirm,
  Select,
  Space,
  Table,
  Tag,
  Typography,
} from "antd";
import { useTranslation } from "react-i18next";

import { useActiveFamily } from "../../hooks/useActiveFamily";
import { homeMindFamilyApi } from "../../api/modules/homeMindFamily";
import FamilyPageShell, {
  QueryError,
  useFamilyQuery,
} from "../../components/family/FamilyPageShell";

const { Text } = Typography;

export default function FamilyFiles() {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const { familyId } = useActiveFamily();
  const [sourceId, setSourceId] = useState<string | null>(null);
  const [path, setPath] = useState(".");
  const [query, setQuery] = useState("");

  const sourcesFetcher = useCallback(
    (id: string) => homeMindFamilyApi.listAssetSources(id),
    [],
  );
  const sources = useFamilyQuery(familyId, sourcesFetcher);

  const entriesFetcher = useCallback(
    (id: string) => {
      if (sourceId === null) return Promise.resolve([]);
      return query.trim()
        ? homeMindFamilyApi.searchDirectory(id, sourceId, query.trim(), path)
        : homeMindFamilyApi.listDirectory(id, sourceId, path);
    },
    [sourceId, path, query],
  );
  const entries = useFamilyQuery(familyId, entriesFetcher);

  const mutate = async (
    label: string,
    run: (id: string) => Promise<unknown>,
  ) => {
    if (familyId === null) return;
    try {
      await run(familyId);
      message.success(label);
      entries.reload();
    } catch (error) {
      message.error(error instanceof Error ? error.message : String(error));
    }
  };

  const crumbs = path
    .split("/")
    .filter(Boolean)
    .map((segment, index, all) => ({
      title: segment,
      onClick: () => setPath(all.slice(0, index + 1).join("/")),
    }));

  return (
    <FamilyPageShell
      title={t("family.files.title", "文件")}
      actions={
        <Space>
          <Select
            style={{ minWidth: 200 }}
            placeholder={t("family.files.pickSource", "选择数据源")}
            value={sourceId}
            options={(sources.data ?? []).map((source) => ({
              label: source.directory_uri,
              value: source.id,
            }))}
            onChange={(next) => {
              setSourceId(next);
              setPath(".");
              setQuery("");
            }}
          />
          <Input.Search
            allowClear
            placeholder={t("family.files.search", "搜索")}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            style={{ width: 200 }}
          />
        </Space>
      }
    >
      <>
      <QueryError error={sources.error} />
      <Card size="small">
        <Breadcrumb
          items={[
            {
              title: t("family.files.root", "根目录"),
              onClick: () => setPath("."),
            },
            ...crumbs,
          ]}
        />
        <Table
          size="small"
          style={{ marginTop: 12 }}
          rowKey="path"
          pagination={false}
          dataSource={entries.data ?? []}
          locale={{ emptyText: <Empty description={t("family.files.empty", "暂无文件")} /> }}
          columns={[
            {
              title: t("family.files.name", "名称"),
              dataIndex: "path",
              render: (value: string, row) =>
                row.kind === "directory" ? (
                  <a onClick={() => setPath(value)}>{value}</a>
                ) : (
                  value
                ),
            },
            {
              title: t("family.files.size", "大小"),
              dataIndex: "size_bytes",
              width: 120,
              render: (value: number | null) =>
                value === null ? "-" : `${Math.round(value / 1024)} KB`,
            },
            {
              title: t("family.files.modified", "修改时间"),
              width: 200,
              render: (_value, row) => row.path,
            },
            {
              title: t("family.files.actions", "操作"),
              width: 140,
              render: (_value, row) =>
                row.kind === "directory" ? null : (
                  <Popconfirm
                    title={t("family.files.confirmDelete", "确认移入恢复站?")}
                    onConfirm={() => {
                      if (sourceId === null) return;
                      mutate("已提交删除申请", (id) =>
                        homeMindFamilyApi.mutateFile(id, {
                          action: "filesystem.delete",
                          source_id: sourceId,
                          path: row.path,
                        }),
                      );
                    }}
                  >
                    <Button size="small" danger>
                      {t("family.files.delete", "删除")}
                    </Button>
                  </Popconfirm>
                ),
            },
          ]}
        />
        {entries.loading ? (
          <Text type="secondary">{t("family.files.loading", "加载中…")}</Text>
        ) : null}
        <div style={{ marginTop: 12 }}>
          <Tag>{t("family.files.trashHint", "删除的文件会先进入恢复站")}</Tag>
        </div>
      </Card>
      </>
    </FamilyPageShell>
  );
}
