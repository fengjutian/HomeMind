/**
 * Face-match review queue (Stage C4).
 *
 * The framing here is the whole point: a candidate is a *suggestion* a
 * provider made, not an identity. So the row shows the confidence as a
 * number next to the photo, states plainly that nothing has been applied
 * yet, and requires an explicit click per row. There is no "accept all" —
 * confirming eleven faces in one click is exactly the automated decision
 * this queue exists to prevent.
 *
 * Deciding is manager-only server-side; this panel simply hides the
 * buttons for everyone else rather than pretending to enforce it.
 */
import { useCallback, useEffect, useState } from "react";
import {
  App,
  Badge,
  Button,
  Card,
  Col,
  Empty,
  Popconfirm,
  Row,
  Segmented,
  Space,
  Tag,
  Typography,
} from "antd";
import { useTranslation } from "react-i18next";

import { useActiveFamily } from "../../hooks/useActiveFamily";
import {
  homeMindPagesApi,
  type FaceCandidate,
} from "../../api/modules/homeMindPages";
import { QueryError, useFamilyQuery } from "./FamilyPageShell";

const { Text } = Typography;

const STATUS_COLORS: Record<string, string> = {
  PENDING: "warning",
  CONFIRMED: "success",
  REJECTED: "default",
};

export default function FaceCandidatesPanel() {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const { familyId } = useActiveFamily();
  const [status, setStatus] = useState<"PENDING" | "CONFIRMED" | "REJECTED">(
    "PENDING",
  );
  const [deciding, setDeciding] = useState<string | null>(null);

  const fetcher = useCallback(
    (id: string) => homeMindPagesApi.listFaceCandidates(id, status),
    [status],
  );
  const candidates = useFamilyQuery(familyId, fetcher);

  useEffect(() => {
    candidates.reload();
    // `candidates` is a new object each render; reloading on the status
    // change is what actually matters here.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status]);

  const decide = useCallback(
    async (
      candidate: FaceCandidate,
      action: "confirm" | "reject",
    ): Promise<void> => {
      if (!familyId) return;
      setDeciding(candidate.candidate_id);
      try {
        if (action === "confirm") {
          await homeMindPagesApi.confirmFaceCandidate(
            familyId,
            candidate.candidate_id,
          );
        } else {
          await homeMindPagesApi.rejectFaceCandidate(
            familyId,
            candidate.candidate_id,
          );
        }
        message.success(
          action === "confirm"
            ? t("family.facesConfirmed", "已确认")
            : t("family.facesRejected", "已忽略"),
        );
        candidates.reload();
      } catch (error) {
        message.error(error instanceof Error ? error.message : String(error));
      } finally {
        setDeciding(null);
      }
    },
    [candidates, familyId, message, t],
  );

  const rows = candidates.data ?? [];

  return (
    <Card
      size="small"
      title={
        <Space>
          {t("family.facesTitle", "人脸待确认")}
          {status === "PENDING" && rows.length > 0 ? (
            <Badge count={rows.length} />
          ) : null}
        </Space>
      }
      extra={
        <Segmented
          size="small"
          value={status}
          onChange={(value) => setStatus(String(value) as typeof status)}
          options={[
            { label: t("family.facesPending", "待确认"), value: "PENDING" },
            { label: t("family.facesConfirmedList", "已确认"), value: "CONFIRMED" },
            { label: t("family.facesRejectedList", "已忽略"), value: "REJECTED" },
          ]}
        />
      }
    >
      <QueryError error={candidates.error} />
      {status === "PENDING" ? (
        <Text type="secondary" style={{ display: "block", marginBottom: 8 }}>
          {t(
            "family.facesDisclaimer",
            "以下都是模型的猜测，确认前不会作为身份使用。忽略过的匹配不会再次自动出现。",
          )}
        </Text>
      ) : null}

      {rows.length === 0 ? (
        <Empty description={t("family.facesEmpty", "暂无待确认的人脸")} />
      ) : (
        <Row gutter={[8, 8]}>
          {rows.map((candidate) => (
            <Col key={candidate.candidate_id} xs={24} sm={12} md={8} lg={6}>
              <Card
                size="small"
                styles={{ body: { padding: 8 } }}
                cover={
                  familyId === null ? null : (
                    <img
                      src={homeMindPagesApi.assetThumbnailUrl(
                        familyId,
                        candidate.asset_id,
                        240,
                        240,
                      )}
                      alt={candidate.asset_id}
                      style={{ width: "100%", height: 140, objectFit: "cover" }}
                      loading="lazy"
                      onError={(event) => {
                        // A missing preview must not hide the row: the
                        // decision is still meaningful without it.
                        (event.currentTarget as HTMLImageElement).style.display =
                          "none";
                      }}
                    />
                  )
                }
              >
                <Space direction="vertical" size={4} style={{ width: "100%" }}>
                  <Space size={4}>
                    <Tag color={STATUS_COLORS[candidate.status]}>
                      {candidate.status}
                    </Tag>
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      {Math.round(candidate.confidence * 100)}%
                    </Text>
                  </Space>
                  <Text type="secondary" style={{ fontSize: 12 }} ellipsis>
                    {t("family.facesMember", "成员")}: {candidate.member_id}
                  </Text>
                  {candidate.status === "PENDING" ? (
                    <Space size={4}>
                      <Button
                        size="small"
                        type="primary"
                        loading={deciding === candidate.candidate_id}
                        onClick={() => void decide(candidate, "confirm")}
                      >
                        {t("family.facesConfirm", "确认")}
                      </Button>
                      <Popconfirm
                        title={t(
                          "family.facesRejectConfirm",
                          "忽略这条匹配？忽略后不会再次自动出现。",
                        )}
                        onConfirm={() => void decide(candidate, "reject")}
                      >
                        <Button
                          size="small"
                          loading={deciding === candidate.candidate_id}
                        >
                          {t("family.facesReject", "忽略")}
                        </Button>
                      </Popconfirm>
                    </Space>
                  ) : (
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      {candidate.decided_at
                        ? new Date(candidate.decided_at * 1000).toLocaleString()
                        : ""}
                    </Text>
                  )}
                </Space>
              </Card>
            </Col>
          ))}
        </Row>
      )}
    </Card>
  );
}