import { useCallback, useEffect, useState } from "react";
import {
  App,
  Button,
  Card,
  Checkbox,
  Descriptions,
  Empty,
  List,
  Modal,
  Popconfirm,
  Space,
  Tag,
  Upload,
} from "antd";
import { Download, FileArchive, Trash2, UploadCloud } from "lucide-react";

import {
  homeMindFamilyApi,
  type FamilyExportItem,
  type FamilyImportStage,
} from "../../api/modules/homeMindFamily";
import { useServerTimezone } from "../../hooks/useServerTimezone";
import { formatServerDateTime } from "../../utils/formatMessageTime";
import styles from "./index.module.less";

interface Props {
  familyId: string;
}

const saveBlob = (blob: Blob, filename: string) => {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
};

export default function ImportExportPanel({ familyId }: Props) {
  const { message } = App.useApp();
  const timezone = useServerTimezone();
  const [rows, setRows] = useState<FamilyExportItem[]>([]);
  const [includeAssets, setIncludeAssets] = useState(false);
  const [staged, setStaged] = useState<FamilyImportStage | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setRows(await homeMindFamilyApi.listExports(familyId));
    } catch (error) {
      message.error(error instanceof Error ? error.message : String(error));
    } finally {
      setLoading(false);
    }
  }, [familyId, message]);

  useEffect(() => void load(), [load]);

  return (
    <div className={styles.twoColumnGrid}>
      <Card className={styles.panelCard} title="家庭导出">
        <Space wrap style={{ marginBottom: 16 }}>
          <Checkbox
            checked={includeAssets}
            onChange={(event) => setIncludeAssets(event.target.checked)}
          >
            包含原始照片和文件
          </Checkbox>
          <Button
            type="primary"
            icon={<FileArchive size={16} />}
            onClick={async () => {
              await homeMindFamilyApi.createExport(familyId, includeAssets);
              message.success("导出已创建");
              await load();
            }}
          >
            创建 ZIP 导出
          </Button>
        </Space>
        <List
          loading={loading}
          dataSource={rows}
          locale={{
            emptyText: (
              <Empty
                image={Empty.PRESENTED_IMAGE_SIMPLE}
                description="暂无历史导出"
              />
            ),
          }}
          renderItem={(row) => (
            <List.Item
              actions={[
                <Button
                  key="download"
                  type="text"
                  icon={<Download size={15} />}
                  disabled={row.status !== "COMPLETED"}
                  onClick={async () =>
                    saveBlob(
                      await homeMindFamilyApi.downloadExport(
                        familyId,
                        row.export_id,
                      ),
                      `homemind-family-${row.export_id}.zip`,
                    )
                  }
                >
                  下载
                </Button>,
                <Popconfirm
                  key="delete"
                  title="删除这份导出？"
                  onConfirm={async () => {
                    await homeMindFamilyApi.deleteExport(
                      familyId,
                      row.export_id,
                    );
                    await load();
                  }}
                >
                  <Button type="text" danger icon={<Trash2 size={15} />}>
                    删除
                  </Button>
                </Popconfirm>,
              ]}
            >
              <List.Item.Meta
                title={
                  <Space>
                    <span>{row.export_id}</span>
                    <Tag>{row.status}</Tag>
                  </Space>
                }
                description={`${formatServerDateTime(
                  row.created_at,
                  timezone,
                )} · ${(row.byte_size / 1024 / 1024).toFixed(2)} MB${
                  row.includes_original_assets ? " · 含原始资产" : ""
                }`}
              />
            </List.Item>
          )}
        />
      </Card>
      <Card className={styles.panelCard} title="家庭导入">
        <Upload
          accept=".zip,application/zip"
          maxCount={1}
          showUploadList={false}
          customRequest={async ({ file, onError, onSuccess }) => {
            if (!(file instanceof File)) return;
            try {
              const result = await homeMindFamilyApi.stageImport(
                familyId,
                file,
              );
              setStaged(result);
              onSuccess?.(result);
            } catch (error) {
              const reason =
                error instanceof Error ? error : new Error(String(error));
              onError?.(reason);
              message.error(reason.message);
            }
          }}
        >
          <Button icon={<UploadCloud size={16} />}>上传 ZIP 并预检</Button>
        </Upload>
        <Empty
          style={{ marginTop: 36 }}
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description="上传导出包后，将先校验格式、校验和与数据冲突，不会直接写入家庭数据。"
        />
      </Card>
      <Modal
        title="导入预检结果"
        open={staged !== null}
        okText="确认导入"
        cancelText="拒绝并删除"
        okButtonProps={{ disabled: (staged?.conflicts.length ?? 0) > 0 }}
        onOk={async () => {
          if (!staged) return;
          await homeMindFamilyApi.applyImport(familyId, staged.import_id);
          message.success("导入完成");
          setStaged(null);
        }}
        onCancel={async () => {
          if (staged)
            await homeMindFamilyApi.rejectImport(familyId, staged.import_id);
          setStaged(null);
        }}
      >
        {staged && (
          <Descriptions
            column={1}
            bordered
            size="small"
            items={[
              {
                key: "format",
                label: "格式",
                children: `${staged.format} v${staged.version}`,
              },
              { key: "id", label: "导入 ID", children: staged.import_id },
              {
                key: "conflicts",
                label: "冲突",
                children:
                  staged.conflicts.length === 0
                    ? "未发现冲突，可以导入"
                    : `${staged.conflicts.length} 项冲突；解决冲突后才能导入`,
              },
            ]}
          />
        )}
        {(staged?.conflicts.length ?? 0) > 0 && (
          <pre className={styles.importConflicts}>
            {JSON.stringify(staged?.conflicts, null, 2)}
          </pre>
        )}
      </Modal>
    </div>
  );
}
