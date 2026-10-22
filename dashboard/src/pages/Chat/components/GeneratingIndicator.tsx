import { useTranslation } from "react-i18next";
import { Button } from "antd";
import { useElapsedSince } from "../../../hooks/useElapsedSeconds";
import type { TeamMemberActivity } from "../hooks/sseHelpers";
import styles from "../index.module.less";

interface GeneratingIndicatorProps {
  /** When set, appends elapsed seconds (useful while waiting for the first token). */
  startedAt?: number | null;
  showElapsed?: boolean;
  /**
   * Host turn already settled; only team members (or late peers) are still
   * producing — softer named copy.
   */
  membersOnly?: boolean;
  /** Named speakers still generating (e.g. ["临床助手"]). */
  speakerNames?: ReadonlyArray<string>;
  activity?: TeamMemberActivity;
  onCancelMember?: () => void;
}

/**
 * Bottom-left live status for the turn. Stop/cancel stays on the composer —
 * this row is status text only so it can remain visible the whole time.
 */
function GeneratingAvatar({ agentId }: { agentId?: string | null }) {
  const { t } = useTranslation();
  const { agents, activeAgent } = useAgent();
  const isTeamRoom = isTeamAgent(activeAgent);
  const speakerId =
    rewritePeerSpeakerId(activeAgent?.agent_id, agentId) ||
    agentId ||
    activeAgent?.agent_id;
  const hostSpeaker = isTeamHostSpeaker(
    isTeamRoom,
    speakerId,
    activeAgent?.agent_id,
  );
  const expert =
    (speakerId && agents.find((item) => item.agent_id === speakerId)) ||
    (!isTeamRoom || hostSpeaker ? activeAgent : undefined);
  const avatarProfileId = hostSpeaker
    ? activeAgent?.agent_id
    : speakerId || undefined;
  if (!expert && !avatarProfileId) return null;
  const avatarTooltip = hostSpeaker
    ? t("chat.teamHostHover", {
        name: activeAgent?.name || expert?.name || "",
      })
    : expert?.name || undefined;
  return (
    <div className={styles.avatarCol}>
      <ExpertMessageAvatar
        name={expert?.name || avatarProfileId}
        color={expert?.color}
        iconName={expert?.icon_name}
        iconUrl={expert?.icon_url}
        tooltip={avatarTooltip}
        profileAgentId={avatarProfileId}
      />
    </div>
  );
}

export default function GeneratingIndicator({
  startedAt = null,
  showElapsed = false,
  membersOnly = false,
  speakerNames = [],
  activity,
  onCancelMember,
}: GeneratingIndicatorProps) {
  const { t } = useTranslation();
  const elapsedAnchor =
    startedAt != null && startedAt > 0 ? startedAt : Date.now();
  const elapsed = useElapsedSince(elapsedAnchor);
  const showTimer = Boolean(
    !membersOnly && showElapsed && startedAt != null && startedAt > 0,
  );

  const named = speakerNames.filter((n) => n.trim());
  const memberElapsed = useElapsedSince(
    activity?.started_at ? activity.started_at * 1000 : elapsedAnchor,
  );
  const idleSeconds = activity?.last_activity_at
    ? Math.max(0, Math.floor(Date.now() / 1000 - activity.last_activity_at))
    : 0;
  let label: string;
  if (membersOnly || named.length > 0) {
    if (named.length === 1) {
      label = t("chat.memberResponding", {
        name: named[0],
        defaultValue: "{{name}} 正在回答",
      });
    } else if (named.length > 1) {
      label = t("chat.membersRespondingNamed", {
        names: named.join(t("chat.nameJoin", { defaultValue: "、" })),
        defaultValue: "{{names}} 正在回答",
      });
    } else {
      label = t("chat.membersResponding", {
        defaultValue: "成员回答中",
      });
    }
  } else if (showTimer) {
    label = t("chat.generatingWithElapsed", {
      seconds: elapsed,
      defaultValue: "生成中 · {{seconds}}s",
    });
  } else {
    label = t("chat.generating", "生成中");
  }

  return (
    <div className={styles.turnInset}>
      <div
        className={styles.generatingIndicator}
        role="status"
        aria-live="polite"
      >
        <span className={styles.thinkingDot} />
        <span className={styles.thinkingDot} />
        <span className={styles.thinkingDot} />
        <span className={styles.generatingText}>{label}</span>
        {activity ? (
          <span className={styles.generatingText}>
            · {t(`chat.memberStage.${activity.stage}`, activity.stage)} ·{" "}
            {memberElapsed}s
            {idleSeconds > 0
              ? ` · ${t("chat.memberLastActivity", {
                  seconds: idleSeconds,
                  defaultValue: "last activity {{seconds}}s ago",
                })}`
              : ""}
          </span>
        ) : null}
        {onCancelMember ? (
          <Button size="small" danger type="text" onClick={onCancelMember}>
            {t("chat.cancelMember", "Stop expert")}
          </Button>
        ) : null}
      </div>
    </div>
  );
}
