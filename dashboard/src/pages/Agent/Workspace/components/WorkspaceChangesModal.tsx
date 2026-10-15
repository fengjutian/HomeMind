import { useCallback, useEffect, useMemo, useState } from "react";
import { Alert, Input, List, Modal, Select, Space, Spin, Tag } from "antd";
import { useTranslation } from "react-i18next";
import {
  workspaceApi,
  type WorkspaceChange,
} from "../../../../api/modules/workspace";
import { useServerTimezone } from "../../../../hooks/useServerTimezone";
import { formatServerDateTime } from "../../../../utils/formatMessageTime";

interface Props {
  agentId: string;
  open: boolean;
  onClose: () => void;
}

export default function WorkspaceChangesModal({
  agentId,
  open,
  onClose,
}: Props) {
  const { t } = useTranslation();
  const timeZone = useServerTimezone();
  const [items, setItems] = useState<WorkspaceChange[]>([]);
  const [loading, setLoading] = useState(false);
  const [path, setPath] = useState("");
  const [operation, setOperation] = useState<string>();
  const [source, setSource] = useState<string>();
  const [selected, setSelected] = useState<WorkspaceChange>();

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const result = await workspaceApi.listWorkspaceChanges(agentId, {
        path: path || undefined,
        operation,
        source,
      });
      setItems(result.items);
    } finally {
      setLoading(false);
    }
  }, [agentId, operation, path, source]);

  useEffect(() => {
    if (open) void load();
  }, [load, open]);

  const sources = useMemo(
    () =>
      [...new Set(items.map((item) => item.source))].map((value) => ({
        value,
      })),
    [items],
  );

  return (
    <>
      <Modal
        title={t("workspace.audit.title")}
        open={open}
        onCancel={onClose}
        footer={null}
        width={900}
      >
        <Space wrap style={{ marginBottom: 12 }}>
          <Input.Search
            allowClear
            value={path}
            onChange={(event) => setPath(event.target.value)}
            onSearch={() => void load()}
            placeholder={t("workspace.audit.pathFilter")}
          />
          <Select
            allowClear
            value={operation}
            onChange={setOperation}
            placeholder={t("workspace.audit.operation")}
            options={["create", "overwrite", "delete", "move", "mkdir"].map(
              (value) => ({ value }),
            )}
          />
          <Select
            allowClear
            value={source}
            onChange={setSource}
            placeholder={t("workspace.audit.source")}
            options={sources}
          />
        </Space>
        {loading ? (
          <Spin />
        ) : (
          <List
            dataSource={items}
            locale={{ emptyText: t("workspace.audit.empty") }}
            renderItem={(item) => (
              <List.Item
                onClick={() => {
                  void workspaceApi
                    .getWorkspaceChange(agentId, item.change_id)
                    .then(setSelected);
                }}
                style={{ cursor: "pointer" }}
              >
                <List.Item.Meta
                  title={
                    <Space>
                      <Tag>{item.operation}</Tag>
                      <span>{item.path}</span>
                    </Space>
                  }
                  description={`${formatServerDateTime(item.ts, timeZone)} · ${
                    item.source
                  } · ${item.status}`}
                />
              </List.Item>
            )}
          />
        )}
      </Modal>
      <Modal
        title={selected?.path}
        open={!!selected}
        onCancel={() => setSelected(undefined)}
        footer={null}
        width={900}
      >
        {selected?.error_message && (
          <Alert type="error" message={selected.error_message} />
        )}
        {selected?.diff_text ? (
          <pre
            style={{
              overflow: "auto",
              maxHeight: "65vh",
              whiteSpace: "pre-wrap",
            }}
          >
            {selected.diff_text}
          </pre>
        ) : (
          <Alert type="info" message={t("workspace.audit.noDiff")} />
        )}
      </Modal>
    </>
  );
}
