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
  Statistic,
  Tag,
  Typography,
} from "antd";
import { FolderSearch, RefreshCw, Search, Trash2 } from "lucide-react";
import { useTranslation } from "react-i18next";

import {
  homeMindFamilyApi,
  type FamilyAsset,
  type FamilyAssetScanResult,
  type FamilyAssetSource,
  type FamilySpace,
} from "../../api/modules/homeMindFamily";
import { formatServerDateTime } from "../../utils/formatMessageTime";
import { useServerTimezone } from "../../hooks/useServerTimezone";
import styles from "./index.module.less";

interface AssetsPanelProps {
  familyId: string;
}

const formatBytes = (bytes: number) => {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
  return `${(bytes / 1024 ** 3).toFixed(1)} GB`;
};

export default function AssetsPanel({ familyId }: AssetsPanelProps) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const timezone = useServerTimezone();
  const [assets, setAssets] = useState<FamilyAsset[]>([]);
  const [sources, setSources] = useState<FamilyAssetSource[]>([]);
  const [spaces, setSpaces] = useState<FamilySpace[]>([]);
  const [duplicates, setDuplicates] = useState<FamilyAsset[][]>([]);
  const [duplicateOpen, setDuplicateOpen] = useState(false);
  const [scanResult, setScanResult] = useState<FamilyAssetScanResult | null>(
    null,
  );
  const [scanning, setScanning] = useState(false);

  const load = useCallback(
    async (filters?: {
      query?: string;
      asset_type?: string;
      space_id?: string;
    }) => {
      try {
        const [nextAssets, nextSources, nextSpaces] = await Promise.all([
          homeMindFamilyApi.listAssets(familyId, filters),
          homeMindFamilyApi.listAssetSources(familyId),
          homeMindFamilyApi.listSpaces(familyId),
        ]);
        setAssets(nextAssets);
        setSources(nextSources);
        setSpaces(nextSpaces);
      } catch (error) {
        message.error(error instanceof Error ? error.message : String(error));
      }
    },
    [familyId, message],
  );

  useEffect(() => void load(), [load]);

  const showDuplicates = async () => {
    const groups = await homeMindFamilyApi.listDuplicateAssets(familyId);
    setDuplicates(groups);
    setDuplicateOpen(true);
  };

  return (
    <div className={styles.assetsLayout}>
      <Card
        title={t("family.assetSources", "资产目录")}
        className={styles.panelCard}
      >
        <Form
          className={styles.quickForm}
          layout="inline"
          initialValues={{ recursive: "true", visibility: "FAMILY" }}
          onFinish={async (values) => {
            setScanning(true);
            try {
              const result = await homeMindFamilyApi.scanAssets(familyId, {
                ...values,
                recursive: values.recursive === "true",
              });
              setScanResult(result);
              await load();
            } finally {
              setScanning(false);
            }
          }}
        >
          <Form.Item
            name="directory"
            rules={[{ required: true }]}
            style={{ flex: 1 }}
          >
            <Input
              placeholder={t(
                "family.directoryPath",
                "服务器本地目录，例如 D:\\Photos",
              )}
            />
          </Form.Item>
          <Form.Item name="space_id">
            <Select
              allowClear
              className={styles.inlineSelect}
              placeholder={t("family.space", "家庭空间")}
              options={spaces.map((space) => ({
                value: space.id,
                label: space.name,
              }))}
            />
          </Form.Item>
          <Form.Item name="visibility">
            <Select
              className={styles.inlineSelect}
              options={["FAMILY", "PRIVATE", "SENSITIVE", "PUBLIC"].map(
                (value) => ({ value, label: value }),
              )}
            />
          </Form.Item>
          <Form.Item name="recursive">
            <Select
              className={styles.smallSelect}
              options={[
                { value: "true", label: t("family.recursive", "包含子目录") },
                {
                  value: "false",
                  label: t("family.currentDirectory", "仅当前目录"),
                },
              ]}
            />
          </Form.Item>
          <Button
            type="primary"
            htmlType="submit"
            loading={scanning}
            icon={<FolderSearch size={16} />}
          >
            {t("family.scan", "扫描")}
          </Button>
        </Form>
        {scanResult && (
          <div className={styles.scanStats}>
            <Statistic
              title={t("family.indexed", "已索引")}
              value={scanResult.indexed}
            />
            <Statistic
              title={t("family.unchanged", "未变化")}
              value={scanResult.unchanged}
            />
            <Statistic
              title={t("family.skipped", "已跳过")}
              value={scanResult.skipped}
            />
            <Statistic
              title={t("family.failed", "失败")}
              value={scanResult.failed}
            />
          </div>
        )}
        <List
          size="small"
          dataSource={sources}
          locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} /> }}
          renderItem={(source) => (
            <List.Item
              actions={[
                <Button
                  key="rescan"
                  type="text"
                  icon={<RefreshCw size={15} />}
                  onClick={async () => {
                    setScanResult(
                      await homeMindFamilyApi.rescanAssetSource(
                        familyId,
                        source.id,
                      ),
                    );
                    await load();
                  }}
                >
                  {t("family.rescan", "重新扫描")}
                </Button>,
              ]}
            >
              <List.Item.Meta
                title={source.directory_uri}
                description={
                  <Space wrap>
                    <Tag>{source.visibility}</Tag>
                    {source.last_scanned_at && (
                      <Typography.Text type="secondary">
                        {formatServerDateTime(source.last_scanned_at, timezone)}
                      </Typography.Text>
                    )}
                  </Space>
                }
              />
            </List.Item>
          )}
        />
      </Card>

      <Card
        title={t("family.assets", "家庭资产")}
        className={styles.panelCard}
        extra={
          <Button onClick={showDuplicates}>
            {t("family.findDuplicates", "查找重复文件")}
          </Button>
        }
      >
        <Form className={styles.quickForm} layout="inline" onFinish={load}>
          <Form.Item name="query" style={{ flex: 1 }}>
            <Input
              prefix={<Search size={15} />}
              allowClear
              placeholder={t("family.searchAssets", "搜索文件名")}
            />
          </Form.Item>
          <Form.Item name="asset_type">
            <Select
              allowClear
              className={styles.inlineSelect}
              placeholder={t("family.assetType", "资产类型")}
              options={["PHOTO", "VIDEO", "DOCUMENT", "AUDIO", "OTHER"].map(
                (value) => ({ value, label: value }),
              )}
            />
          </Form.Item>
          <Form.Item name="space_id">
            <Select
              allowClear
              className={styles.inlineSelect}
              placeholder={t("family.space", "家庭空间")}
              options={spaces.map((space) => ({
                value: space.id,
                label: space.name,
              }))}
            />
          </Form.Item>
          <Button htmlType="submit">{t("common.search", "搜索")}</Button>
        </Form>
        <List
          dataSource={assets}
          locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} /> }}
          renderItem={(asset) => (
            <List.Item
              actions={[
                <Popconfirm
                  key="remove"
                  title={t(
                    "family.removeIndexConfirm",
                    "仅从索引移除，不删除原文件。是否继续？",
                  )}
                  onConfirm={async () => {
                    await homeMindFamilyApi.deleteAssetIndex(
                      familyId,
                      asset.id,
                    );
                    await load();
                  }}
                >
                  <Button type="text" danger icon={<Trash2 size={16} />}>
                    {t("family.removeIndex", "移除索引")}
                  </Button>
                </Popconfirm>,
              ]}
            >
              <List.Item.Meta
                title={asset.name}
                description={
                  <Space wrap>
                    <Tag>{asset.asset_type}</Tag>
                    <span>{formatBytes(asset.size_bytes)}</span>
                    <Typography.Text type="secondary" ellipsis>
                      {asset.uri}
                    </Typography.Text>
                  </Space>
                }
              />
            </List.Item>
          )}
        />
      </Card>

      <Modal
        title={t("family.duplicateAssets", "重复文件")}
        open={duplicateOpen}
        footer={null}
        onCancel={() => setDuplicateOpen(false)}
        width={720}
      >
        <List
          dataSource={duplicates}
          locale={{
            emptyText: (
              <Empty
                description={t("family.noDuplicates", "没有发现重复文件")}
              />
            ),
          }}
          renderItem={(group, index) => (
            <List.Item>
              <List.Item.Meta
                title={`${t("family.duplicateGroup", "重复组")} ${index + 1}`}
                description={group.map((asset) => (
                  <div key={asset.id}>
                    {asset.name} · {asset.uri}
                  </div>
                ))}
              />
            </List.Item>
          )}
        />
      </Modal>
    </div>
  );
}
