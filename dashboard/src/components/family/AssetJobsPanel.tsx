/**
 * Persistent asset-job console (Stage D).
 *
 * Reused by the Photos and Files pages rather than duplicated: a job is
 * the same object whichever page you start it from, and two copies of
 * this table would drift within a week.
 *
 * Two things this component is deliberate about:
 *
 * - **A job is created from registered assets, not typed paths.** The
 *   form offers the asset-scoped types with a picker; only SCAN may name
 *   a source. That mirrors the API contract rather than working around
 *   it, so a user cannot queue work the backend will refuse.
 * - **Provider-backed jobs show their provider fields.** A VISION job
 *   without a model is rejected at creation time, so the form asks for
 *   it instead of letting the server return a 400 later.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  App,
  Button,
  Card,
  Drawer,
  Form,
  Input,
  InputNumber,
  Modal,
  Progress,
  Segmented,
  Select,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
} from "antd";
import { useTranslation } from "react-i18next";

import { useActiveFamily } from "../../hooks/useActiveFamily";
import {
  ASSET_JOB_TYPES,
  ASSET_SCOPED_JOB_TYPES,
  JOB_STATUS_COLORS,
  JOB_STATUS_LABELS,
  PROVIDER_BACKED_JOB_TYPES,
  homeMindPagesApi,
  type AssetJob,
  type AssetJobItem,
} from "../../api/modules/homeMindPages";
import { homeMindFamilyApi, type FamilyAsset } from "../../api/modules/homeMindFamily";

const { Text } = Typography;

const ITEM_STATUS_COLORS: Record<string, string> = {
  SUCCEEDED: "success",
  SKIPPED: "default",
  FAILED: "error",
  PENDING: "processing",
};

export interface AssetJobsPanelProps {
  /** Preselects the asset scope, e.g. the current photo selection. */
  defaultAssetIds?: string[];
  /** Hide the "new job" button on pages that own their own trigger. */
  showCreate?: boolean;
}

