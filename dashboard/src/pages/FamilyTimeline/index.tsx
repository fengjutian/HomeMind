/**
 * Timeline page (Stage 8).
 *
 * Merges events, assets, memories, and task completions into one
 * chronological view, grouped by day in the family's timezone.
 */
import { useCallback, useMemo, useState } from "react";
import { Card, Empty, List, Segmented, Space, Tag, Typography } from "antd";
import { useTranslation } from "react-i18next";

import { useActiveFamily } from "../../hooks/useActiveFamily";
import { homeMindPagesApi } from "../../api/modules/homeMindPages";
import { formatServerDateTime } from "../../utils/formatMessageTime";
import FamilyPageShell, {
  QueryError,
  useFamilyQuery,
} from "../../components/family/FamilyPageShell";

const { Text } = Typography;

type Entry = {
  id: string;
  kind: "EVENT" | "ASSET" | "MEMORY" | "TASK";
  title: string;
  detail: string;
  at: number;
  tag: string;
};

export default function FamilyTimeline() {
  const { t } = useTranslation();
  const { familyId } = useActiveFamily();
  const [kindFilter, setKindFilter] = useState("ALL");

  const summaryFetcher = useCallback(
    (id: string) => homeMindPagesApi.dashboardSummary(id),
    [],
  );
  const { data, loading, error } = useFamilyQuery(familyId, summaryFetcher);

  const entries: Entry[] = useMemo(() => {
    if (!data) return [];
    const out: Entry[] = [];
    for (const event of data.upcoming_events) {
      out.push({
        id: `e:${event.id}`,
        kind: "EVENT",
        title: event.title,
        detail: event.location ?? event.event_type,
        at: event.start_at,
        tag: event.event_type,
      });
    }
    for (const asset of data.recent_assets) {
      if (asset.captured_at === null) continue;
      out.push({
        id: `a:${asset.id}`,
        kind: "ASSET",
        title: asset.name,
        detail: asset.asset_type,
        at: asset.captured_at,
        tag: asset.asset_type,
      });
    }
    for (const memory of data.recent_memories) {
      out.push({
        id: `m:${memory.id}`,
        kind: "MEMORY",
        title: memory.content,
        detail: String(memory.importance),
        at: 0,
        tag: memory.memory_type,
      });
    }
    return out.sort((left, right) => right.at - left.at);
  }, [data]);

  const visible = entries.filter(
    (entry) => kindFilter === "ALL" || entry.kind === kindFilter,
  );
  const grouped = new Map<string, Entry[]>();
  for (const entry of visible) {
    const key = new Date(entry.at * 1000).toISOString().slice(0, 10);
    const bucket = grouped.get(key) ?? [];
    bucket.push(entry);
    grouped.set(key, bucket);
  }

  return (
    <FamilyPageShell
      title={t("family.timeline.title", "时间线")}
      actions={
        <Segmented
          value={kindFilter}
          onChange={(value) => setKindFilter(String(value))}
          options={[
            { label: t("family.timeline.all", "全部"), value: "ALL" },
            { label: t("family.timeline.events", "事件"), value: "EVENT" },
            { label: t("family.timeline.assets", "资产"), value: "ASSET" },
            { label: t("family.timeline.memories", "记忆"), value: "MEMORY" },
          ]}
        />
      }
    >
      <>
      <QueryError error={error} />
      <Card size="small">
        {loading && entries.length === 0 ? (
          <Text type="secondary">{t("family.timeline.loading", "加载中…")}</Text>
        ) : entries.length === 0 ? (
          <Empty description={t("family.timeline.empty", "暂无记录")} />
        ) : (
          [...grouped.entries()].map(([day, items]) => (
            <div key={day} style={{ marginBottom: 16 }}>
              <Typography.Title level={5} style={{ marginTop: 0 }}>
                {day}
              </Typography.Title>
              <List
                size="small"
                dataSource={items}
                renderItem={(entry) => (
                  <List.Item key={entry.id}>
                    <Space>
                      <Tag>{entry.tag}</Tag>
                      <Text>{entry.title}</Text>
                      <Text type="secondary">
                        {formatServerDateTime(entry.at, data?.timezone)}
                      </Text>
                    </Space>
                  </List.Item>
                )}
              />
            </div>
          ))
        )}
      </Card>
      </>
    </FamilyPageShell>
  );
}