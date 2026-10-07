/**
 * Family task list (Stage 8) — mobile-aware.
 *
 * The manual to-do path stays as simple as it was: type a title, press
 * add, tick it off. What changed for Stage 7 is that the page works at
 * 360px — the input and its button share one row rather than wrapping
 * into two, because a button below the fold is a button nobody taps.
 *
 * The schedulable board lives at `/task-board`; this page stays the
 * quick list so a family member who just wants to add "buy milk" never
 * meets a state machine.
 */
import { useCallback, useState } from "react";
import { App, Button, Card, Checkbox, Empty, Grid, Input, Segmented, Space } from "antd";
import { useTranslation } from "react-i18next";

import { useActiveFamily } from "../../hooks/useActiveFamily";
import { homeMindFamilyApi } from "../../api/modules/homeMindFamily";
import FamilyPageShell, {
  QueryError,
  useFamilyQuery,
} from "../../components/family/FamilyPageShell";

const { useBreakpoint } = Grid;

export default function FamilyTasks() {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const { familyId } = useActiveFamily();
  const [title, setTitle] = useState("");
  const [filter, setFilter] = useState("OPEN");
  const screens = useBreakpoint();
  const isPhone = screens.sm === false;

  const fetcher = useCallback((id: string) => homeMindFamilyApi.listTasks(id), []);
  const { data, loading, error, reload } = useFamilyQuery(familyId, fetcher);

  const visible = (data ?? []).filter((task) => {
    if (filter === "OPEN") return task.status !== "DONE" && task.status !== "CANCELLED";
    if (filter === "DONE") return task.status === "DONE";
    return true;
  });

  const add = async () => {
    if (familyId === null || !title.trim()) return;
    try {
      await homeMindFamilyApi.createTask(familyId, { title: title.trim() });
      setTitle("");
      message.success(t("family.tasks.created", "已创建"));
      reload();
    } catch (err) {
      message.error(err instanceof Error ? err.message : String(err));
    }
  };

  const toggle = async (taskId: string, status: string) => {
    if (familyId === null) return;
    try {
      await homeMindFamilyApi.updateTask(familyId, taskId, {
        status: status === "DONE" ? "TODO" : "DONE",
      });
      reload();
    } catch (err) {
      message.error(err instanceof Error ? err.message : String(err));
    }
  };

  return (
    <FamilyPageShell
      title={t("family.tasks.title", "家庭任务")}
      actions={
        <Segmented
          size={isPhone ? "small" : "middle"}
          value={filter}
          onChange={(value) => setFilter(String(value))}
          options={[
            { label: t("family.tasks.open", "未完成"), value: "OPEN" },
            { label: t("family.tasks.done", "已完成"), value: "DONE" },
            { label: t("family.tasks.all", "全部"), value: "ALL" },
          ]}
        />
      }
    >
      <>
        <QueryError error={error} />
        <Card size="small">
          <Space.Compact style={{ width: "100%", marginBottom: 12 }}>
            <Input
              value={title}
              placeholder={t("family.tasks.placeholder", "要做什么？")}
              onChange={(event) => setTitle(event.target.value)}
              onPressEnter={() => void add()}
            />
            <Button type="primary" onClick={() => void add()}>
              {t("family.tasks.add", "添加")}
            </Button>
          </Space.Compact>
          {loading && !data ? null : visible.length === 0 ? (
            <Empty description={t("family.tasks.empty", "暂无任务")} />
          ) : (
            visible.map((task) => (
              <div key={task.id} style={{ padding: "4px 0" }}>
                <Checkbox
                  checked={task.status === "DONE"}
                  onChange={() => void toggle(task.id, task.status)}
                >
                  {task.title}
                </Checkbox>
              </div>
            ))
          )}
        </Card>
      </>
    </FamilyPageShell>
  );
}