/**
 * HomeMind calendar page (Stage 1).
 *
 * Three views over the same data:
 *
 * - **Month grid** for desktop, laid out from the occurrences the
 *   backend computed. The page never expands a recurrence rule itself —
 *   a weekly event is one row on the server, and this view renders what
 *   it is handed.
 * - **Week strip** — seven columns, same data, less noise.
 * - **Agenda** for narrow screens. At 360px a month grid is unreadable,
 *   so the agenda is not a degraded fallback but the layout a phone
 *   actually gets.
 *
 * Every time is rendered in the *server's* timezone via
 * `useServerTimezone` and the `formatMessageTime` helpers, never the
 * browser's: a family that travels should see the household's clock,
 * not whatever laptop they happen to be on.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  App,
  Badge,
  Button,
  Card,
  Col,
  DatePicker,
  Form,
  Input,
  Modal,
  Popconfirm,
  Row,
  Segmented,
  Select,
  Space,
  Switch,
  Tag,
  Typography,
} from "antd";
import dayjs, { type Dayjs } from "dayjs";
import { useTranslation } from "react-i18next";

import {
  homeMindCalendarApi,
  type FamilyCalendar,
  type FamilyCalendarEvent,
  type FamilyCalendarOccurrence,
} from "../../api/modules/homeMindCalendar";
import FamilyPageShell, {
  QueryError,
  useFamilyQuery,
} from "../../components/family/FamilyPageShell";
import { useActiveFamily } from "../../hooks/useActiveFamily";
import { useServerTimezone } from "../../hooks/useServerTimezone";
import {
  formatServerHourMinute,
  formatServerYmd,
} from "../../utils/formatMessageTime";

const { Text } = Typography;

type ViewMode = "MONTH" | "WEEK" | "AGENDA";

/**
 * Repeat choices, expressed as RFC 5545 RRULE.
 *
 * A fixed list rather than free text: the backend rejects natural
 * language anyway, and offering it here would just produce 400s.
 */
const RECURRENCE_OPTIONS: { value: string; labelKey: string }[] = [
  { value: "", labelKey: "familyCalendar.recurrenceNone" },
  { value: "FREQ=DAILY", labelKey: "familyCalendar.recurrenceDaily" },
  { value: "FREQ=WEEKLY", labelKey: "familyCalendar.recurrenceWeekly" },
  { value: "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR", labelKey: "familyCalendar.recurrenceWeekdays" },
  { value: "FREQ=MONTHLY", labelKey: "familyCalendar.recurrenceMonthly" },
];

