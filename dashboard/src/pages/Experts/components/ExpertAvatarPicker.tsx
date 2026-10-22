import {
  EXPERT_AVATAR_PRESETS,
  expertPresetSrc,
  ResourceAvatarPicker,
  splitExpertAvatarUrl,
  validateAvatarFile,
} from "../../../components/AvatarPicker";
import ExpertAgentAvatar from "../../Chat/components/ExpertAgentAvatar";
import DiceBearAvatarPresets from "../../../components/DiceBearAvatarPresets";
import styles from "../index.module.less";

export { validateAvatarFile };

interface ExpertAvatarPickerProps {
  iconUrl?: string | null;
  iconName?: string | null;
  color?: string | null;
  disabled?: boolean;
  avatarSeed?: string;
  onPick: (file: File) => void | Promise<void>;
  /** Select a bundled ``/experts/avatars/{id}.svg`` (null = clear to Lucide). */
  onSelectPreset?: (presetId: string | null) => void | Promise<void>;
  onRemove?: () => void | Promise<void>;
}

export default function ExpertAvatarPicker({
  iconUrl,
  iconName,
  color,
  disabled = false,
  avatarSeed = "homemind-agent",
  onPick,
  onSelectPreset,
  onRemove,
}: ExpertAvatarPickerProps) {
  const { imageUrl, presetId: rawPresetId } = splitExpertAvatarUrl(iconUrl);
  // First grid tile is the Lucide fallback; treat bundled ``default.svg`` the same.
  const presetId = rawPresetId === "default" ? null : rawPresetId;

  return (
    <ResourceAvatarPicker
      imageUrl={imageUrl}
      presetId={presetId}
      presets={EXPERT_AVATAR_PRESETS}
      disabled={disabled}
      onPick={onPick}
      onSelectPreset={onSelectPreset}
      onRemove={onRemove}
      renderPreview={({ displayUrl, presetId: selectedPreset, hasPhoto }) => {
        const previewUrl = hasPhoto
          ? displayUrl
          : selectedPreset && selectedPreset !== "default"
          ? expertPresetSrc(selectedPreset)
          : null;
        return (
          <ExpertAgentAvatar
            iconUrl={previewUrl}
            iconName={iconName}
            color={color}
            size={56}
          />
        </div>
      </Modal>
      <div className={styles.avatarPickerActions}>
        <Button
          size="small"
          icon={<ImagePlus size={14} />}
          disabled={disabled}
          onClick={handlePick}
        >
          {hasPhoto ? t("experts.avatarChange") : t("experts.avatarUpload")}
        </Button>
        {hasPhoto && onRemove ? (
          <Button
            size="small"
            disabled={disabled}
            onClick={() => {
              replaceLocalPreview(null);
              void Promise.resolve(onRemove()).catch(() => undefined);
            }}
          >
            {t("experts.avatarRemove")}
          </Button>
        ) : null}
        <span className={styles.avatarPickerHint}>
          {t("experts.avatarHint")}
        </span>
      </div>
      <DiceBearAvatarPresets
        seed={avatarSeed}
        disabled={disabled}
        onPick={async (file) => {
          replaceLocalPreview(URL.createObjectURL(file));
          try {
            await onPick(file);
          } catch {
            replaceLocalPreview(null);
          }
        }}
      />
    </div>
  );
}
