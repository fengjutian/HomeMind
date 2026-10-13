import { useCallback, useEffect, useRef, useState } from "react";
import { Button, Input, Spin } from "antd";
import { Download, ExternalLink, RefreshCw, Sparkles } from "lucide-react";
import { useTranslation } from "react-i18next";

import { request } from "../../../../api/request";
import {
  apiErrorMessage,
  isNotFoundApiError,
} from "../../../../utils/apiError";
import { message } from "@/utils/antdMessage";
import {
  skillImportPath,
  skillListPath,
  type SkillInstallTarget,
} from "./skillInstallTarget";
import styles from "../index.module.less";

interface SkillsShSkill {
  id: string;
  slug: string;
  name: string;
  source: string;
  installs: number;
  url: string;
  description?: string;
  homepage?: string;
}

interface SkillsShTabProps {
  target: SkillInstallTarget;
  onInstalled?: () => void;
  source?: "skills-sh" | "qoder-community";
}

export default function SkillsShTab({
  target,
  onInstalled,
  source = "skills-sh",
}: SkillsShTabProps) {
  const { t } = useTranslation();
  const [query, setQuery] = useState("");
  const [skills, setSkills] = useState<SkillsShSkill[]>([]);
  const [installed, setInstalled] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(true);
  const [installing, setInstalling] = useState<string | null>(null);
  const [loadError, setLoadError] = useState("");
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const search = useCallback(
    async (value: string) => {
      setLoading(true);
      setLoadError("");
      try {
        const q = encodeURIComponent(value.trim());
        const endpoint =
          source === "qoder-community"
            ? "/skills/qoder-community/search"
            : "/skills/skills-sh/search";
        setSkills(
          await request<SkillsShSkill[]>(`${endpoint}?q=${q}&limit=100`),
        );
      } catch (error) {
        if (isNotFoundApiError(error)) {
          setLoadError(t("skills.marketBackendOutdated"));
          return;
        }
        const fallback =
          source === "qoder-community"
            ? t("skills.qoderCommunityLoadFailed")
            : t("skills.skillsShLoadFailed");
        setLoadError(apiErrorMessage(error, fallback, t));
      } finally {
        setLoading(false);
      }
    },
    [source, t],
  );

  useEffect(() => {
    if (target.type === "browse") return;
    request<Array<{ name: string; slug?: string }>>(skillListPath(target))
      .then((rows) => setInstalled(new Set(rows.map((row) => row.slug ?? row.name))))
      .catch(() => undefined);
  }, [target]);

  useEffect(() => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => void search(query), 350);
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, [query, search]);

  const install = async (skill: SkillsShSkill) => {
    if (target.type === "browse" || installing) return;
    setInstalling(skill.id);
    try {
      const body: Record<string, unknown> = {
        bundle_url: skill.url,
        overwrite: true,
      };
      if (target.type === "agent") body.enable = true;
      await request(skillImportPath(target), {
        method: "POST",
        body: JSON.stringify(body),
      });
      setInstalled((current) => new Set([...current, skill.slug]));
      message.success(t("skills.installSuccess"));
      onInstalled?.();
    } catch (error) {
      message.error(apiErrorMessage(error, t("skills.installFailed"), t));
    } finally {
      setInstalling(null);
    }
  };

  return (
    <div className={styles.skillHubContainer}>
      <div className={styles.skillHubToolbar}>
        <Input
          className={styles.skillHubSearch}
          placeholder={
            source === "qoder-community"
              ? t("skills.searchQoderCommunity")
              : t("skills.searchSkillsSh")
          }
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          allowClear
          suffix={loading ? <Spin size="small" /> : undefined}
        />
        <Button
          icon={<ExternalLink size={14} />}
          href={
            source === "qoder-community"
              ? "https://qoder-community.pages.dev/skills/"
              : "https://skills.sh/"
          }
          target="_blank"
          type="text"
          size="small"
        >
          {source === "qoder-community" ? "Qoder Community" : "skills.sh"}
        </Button>
        <Button
          icon={<RefreshCw size={14} />}
          type="text"
          size="small"
          onClick={() => void search(query)}
        >
          {t("common.refresh")}
        </Button>
      </div>

      {loadError ? (
        <div style={{ color: "var(--fn-color-danger)", textAlign: "center", padding: 48 }}>
          {loadError}
        </div>
      ) : !loading && skills.length === 0 ? (
        <div style={{ color: "var(--fn-text-tertiary)", textAlign: "center", padding: 48 }}>
          {source === "qoder-community"
            ? t("skills.qoderCommunityEmpty")
            : t("skills.skillsShEmpty")}
        </div>
      ) : (
        <div className={styles.hubGrid}>
          {skills.map((skill) => (
            <div key={skill.id} className={styles.hubCard}>
              <div className={styles.hubCardHeader}>
                <span className={styles.hubCardIconFallback}>
                  <Sparkles size={16} />
                </span>
                <span className={styles.hubCardName}>{skill.name}</span>
              </div>
              <div className={styles.hubCardDesc}>
                {skill.description || skill.source}
              </div>
              <div className={styles.hubCardFooter}>
                <span className={styles.hubCardStat}>
                  <Download size={14} /> {skill.installs.toLocaleString()}
                </span>
                {target.type === "browse" ? (
                  <Button
                    size="small"
                    href={skill.homepage ?? skill.url}
                    target="_blank"
                  >
                    {t("skills.homepage")}
                  </Button>
                ) : (
                  <Button
                    size="small"
                    type={installed.has(skill.slug) ? "default" : "primary"}
                    loading={installing === skill.id}
                    onClick={() => void install(skill)}
                  >
                    {installed.has(skill.slug)
                      ? t("skills.reinstall")
                      : t("skills.install")}
                  </Button>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