export default function FamilyCalendar() {
  const { t } = useTranslation();
  const { familyId } = useActiveFamily();
  const timezone = useServerTimezone();
  const [anchor, setAnchor] = useState<Dayjs>(() => dayjs());
  const [view, setView] = useState<ViewMode>("MONTH");
  const [calendarId, setCalendarId] = useState<string | undefined>(undefined);
  const [editing, setEditing] = useState<FamilyCalendarOccurrence | null>(null);
  const [creating, setCreating] = useState(false);

  const calendarsFetcher = useCallback(
    (id: string) => homeMindCalendarApi.listCalendars(id),
    [],
  );
  const {
    data: calendars,
    error: calendarError,
    reload: reloadCalendars,
  } = useFamilyQuery(familyId, calendarsFetcher);

  const window = useMemo(() => {
    const start = view === "WEEK" ? anchor.startOf("week") : anchor.startOf("month");
    const end = view === "WEEK" ? anchor.endOf("week") : anchor.endOf("month");
    // Pad by a day so a timezone offset cannot clip an occurrence that
    // belongs to the visible edge of the grid.
    return {
      from: start.subtract(1, "day").valueOf(),
      to: end.add(1, "day").valueOf(),
    };
  }, [anchor, view]);

  const occurrencesFetcher = useCallback(
    (id: string) => homeMindCalendarApi.listOccurrences(id, window.from, window.to, calendarId),
    [window.from, window.to, calendarId],
  );
  const { data: occurrences, loading, error, reload } =
    useFamilyQuery(familyId, occurrencesFetcher);

  const events = useMemo(
    () => (occurrences ?? []).filter((o) => o.status === "CONFIRMED"),
    [occurrences],
  );

  const byDay = useMemo(() => {
    const map = new Map<string, FamilyCalendarOccurrence[]>();
    for (const occurrence of events) {
      const key = formatServerYmd(occurrence.starts_at * 1000, timezone);
      const bucket = map.get(key);
      if (bucket) bucket.push(occurrence);
      else map.set(key, [occurrence]);
    }
    for (const bucket of map.values()) bucket.sort((a, b) => a.starts_at - b.starts_at);
    return map;
  }, [events, timezone]);

  const gridDays = useMemo(() => {
    if (view !== "MONTH") return [];
    const first = anchor.startOf("month").startOf("week");
    const last = anchor.endOf("month").endOf("week");
    const days: Dayjs[] = [];
    for (
      let day = first;
      day.isBefore(last) || day.isSame(last, "day");
      day = day.add(1, "day")
    ) {
      days.push(day);
    }
    return days;
  }, [anchor, view]);

  const agendaGroups = useMemo(
    () => [...byDay.entries()].sort(([a], [b]) => a.localeCompare(b)),
    [byDay],
  );

  const refresh = () => {
    reload();
    reloadCalendars();
  };

  return (
    <FamilyPageShell
      title={t("familyCalendar.title", "家庭日历")}
      subtitle={`${t("familyCalendar.timezoneHint", "显示时区")}: ${timezone}`}
      actions={
        <Space wrap>
          <Select
            allowClear
            style={{ minWidth: 160 }}
            placeholder={t("familyCalendar.allCalendars", "全部日历")}
            value={calendarId}
            onChange={setCalendarId}
            options={(calendars ?? []).map((c: FamilyCalendar) => ({
              value: c.id,
              label: c.name,
            }))}
          />
          <Segmented
            value={view}
            onChange={(value) => setView(String(value) as ViewMode)}
            options={[
              { label: t("familyCalendar.viewMonth", "月"), value: "MONTH" },
              { label: t("familyCalendar.viewWeek", "周"), value: "WEEK" },
              { label: t("familyCalendar.viewAgenda", "议程"), value: "AGENDA" },
            ]}
          />
          <Button
            onClick={() =>
              setAnchor(anchor.subtract(1, view === "WEEK" ? "week" : "month"))
            }
          >
            ‹
          </Button>
          <Button onClick={() => setAnchor(dayjs())}>
            {t("familyCalendar.today", "今天")}
          </Button>
          <Button
            onClick={() => setAnchor(anchor.add(1, view === "WEEK" ? "week" : "month"))}
          >
            ›
          </Button>
          <Button type="primary" onClick={() => setCreating(true)}>
            {t("familyCalendar.newEvent", "新建事件")}
          </Button>
        </Space>
      }
    >
      <>
        <QueryError error={calendarError ?? error} />
        {view === "MONTH" ? (
          <MonthGrid
            days={gridDays}
            byDay={byDay}
            timezone={timezone}
            today={formatServerYmd(Date.now(), timezone)}
            onSelect={setEditing}
          />
        ) : null}
        {view === "WEEK" ? (
          <WeekStrip anchor={anchor} byDay={byDay} timezone={timezone} onSelect={setEditing} />
        ) : null}
        {view === "AGENDA" ? (
          <Agenda
            groups={agendaGroups}
            timezone={timezone}
            loading={loading && !occurrences}
            onSelect={setEditing}
            emptyHint={t("familyCalendar.empty", "这段时间没有安排")}
          />
        ) : null}
        <EventModal
          open={creating || editing !== null}
          occurrence={editing}
          calendars={calendars ?? []}
          defaultTimezone={timezone}
          onClose={() => {
            setCreating(false);
            setEditing(null);
          }}
          onSaved={refresh}
        />
      </>
    </FamilyPageShell>
  );
}

function MonthGrid({
  days,
  byDay,
  timezone,
  today,
  onSelect,
}: {
  days: Dayjs[];
  byDay: Map<string, FamilyCalendarOccurrence[]>;
  timezone: string;
  today: string;
  onSelect: (occurrence: FamilyCalendarOccurrence) => void;
}) {
  const { t } = useTranslation();
  return (
    <Row gutter={[8, 8]}>
      {days.map((day) => {
        const key = formatServerYmd(day.valueOf(), timezone);
        const items = byDay.get(key) ?? [];
        const inMonth = day.format("YYYY-MM") === today.slice(0, 7);
        return (
          <Col key={key} xs={24} sm={12} md={8} lg={6} xl={4}>
            <Card size="small" styles={{ body: { padding: 8, minHeight: 96 } }}>
              <Text type={inMonth ? undefined : "secondary"}>{day.date()}</Text>
              {items.slice(0, 3).map((item) => (
                <div
                  key={`${item.event_id}-${item.occurrence_key}`}
                  style={{ marginTop: 4 }}
                >
                  <Tag
                    color="blue"
                    style={{ cursor: "pointer", marginInlineEnd: 0 }}
                    onClick={() => onSelect(item)}
                  >
                    {formatServerHourMinute(item.starts_at * 1000, timezone)} {item.title}
                  </Tag>
                </div>
              ))}
              {items.length > 3 ? (
                <Text type="secondary" style={{ fontSize: 12 }}>
                  {t("familyCalendar.more", "还有")} +{items.length - 3}
                </Text>
              ) : null}
            </Card>
          </Col>
        );
      })}
    </Row>
  );
}

