/**
 * Photos page (Stage 8).
 *
 * Grid + thumbnail + filters + batch actions. Thumbnails come from the
 * Stage 6 cache endpoint, so the grid never pulls full-resolution
 * files; a failed thumbnail falls back to the file name rather than a
 * broken image.
 */
import { useCallback, useMemo, useState } from "react";
import {
  App,
  Button,
  Card,
  Col,
  Empty,
  Progress,
  Row,
  Segmented,
  Space,
  Table,
  Tag,
  Typography,
} from "antd";
import { useTranslation } from "react-i18next";

import { useActiveFamily } from "../../hooks/useActiveFamily";
import {
  homeMindPagesApi,
  JOB_STATUS_COLORS,
  JOB_STATUS_LABELS,
  type AssetJob,
} from "../../api/modules/homeMindPages";
import { homeMindFamilyApi } from "../../api/modules/homeMindFamily";
import FamilyPageShell, {
  QueryError,
  useFamilyQuery,
} from "../../components/family/FamilyPageShell";

const { Text } = Typography;

export default function FamilyPhotos() {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const { familyId } = useActiveFamily();
  const [jobFilter, setJobFilter] = useState<string>("ALL");

  const jobsFetcher = useCallback(
    (id: string) => homeMindPagesApi.listAssetJobs(id),
    [],
  );
  const assetsFetcher = useCallback(
    (id: string) => homeMindFamilyApi.listAssets(id),
    [],
  );
  const jobs = useFamilyQuery(familyId, jobsFetcher);
  const assets = useFamilyQuery(familyId, assetsFetcher);

  const visibleJobs = useMemo(
    () =>
      (jobs.data ?? []).filter(
        (job) => jobFilter === "ALL" || job.status === jobFilter,
      ),
    [jobs.data, jobFilter],
  );

  const act = async (
    label: string,
    run: (id: string) => Promise<unknown>,
  ) => {
    if (familyId === null) return;
    try {
      await run(familyId);
      message.success(label);
      jobs.reload();
    } catch (error) {
      message.error(error instanceof Error ? error.message : String(error));
    }
  };

  return (
    <FamilyPageShell
      title={t("family.photos.title", "照片")}
      actions={
        <Button
          type="primary"
          onClick={() =>
            act("已提交 AI 分析任务", (id) =>
              homeMindPagesApi.createAssetJob(id, { job_type: "VISION" }),
            )
          }
        >
          {t("family.photos.analyze", "提交 AI 分析")}
        </Button>
      }
    >
      <>
      <QueryError error={jobs.error} />

      <Card
        size="small"
        title={t("family.photos.jobs", "后台任务")}
        extra={
          <Segmented
            size="small"
            value={jobFilter}
            onChange={(value) => setJobFilter(String(value))}
            options={[
              { label: t("family.photos.all", "全部"), value: "ALL" },
              { label: JOB_STATUS_LABELS.RUNNING ?? "进行中", value: "RUNNING" },
              { label: JOB_STATUS_LABELS.COMPLETED ?? "已完成", value: "COMPLETED" },
              { label: JOB_STATUS_LABELS.FAILED ?? "失败", value: "FAILED" },
            ]}
          />
        }
        style={{ marginBottom: 12 }}
      >
        <Table<AssetJob>
          size="small"
          rowKey="job_id"
          pagination={false}
          dataSource={visibleJobs}
          locale={{ emptyText: t("family.photos.noJobs", "暂无后台任务") }}
          columns={[
            {
              title: t("family.photos.jobType", "类型"),
              dataIndex: "job_type",
              render: (value: string) => <Tag>{value}</Tag>,
            },
            {
              title: t("family.photos.status", "状态"),
              dataIndex: "status",
              render: (value: string) => (
                <Tag color={JOB_STATUS_COLORS[value] ?? "default"}>
                  {JOB_STATUS_LABELS[value] ?? value}
                </Tag>
              ),
            },
            {
              title: t("family.photos.progress", "进度"),
              width: 220,
              render: (_value, row) => (
                <Progress
                  percent={row.progress_percent}
                  size="small"
                  status={
                    row.status === "FAILED" ? "exception" : undefined
                  }
                />
              ),
            },
            {
              title: t("family.photos.counts", "成功/跳过/失败"),
              width: 160,
              render: (_value, row) => (
                <Space size={4}>
                  <Text type="success">{row.succeeded_items}</Text>
                  <Text type="secondary">{row.skipped_items}</Text>
                  <Text type="danger">{row.failed_items}</Text>
                </Space>
              ),
            },
            {
              title: t("family.photos.actions", "操作"),
              width: 260,
              render: (_value, row) => (
                <Space size={4}>
                  <Button
                    size="small"
                    onClick={() =>
                      act("已暂停", (id) =>
                        homeMindPagesApi.pauseAssetJob(id, row.job_id),
                      )
                    }
                  >
                    {t("family.photos.pause", "暂停")}
                  </Button>
                  <Button
                    size="small"
                    onClick={() =>
                      act("已恢复", (id) =>
                        homeMindPagesApi.resumeAssetJob(id, row.job_id),
                      )
                    }
                  >
                    {t("family.photos.resume", "恢复")}
                  </Button>
                  <Button
                    size="small"
                    onClick={() =>
                      act("已重试失败项", (id) =>
                        homeMindPagesApi.retryAssetJob(id, row.job_id),
                      )
                    }
                  >
                    {t("family.photos.retryFailed", "重试失败")}
                  </Button>
                  <Button
                    size="small"
                    danger
                    onClick={() =>
                      act("已取消", (id) =>
                        homeMindPagesApi.cancelAssetJob(id, row.job_id),
                      )
                    }
                  >
                    {t("family.photos.cancel", "取消")}
                  </Button>
                </Space>
              ),
            },
          ]}
        />
      </Card>

      <Card size="small" title={t("family.photos.assets", "资产")}>
        <QueryError error={assets.error} />
        <Row gutter={[8, 8]}>
          {(assets.data ?? []).map((asset) => (
            <Col key={asset.id} xs={12} sm={8} md={6} lg={4} xl={3}>
              <Card
                size="small"
                styles={{ body: { padding: 8 } }}
                cover={
                  familyId === null ? null : (
                    <img
                      src={homeMindPagesApi.assetThumbnailUrl(familyId, asset.id)}
                      alt={asset.name}
                      style={{ width: "100%", height: 120, objectFit: "cover" }}
                      loading="lazy"
                      onError={(event) => {
                        // A thumbnail that cannot render must not leave a
                        // broken image in the grid.
                        (event.currentTarget as HTMLImageElement).style.display =
                          "none";
                      }}
                    />
                  )
                }
              >
                <Text ellipsis title={asset.name} style={{ display: "block" }}>
                  {asset.name}
                </Text>
                <Tag>{asset.asset_type}</Tag>
              </Card>
            </Col>
          ))}
        </Row>
        {(assets.data ?? []).length === 0 ? (
          <Empty description={t("family.photos.noAssets", "暂无资产")} />
        ) : null}
      </Card>
      </>
    </FamilyPageShell>
  );
}
