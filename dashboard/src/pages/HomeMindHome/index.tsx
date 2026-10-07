/**
 * HomeMind home page (Stage 8).
 *
 * One aggregate request feeds every card — a dozen parallel calls would
 * make first paint feel slow and could show half-updated sections.
 */
import { useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import {
  Card,
  Col,
  List,
  Progress,
  Row,
  Statistic,
  Tag,
  Typography,
} from "antd";
import {
  Camera,
  CalendarClock,
  CheckSquare,
  Cpu,
  HardDrive,
  ShieldCheck,
  Users,
} from "lucide-react";

import { useActiveFamily } from "../../hooks/useActiveFamily";
import {
  homeMindPagesApi,
  JOB_STATUS_COLORS,
  JOB_STATUS_LABELS,
} from "../../api/modules/homeMindPages";
import { formatServerDateTime } from "../../utils/formatMessageTime";
import FamilyPageShell, { QueryError, useFamilyQuery } from "../../components/family/FamilyPageShell";

const { Text } = Typography;

export default function HomeMindHome() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { familyId } = useActiveFamily();
  const fetcher = useCallback(
    (id: string) => homeMindPagesApi.dashboardSummary(id),
    [],
  );
  const { data, loading, error } = useFamilyQuery(familyId, fetcher);

  return (
    <FamilyPageShell
      title={t("family.home.title", "家庭")}
      subtitle={data?.family_name}
      actions={
        <Tag color="blue" icon={<Users size={14} />}>
          {t("family.home.members", "成员")}: {data?.member_count ?? 0}
        </Tag>
      }
    >
      <>
      <QueryError error={error} />
      {loading && !data ? null : (
        <>
          <Row gutter={[12, 12]}>
            <Col xs={12} sm={8} lg={4}>
              <Card>
                <Statistic
                  title={t("family.home.todayTasks", "今日任务")}
                  value={data?.today_task_count ?? 0}
                  prefix={<CheckSquare size={16} />}
                />
              </Card>
            </Col>
            <Col xs={12} sm={8} lg={4}>
              <Card>
                <Statistic
                  title={t("family.home.openTasks", "未完成任务")}
                  value={data?.open_task_count ?? 0}
                  prefix={<CheckSquare size={16} />}
                />
              </Card>
            </Col>
            <Col xs={12} sm={8} lg={4}>
              <Card>
                <Statistic
                  title={t("family.home.pendingApprovals", "待审批")}
                  value={data?.pending_approval_count ?? 0}
                  prefix={<ShieldCheck size={16} />}
                  valueStyle={
                    (data?.pending_approval_count ?? 0) > 0
                      ? { color: "#cf1322" }
                      : undefined
                  }
                />
              </Card>
            </Col>
            <Col xs={12} sm={8} lg={4}>
              <Card>
                <Statistic
                  title={t("family.home.pendingMemories", "待确认记忆")}
                  value={data?.pending_memory_candidate_count ?? 0}
                />
              </Card>
            </Col>
            <Col xs={12} sm={8} lg={4}>
              <Card>
                <Statistic
                  title={t("family.home.devicesOnline", "设备在线")}
                  value={data?.devices.online ?? 0}
                  suffix={`/ ${data?.devices.total ?? 0}`}
                  prefix={<Cpu size={16} />}
                />
              </Card>
            </Col>
            <Col xs={12} sm={8} lg={4}>
              <Card>
                <Statistic
                  title={t("family.home.recentAssets", "最近资产")}
                  value={data?.recent_assets.length ?? 0}
                  prefix={<HardDrive size={16} />}
                />
              </Card>
            </Col>
          </Row>

          <Row gutter={[12, 12]} style={{ marginTop: 12 }}>
            <Col xs={24} lg={8}>
              <Card
                title={t("family.home.upcomingEvents", "即将发生的事件")}
                extra={<CalendarClock size={16} />}
              >
                <List
                  size="small"
                  dataSource={data?.upcoming_events ?? []}
                  locale={{ emptyText: t("family.home.noEvents", "暂无事件") }}
                  renderItem={(item) => (
                    <List.Item key={item.id}>
                      <List.Item.Meta
                        title={item.title}
                        description={
                          <>
                            <Text type="secondary">
                              {formatServerDateTime(
                                item.start_at,
                                data?.timezone,
                              )}
                            </Text>
                            {item.location ? (
                              <Text type="secondary"> · {item.location}</Text>
                            ) : null}
                          </>
                        }
                      />
                    </List.Item>
                  )}
                />
              </Card>
            </Col>

            <Col xs={24} lg={8}>
              <Card
                title={t("family.home.recentMemories", "最近记忆")}
                extra={
                  <a onClick={() => navigate("/family-memory")}>
                    {t("family.home.viewAll", "查看全部")}
                  </a>
                }
              >
                <List
                  size="small"
                  dataSource={data?.recent_memories ?? []}
                  locale={{ emptyText: t("family.home.noMemories", "暂无记忆") }}
                  renderItem={(item) => (
                    <List.Item key={item.id}>
                      <List.Item.Meta
                        title={item.content}
                        description={
                          <Tag>{item.memory_type}</Tag>
                        }
                      />
                    </List.Item>
                  )}
                />
              </Card>
            </Col>

            <Col xs={24} lg={8}>
              <Card
                title={t("family.home.recentJobs", "最近后台任务")}
                extra={<Camera size={16} />}
              >
                <List
                  size="small"
                  dataSource={data?.recent_jobs ?? []}
                  locale={{ emptyText: t("family.home.noJobs", "暂无任务") }}
                  renderItem={(job) => (
                    <List.Item key={job.id}>
                      <List.Item.Meta
                        title={
                          <>
                            {job.job_type}{" "}
                            <Tag color={JOB_STATUS_COLORS[job.status] ?? "default"}>
                              {JOB_STATUS_LABELS[job.status] ?? job.status}
                            </Tag>
                          </>
                        }
                        description={
                          <Progress
                            percent={job.total_items > 0
                              ? Math.round((job.processed_items / job.total_items) * 100)
                              : 0}
                            size="small"
                          />
                        }
                      />
                    </List.Item>
                  )}
                />
              </Card>
            </Col>
          </Row>
        </>
      )}
      </>
    </FamilyPageShell>
  );
}