function WeekStrip({
  anchor,
  byDay,
  timezone,
  onSelect,
}: {
  anchor: Dayjs;
  byDay: Map<string, FamilyCalendarOccurrence[]>;
  timezone: string;
  onSelect: (occurrence: FamilyCalendarOccurrence) => void;
}) {
  const start = anchor.startOf("week");
  const days = Array.from({ length: 7 }, (_, index) => start.add(index, "day"));
  return (
    <Row gutter={[8, 8]}>
      {days.map((day) => {
        const key = formatServerYmd(day.valueOf(), timezone);
        const items = byDay.get(key) ?? [];
        return (
          <Col key={key} xs={24} sm={12} md={8} lg={6} xl={3}>
            <Card size="small" styles={{ body: { padding: 8, minHeight: 120 } }}>
              <Text strong>{day.format("MM-DD ddd")}</Text>
              <div>
                {items.map((item) => (
                  <OccurrenceRow
                    key={`${item.event_id}-${item.occurrence_key}`}
                    occurrence={item}
                    timezone={timezone}
                    onClick={() => onSelect(item)}
                  />
                ))}
              </div>
            </Card>
          </Col>
        );
      })}
    </Row>
  );
}

function Agenda({
  groups,
  timezone,
  loading,
  onSelect,
  emptyHint,
}: {
  groups: [string, FamilyCalendarOccurrence[]][];
  timezone: string;
  loading: boolean;
  onSelect: (occurrence: FamilyCalendarOccurrence) => void;
  emptyHint: string;
}) {
  return (
    <Card size="small" loading={loading}>
      {groups.length === 0 ? (
        <Text type="secondary">{emptyHint}</Text>
      ) : (
        groups.map(([day, items]) => (
          <div key={day} style={{ marginBottom: 12 }}>
            <Text strong>{day}</Text>
            <div>
              {items.map((item) => (
                <OccurrenceRow
                  key={`${item.event_id}-${item.occurrence_key}`}
                  occurrence={item}
                  timezone={timezone}
                  onClick={() => onSelect(item)}
                />
              ))}
            </div>
          </div>
        ))
      )}
    </Card>
  );
}

function OccurrenceRow({
  occurrence,
  timezone,
  onClick,
}: {
  occurrence: FamilyCalendarOccurrence;
  timezone: string;
  onClick: () => void;
}) {
  return (
    <div style={{ marginTop: 4, cursor: "pointer" }} onClick={onClick}>
      <Badge
        status={occurrence.is_recurring ? "processing" : "default"}
        text={
          <span>
            {formatServerHourMinute(occurrence.starts_at * 1000, timezone)}–
            {formatServerHourMinute(occurrence.ends_at * 1000, timezone)} {occurrence.title}
            {occurrence.location ? (
              <Text type="secondary"> · {occurrence.location}</Text>
            ) : null}
          </span>
        }
      />
    </div>
  );
}

