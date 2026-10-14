import { useState } from "react";
import { Button, Input } from "antd";
import { Sparkles } from "lucide-react";
import { useTranslation } from "react-i18next";

import { request } from "../../../api/request";
import { message } from "@/utils/antdMessage";
import { apiErrorMessage } from "../../../utils/apiError";
import styles from "../index.module.less";

type LocalizedText = { zh: string; en: string };

type QuickPrompt = {
  title: LocalizedText;
  description: LocalizedText;
  prompt: LocalizedText;
  color: string;
  icon_name?: string | null;
};

type GeneratedExpertDraft = {
  label: LocalizedText;
  description: LocalizedText;
  welcome_message: LocalizedText;
  icon_name: string;
  color: string;
  quick_prompts: QuickPrompt[];
  task_examples: Record<string, string[]>;
  soul_md: string;
  model: string;
};

type Props = {
  lang: "zh" | "en";
  onCreated: (agentId: string, name: string) => void;
};

export default function GenerateExpertTab({ lang, onCreated }: Props) {
  const { t } = useTranslation();
  const [requirement, setRequirement] = useState("");
  const [draft, setDraft] = useState<GeneratedExpertDraft | null>(null);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [welcome, setWelcome] = useState("");
  const [soul, setSoul] = useState("");
  const [generating, setGenerating] = useState(false);
  const [creating, setCreating] = useState(false);

  const applyDraft = (next: GeneratedExpertDraft) => {
    setDraft(next);
    setName(next.label[lang] || next.label.zh || next.label.en);
    setDescription(
      next.description[lang] || next.description.zh || next.description.en,
    );
    setWelcome(
      next.welcome_message[lang] ||
        next.welcome_message.zh ||
        next.welcome_message.en,
    );
    setSoul(next.soul_md);
  };

  const generate = async () => {
    const clean = requirement.trim();
    if (clean.length < 10) {
      message.warning(t("experts.aiGenerate.requirementTooShort"));
      return;
    }
    setGenerating(true);
    try {
      const next = await request<GeneratedExpertDraft>("/experts/generate", {
        method: "POST",
        body: JSON.stringify({ requirement: clean }),
      });
      applyDraft(next);
      message.success(t("experts.aiGenerate.generated"));
    } catch (err) {
      message.error(
        apiErrorMessage(err, t("experts.aiGenerate.generateFailed"), t),
      );
    } finally {
      setGenerating(false);
    }
  };

  const create = async () => {
    if (!draft || !name.trim() || !soul.trim()) return;
    setCreating(true);
    try {
      const created = await request<{ agent_id: string; name: string }>(
        "/agents/from-expert/default",
        {
          method: "POST",
          body: JSON.stringify({
            name: name.trim(),
            description: description.trim() || undefined,
            welcome_message: welcome.trim() || undefined,
            color: draft.color,
            file_overrides: [{ name: "SOUL.md", content: soul }],
            quick_prompts: draft.quick_prompts,
            enable_trajectory: true,
            conversation_mode: "craft",
          }),
        },
      );
      message.success(t("experts.agentCreated", { name: created.name }));
      onCreated(created.agent_id, created.name);
    } catch (err) {
      message.error(apiErrorMessage(err, t("experts.createFailed"), t));
    } finally {
      setCreating(false);
    }
  };

  return (
    <div className={styles.aiGeneratePage}>
      <section className={styles.aiGenerateIntro}>
        <div className={styles.aiGenerateHeading}>
          <span className={styles.aiGenerateIcon}>
            <Sparkles size={22} />
          </span>
          <div>
            <h2>{t("experts.aiGenerate.title")}</h2>
            <p>{t("experts.aiGenerate.subtitle")}</p>
          </div>
        </div>
        <Input.TextArea
          value={requirement}
          onChange={(event) => setRequirement(event.target.value)}
          placeholder={t("experts.aiGenerate.placeholder")}
          autoSize={{ minRows: 6, maxRows: 14 }}
          maxLength={8000}
          showCount
          disabled={generating}
        />
        <div className={styles.aiGenerateActions}>
          <span>{t("experts.aiGenerate.safetyHint")}</span>
          <Button
            type="primary"
            icon={<Sparkles size={15} />}
            loading={generating}
            onClick={() => void generate()}
          >
            {draft
              ? t("experts.aiGenerate.regenerate")
              : t("experts.aiGenerate.generate")}
          </Button>
        </div>
      </section>

      {draft && (
        <section className={styles.aiGeneratePreview}>
          <div className={styles.aiGeneratePreviewHeader}>
            <div>
              <h3>{t("experts.aiGenerate.previewTitle")}</h3>
              <p>{t("experts.aiGenerate.modelUsed", { model: draft.model })}</p>
            </div>
            <Button
              type="primary"
              loading={creating}
              disabled={!name.trim() || !soul.trim()}
              onClick={() => void create()}
            >
              {t("experts.aiGenerate.create")}
            </Button>
          </div>

          <div className={styles.aiGenerateFields}>
            <label>
              <span>{t("experts.agentName")}</span>
              <Input
                value={name}
                onChange={(event) => setName(event.target.value)}
              />
            </label>
            <label>
              <span>{t("experts.agentDescription")}</span>
              <Input.TextArea
                value={description}
                onChange={(event) => setDescription(event.target.value)}
                autoSize={{ minRows: 2, maxRows: 5 }}
              />
            </label>
            <label>
              <span>{t("experts.welcomeMessageTitle")}</span>
              <Input
                value={welcome}
                onChange={(event) => setWelcome(event.target.value)}
              />
            </label>
            <label>
              <span>SOUL.md</span>
              <Input.TextArea
                className={styles.aiGenerateSoul}
                value={soul}
                onChange={(event) => setSoul(event.target.value)}
                autoSize={{ minRows: 18, maxRows: 32 }}
              />
            </label>
          </div>

          <div className={styles.aiGeneratePromptSection}>
            <h4>{t("experts.aiGenerate.quickPrompts")}</h4>
            <div className={styles.aiGeneratePromptGrid}>
              {draft.quick_prompts.map((prompt, index) => (
                <div
                  className={styles.aiGeneratePromptCard}
                  key={`${prompt.title.zh}-${index}`}
                >
                  <strong>{prompt.title[lang] || prompt.title.zh}</strong>
                  <span>
                    {prompt.description[lang] || prompt.description.zh}
                  </span>
                </div>
              ))}
            </div>
          </div>
        </section>
      )}
    </div>
  );
}