export default function AssetJobsPanel({
  defaultAssetIds,
  showCreate = true,
}: AssetJobsPanelProps) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const { familyId } = useActiveFamily();
  const [filter, setFilter] = useState<string>("ALL");
  const [jobs, setJobs] = useState<AssetJob[]>([]);
  const [assets, setAssets] = useState<FamilyAsset[]>([]);
  const [loading, setLoading] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [detail, setDetail] = useState<AssetJob | null>(null);
  const [items, setItems] = useState<AssetJobItem[]>([]);
  const [itemFilter, setItemFilter] = useState<string>("FAILED");
  const [form] = Form.useForm();
  const jobType = Form.useWatch("job_type", form);

  const reload = useCallback(async () => {
    if (!familyId) return;
    setLoading(true);
    try {
      const [jobList, assetList] = await Promise.all([
        homeMindPagesApi.listAssetJobs(familyId),
        homeMindFamilyApi.listAssets(familyId),
      ]);
      setJobs(jobList);
      setAssets(assetList);
    } catch (error) {
      message.error(error instanceof Error ? error.message : String(error));
    } finally {
      setLoading(false);
    }
  }, [familyId, message]);

  useEffect(() => {
    void reload();
  }, [reload]);

  const openDetail = useCallback(
    async (job: AssetJob) => {
      setDetail(job);
      try {
        setItems(
          await homeMindPagesApi.listAssetJobItems(familyId ?? "", job.job_id, {
            status: itemFilter === "ALL" ? undefined : itemFilter,
            limit: 200,
          }),
        );
      } catch (error) {
        message.error(error instanceof Error ? error.message : String(error));
      }
    },
    [familyId, itemFilter, message],
  );

  const act = useCallback(
    async (
      label: string,
      run: (id: string) => Promise<unknown>,
    ): Promise<void> => {
      if (!familyId) return;
      try {
        await run(familyId);
        message.success(label);
        await reload();
      } catch (error) {
        message.error(error instanceof Error ? error.message : String(error));
      }
    },
    [familyId, message, reload],
  );

  const visible = useMemo(
    () => jobs.filter((job) => filter === "ALL" || job.status === filter),
    [jobs, filter],
  );

  const needsProvider = PROVIDER_BACKED_JOB_TYPES.includes(String(jobType));
  const isAssetScoped = ASSET_SCOPED_JOB_TYPES.includes(String(jobType));

  const submit = useCallback(async () => {
    if (!familyId) return;
    const values = await form.validateFields();
    const body: Parameters<typeof homeMindPagesApi.createAssetJob>[1] = {
      job_type: values.job_type,
    };
    if (isAssetScoped) {
      body.asset_ids = values.asset_ids ?? [];
    }
    if (values.source_id) body.source_id = values.source_id;
    if (needsProvider) {
      body.config = {
        vision_provider_id: values.vision_provider_id,
        vision_model: values.vision_model,
        embedding_provider_id: values.embedding_provider_id,
        embedding_model: values.embedding_model,
      };
    }
    await act(t("family.jobsCreated", "任务已提交"), (id) =>
      homeMindPagesApi.createAssetJob(id, body),
    );
    setCreateOpen(false);
    form.resetFields();
  }, [act, form, isAssetScoped, needsProvider, t]);

  return (
    <>
      <Card
        size="small"
        title={t("family.jobsTitle", "后台任务")}
        extra={
          <Space>
            <Segmented
              size="small"
              value={filter}
              onChange={(value) => setFilter(String(value))}
              options={[
                { label: t("family.jobsAll", "全部"), value: "ALL" },
                { label: JOB_STATUS_LABELS.RUNNING ?? "进行中", value: "RUNNING" },
                { label: JOB_STATUS_LABELS.COMPLETED ?? "已完成", value: "COMPLETED" },
                { label: JOB_STATUS_LABELS.FAILED ?? "失败", value: "FAILED" },
              ]}
            />
            {showCreate ? (
              <Button size="small" type="primary" onClick={() => setCreateOpen(true)}>
                {t("family.jobsNew", "新建任务")}
              </Button>
            ) : null}
          </Space>
        }
      >
        <Table<AssetJob>
          size="small"
          rowKey="job_id"
          loading={loading}
          pagination={false}
          dataSource={visible}
          locale={{ emptyText: t("family.jobsEmpty", "暂无后台任务") }}
          columns={[
            {
              title: t("family.jobsType", "类型"),
              dataIndex: "job_type",
              render: (value: string) => <Tag>{value}</Tag>,
            },
            {
              title: t("family.jobsStatus", "状态"),
              dataIndex: "status",
              render: (value: string) => (
                <Tag color={JOB_STATUS_COLORS[value] ?? "default"}>
                  {JOB_STATUS_LABELS[value] ?? value}
                </Tag>
              ),
            },
            {
              title: t("family.jobsProgress", "进度"),
              width: 200,
              render: (_value, row) => (
                <Progress
                  percent={row.progress_percent}
                  size="small"
                  status={row.status === "FAILED" ? "exception" : undefined}
                />
              ),
            },
            {
              title: t("family.jobsCounts", "成功/跳过/失败"),
              width: 140,
              render: (_value, row) => (
                <Space size={4}>
                  <Text type="success">{row.succeeded_items}</Text>
                  <Text type="secondary">{row.skipped_items}</Text>
                  <Text type="danger">{row.failed_items}</Text>
                </Space>
              ),
            },
            {
              title: t("family.jobsActions", "操作"),
              width: 280,
              render: (_value, row) => (
                <Space size={4} wrap>
                  <Button size="small" onClick={() => openDetail(row)}>
                    {t("family.jobsDetails", "详情")}
                  </Button>
                  <Button
                    size="small"
                    disabled={row.status !== "PENDING" && row.status !== "RUNNING"}
                    onClick={() =>
                      act(t("family.jobsPaused", "已暂停"), (id) =>
                        homeMindPagesApi.pauseAssetJob(id, row.job_id),
                      )
                    }
                  >
                    {t("family.jobsPause", "暂停")}
                  </Button>
                  <Button
                    size="small"
                    disabled={row.status !== "PAUSED"}
                    onClick={() =>
                      act(t("family.jobsResumed", "已恢复"), (id) =>
                        homeMindPagesApi.resumeAssetJob(id, row.job_id),
                      )
                    }
                  >
                    {t("family.jobsResume", "恢复")}
                  </Button>
                  <Button
                    size="small"
                    onClick={() =>
                      act(t("family.jobsRetried", "已重试失败项"), (id) =>
                        homeMindPagesApi.retryAssetJob(id, row.job_id),
                      )
                    }
                  >
                    {t("family.jobsRetry", "重试失败")}
                  </Button>
                  <Tooltip
                    title={
                      row.failed_items > 0
                        ? t("family.jobsRetryHint", "只重跑失败项，已成功的不会重复执行")
                        : ""
                    }
                  >
                    <Button
                      size="small"
                      danger
                      disabled={
                        row.status === "COMPLETED" ||
                        row.status === "CANCELLED" ||
                        row.status === "FAILED"
                      }
                      onClick={() =>
                        act(t("family.jobsCancelled", "已取消"), (id) =>
                          homeMindPagesApi.cancelAssetJob(id, row.job_id),
                        )
                      }
                    >
                      {t("family.jobsCancel", "取消")}
                    </Button>
                  </Tooltip>
                </Space>
              ),
            },
          ]}
        />
      </Card>

      <Modal
        open={createOpen}
        title={t("family.jobsNew", "新建任务")}
        onCancel={() => setCreateOpen(false)}
        onOk={() => void submit()}
        okText={t("family.jobsSubmit", "提交")}
        cancelText={t("family.jobsCancel", "取消")}
      >
        <Form
          form={form}
          layout="vertical"
          initialValues={{ job_type: "REINDEX", asset_ids: defaultAssetIds }}
        >
          <Form.Item
            name="job_type"
            label={t("family.jobsType", "类型")}
            rules={[{ required: true }]}
          >
            <Select options={ASSET_JOB_TYPES.map((value) => ({ value, label: value }))} />
          </Form.Item>
          {isAssetScoped ? (
            <Form.Item
              name="asset_ids"
              label={t("family.jobsAssetScope", "目标资产")}
              rules={[
                {
                  required: true,
                  message: t(
                    "family.jobsAssetScopeRequired",
                    "请选择至少一个已登记资产",
                  ),
                },
              ]}
              extra={t(
                "family.jobsAssetScopeHint",
                "只能处理已登记的家庭资产；填写任意本地路径的服务端会拒绝",
              )}
            >
              <Select
                mode="multiple"
                showSearch
                optionFilterProp="label"
                options={assets.map((asset) => ({
                  value: asset.id,
                  label: asset.name,
                }))}
              />
            </Form.Item>
          ) : (
            <Form.Item
              name="source_id"
              label={t("family.jobsSource", "资产来源")}
              rules={[
                {
                  required: true,
                  message: t("family.jobsSourceRequired", "扫描任务需要选择来源"),
                },
              ]}
            >
              <Input
                placeholder={t("family.jobsSourcePlaceholder", "来源 ID")}
              />
            </Form.Item>
          )}
          {needsProvider ? (
            <>
              <Form.Item
                name="vision_provider_id"
                label={t("family.jobsVisionProvider", "视觉 Provider ID")}
              >
                <InputNumber min={1} style={{ width: "100%" }} />
              </Form.Item>
              <Form.Item
                name="vision_model"
                label={t("family.jobsVisionModel", "视觉模型")}
              >
                <Input />
              </Form.Item>
              <Form.Item
                name="embedding_provider_id"
                label={t("family.jobsEmbeddingProvider", "向量 Provider ID")}
              >
                <InputNumber min={1} style={{ width: "100%" }} />
              </Form.Item>
              <Form.Item
                name="embedding_model"
                label={t("family.jobsEmbeddingModel", "向量模型")}
              >
                <Input />
              </Form.Item>
            </>
          ) : null}
        </Form>
      </Modal>

      <Drawer
        open={detail !== null}
        onClose={() => setDetail(null)}
        title={
          detail
            ? `${detail.job_type} · ${JOB_STATUS_LABELS[detail.status] ?? detail.status}`
            : ""
        }
        extra={
          <Segmented
            size="small"
            value={itemFilter}
            onChange={(value) => {
              const next = String(value);
              setItemFilter(next);
              if (detail) void openDetail(detail);
            }}
            options={[
              { label: t("family.jobsFailedItems", "失败项"), value: "FAILED" },
              { label: t("family.jobsAllItems", "全部"), value: "ALL" },
              { label: t("family.jobsSkippedItems", "跳过"), value: "SKIPPED" },
            ]}
          />
        }
      >
        {detail?.error_summary ? (
          <Text type="danger" style={{ display: "block", marginBottom: 8 }}>
            {detail.error_summary}
          </Text>
        ) : null}
        <Table<AssetJobItem>
          size="small"
          rowKey="item_id"
          pagination={{ pageSize: 20 }}
          dataSource={items}
          columns={[
            {
              title: t("family.jobsItemStatus", "状态"),
              dataIndex: "status",
              width: 100,
              render: (value: string) => (
                <Tag color={ITEM_STATUS_COLORS[value] ?? "default"}>{value}</Tag>
              ),
            },
            {
              title: t("family.jobsItemSource", "来源"),
              dataIndex: "source_path",
              ellipsis: true,
            },
            {
              title: t("family.jobsItemError", "错误"),
              dataIndex: "error",
              ellipsis: true,
              render: (value: string | null) =>
                value ? <Text type="danger">{value}</Text> : null,
            },
          ]}
        />
      </Drawer>
    </>
  );
}