function EventModal({
  open,
  occurrence,
  calendars,
  defaultTimezone,
  onClose,
  onSaved,
}: {
  open: boolean;
  occurrence: FamilyCalendarOccurrence | null;
  calendars: FamilyCalendar[];
  defaultTimezone: string;
  onClose: () => void;
  onSaved: () => void;
}) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const { familyId } = useActiveFamily();
  const [form] = Form.useForm();
  // The edit path needs the stored row, not just the occurrence: the
  // optimistic-lock version and the exact RRULE both live there.
  const [source, setSource] = useState<FamilyCalendarEvent | null>(null);

  const isEdit = occurrence !== null;

  useEffect(() => {
    if (familyId === null || occurrence === null) {
      setSource(null);
      return;
    }
    let cancelled = false;
    homeMindCalendarApi
      .listEvents(familyId)
      .then((rows) => {
        if (cancelled) return;
        setSource(rows.find((row) => row.id === occurrence.event_id) ?? null);
      })
      .catch(() => {
        if (!cancelled) setSource(null);
      });
    return () => {
      cancelled = true;
    };
  }, [familyId, occurrence]);

  const initialValues = useMemo(
    () => ({
      calendar_id: occurrence?.calendar_id ?? calendars[0]?.id,
      title: occurrence?.title ?? "",
      location: occurrence?.location ?? "",
      description: occurrence?.description ?? "",
      all_day: occurrence?.all_day ?? false,
      recurrence_rule: source?.recurrence_rule ?? "",
      time_range: occurrence
        ? [dayjs(occurrence.starts_at * 1000), dayjs(occurrence.ends_at * 1000)]
        : [dayjs().hour(9).minute(0), dayjs().hour(10).minute(0)],
    }),
    [occurrence, source, calendars],
  );

  const submit = async () => {
    if (familyId === null) return;
    const values = await form.validateFields();
    const [from, to] = values.time_range as [Dayjs, Dayjs];
    const payload = {
      calendar_id: values.calendar_id,
      title: values.title,
      description: values.description ?? "",
      location: values.location || null,
      all_day: Boolean(values.all_day),
      starts_at: from.valueOf(),
      ends_at: to.valueOf(),
      recurrence_rule: values.recurrence_rule || null,
    };
    try {
      if (isEdit && occurrence) {
        await homeMindCalendarApi.updateEvent(familyId, occurrence.event_id, {
          title: payload.title,
          description: payload.description,
          location: payload.location,
          all_day: payload.all_day,
          starts_at: payload.starts_at,
          ends_at: payload.ends_at,
          recurrence_rule: payload.recurrence_rule,
          // Send the version we read; the server answers 409 rather
          // than overwriting a sibling's edit made since.
          ...(source ? { expected_version: source.version } : {}),
        });
      } else {
        await homeMindCalendarApi.createEvent(familyId, {
          ...payload,
          timezone: defaultTimezone,
        });
      }
      message.success(t("familyCalendar.saved", "已保存"));
      onSaved();
      onClose();
      form.resetFields();
    } catch (err) {
      message.error(err instanceof Error ? err.message : String(err));
    }
  };

  const cancel = async () => {
    if (familyId === null || !occurrence) return;
    try {
      await homeMindCalendarApi.cancelEvent(familyId, occurrence.event_id);
      message.success(t("familyCalendar.cancelled", "已取消"));
      onSaved();
      onClose();
    } catch (err) {
      message.error(err instanceof Error ? err.message : String(err));
    }
  };

  return (
    <Modal
      open={open}
      title={
        isEdit
          ? t("familyCalendar.editEvent", "编辑事件")
          : t("familyCalendar.newEvent", "新建事件")
      }
      onCancel={onClose}
      onOk={() => void submit()}
      okText={t("familyCalendar.save", "保存")}
      destroyOnHidden
    >
      <Form
        form={form}
        layout="vertical"
        initialValues={initialValues}
        key={`${occurrence?.occurrence_key ?? "new"}:${source?.version ?? 0}`}
      >
        <Form.Item
          name="calendar_id"
          label={t("familyCalendar.calendar", "日历")}
          rules={[{ required: true }]}
        >
          <Select
            options={calendars.map((c) => ({ value: c.id, label: c.name }))}
            disabled={isEdit}
          />
        </Form.Item>
        <Form.Item
          name="title"
          label={t("familyCalendar.title_", "标题")}
          rules={[{ required: true }]}
        >
          <Input maxLength={200} />
        </Form.Item>
        <Form.Item
          name="time_range"
          label={t("familyCalendar.timeRange", "时间")}
          rules={[{ required: true }]}
        >
          <DatePicker.RangePicker
            showTime={{ format: "HH:mm", minuteStep: 5 }}
            format="YYYY-MM-DD HH:mm"
            style={{ width: "100%" }}
          />
        </Form.Item>
        <Form.Item name="location" label={t("familyCalendar.location", "地点")}>
          <Input maxLength={300} />
        </Form.Item>
        <Form.Item name="description" label={t("familyCalendar.description", "备注")}>
          <Input.TextArea rows={2} maxLength={5000} />
        </Form.Item>
        <Form.Item
          name="all_day"
          label={t("familyCalendar.allDay", "全天")}
          valuePropName="checked"
        >
          <Switch />
        </Form.Item>
        <Form.Item
          name="recurrence_rule"
          label={t("familyCalendar.recurrence", "重复")}
          extra={t("familyCalendar.recurrenceHint", "重复规则以 RFC 5545 RRULE 保存")}
        >
          <Select
            options={RECURRENCE_OPTIONS.map((o) => ({ value: o.value, label: t(o.labelKey) }))}
          />
        </Form.Item>
        {isEdit && occurrence ? (
          <Popconfirm
            title={t("familyCalendar.cancelConfirm", "确认取消这个事件？")}
            onConfirm={() => void cancel()}
          >
            <Button danger block>
              {t("familyCalendar.cancelEvent", "取消事件")}
            </Button>
          </Popconfirm>
        ) : null}
      </Form>
    </Modal>
  );
}