import { request, requestUpload } from "../../../api/request";
import adminPortrait from "../../../assets/avatars/admin.png";
import bossPortrait from "../../../assets/avatars/boss.png";
import doctorPortrait from "../../../assets/avatars/doctor.png";
import financePortrait from "../../../assets/avatars/finance.png";
import nursePortrait from "../../../assets/avatars/nurse.png";
import opsPortrait from "../../../assets/avatars/ops.png";
import staffPortrait from "../../../assets/avatars/staff.png";
import studentPortrait from "../../../assets/avatars/student.png";
import supportPortrait from "../../../assets/avatars/support.png";
import teacherPortrait from "../../../assets/avatars/teacher.png";
import { useAuthImageSrc } from "../../../hooks/useAuthImageSrc";
import { validateAvatarFile } from "../../Experts/components/ExpertAvatarPicker";
import DiceBearAvatarPresets from "../../../components/DiceBearAvatarPresets";
import styles from "./index.module.less";

export type AvatarKind = "user" | "role";

function presetsFor(kind: AvatarKind): AvatarPresetCatalog {
  return kind === "role" ? ROLE_AVATAR_PRESETS : USER_AVATAR_PRESETS;
}

export function resolveAvatarPreset(kind: AvatarKind, icon?: string | null) {
  return resolveCatalogPreset(presetsFor(kind), icon);
}

export function uploadUserAvatar(userId: number, file: File) {
  const body = new FormData();
  body.append("file", file);
  return requestUpload<{ avatar_url: string | null }>(
    `/users/${userId}/avatar`,
    body,
  );
}

export function deleteUserAvatar(userId: number) {
  return request<void>(`/users/${userId}/avatar`, { method: "DELETE" });
}

export function ProfileAvatar({
  url,
  icon,
  defaultIcon,
  kind = "user",
  className,
}: {
  url?: string | null;
  icon?: string | null;
  defaultIcon?: string | null;
  kind?: AvatarKind;
  className?: string;
}) {
  return (
    <ResourceAvatar
      url={url}
      icon={icon}
      defaultIcon={defaultIcon}
      presets={presetsFor(kind)}
      className={className}
    />
  );
}

export function RoleSelectLabel({
  url,
  icon,
  label,
}: {
  url?: string | null;
  icon?: string | null;
  label: string;
}) {
  return (
    <span className={styles.roleSelectOption}>
      <ProfileAvatar
        url={url}
        icon={icon}
        kind="role"
        className={styles.roleSelectIcon}
      />
      <span className={styles.roleSelectName}>{label}</span>
    </span>
  );
}

export function ProfileAvatarPicker({
  avatarUrl,
  icon,
  kind = "user",
  disabled = false,
  avatarSeed = "homemind-user",
  onPick,
  onSelectIcon,
  onRemove,
}: {
  avatarUrl?: string | null;
  icon?: string | null;
  kind?: AvatarKind;
  disabled?: boolean;
  avatarSeed?: string;
  onPick: (file: File) => void | Promise<void>;
  onSelectIcon?: (icon: string | null) => void | Promise<void>;
  onRemove?: () => void | Promise<void>;
}) {
  return (
    <div className={styles.profileAvatarPicker}>
      <div className={styles.profileAvatarPickerHead}>
        <ProfileAvatar
          url={displayUrl}
          icon={icon}
          kind={kind}
          className={[
            styles.profileAvatarPickerPreview,
            kind === "role" ? styles.profileAvatarPickerPreviewRole : "",
          ]
            .filter(Boolean)
            .join(" ")}
        />
        <div className={styles.profileAvatarPickerActions}>
          <Button
            size="small"
            icon={<ImagePlus size={14} />}
            disabled={disabled}
            onClick={() => {
              void pickImageFile().then(async (file) => {
                if (!file) return;
                const err = validateAvatarFile(file, t);
                if (err) {
                  message.error(err);
                  return;
                }
                replaceLocalPreview(URL.createObjectURL(file));
                try {
                  await onPick(file);
                } catch {
                  replaceLocalPreview(null);
                }
              });
            }}
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
          <span className={styles.profileAvatarPickerHint}>
            {t("experts.avatarHint")}
          </span>
        </div>
      </div>
      <div className={styles.avatarPresetLabel}>
        {t("adminUsers.avatarSamples")}
      </div>
      <div className={styles.avatarPresetGrid}>
        {presets.map((preset, index) => {
          const selected =
            !hasPhoto && (index === 0 ? !icon : icon === preset.id);
          const label =
            index === 0 ? t("adminUsers.avatarDefault") : t(preset.labelKey);
          const plain = kind === "role";
          const portrait = Boolean(preset.src);
          const PresetIcon = preset.Icon;
          return (
            <button
              key={preset.id}
              type="button"
              className={[
                styles.avatarPreset,
                plain ? styles.avatarPresetPlain : "",
                portrait ? styles.avatarPresetPortrait : "",
                selected ? styles.avatarPresetActive : "",
              ]
                .filter(Boolean)
                .join(" ")}
              style={
                plain || portrait
                  ? undefined
                  : { background: preset.bg, color: preset.fg }
              }
              disabled={disabled}
              aria-pressed={selected}
              aria-label={label}
              title={label}
              onClick={() => {
                replaceLocalPreview(null);
                void Promise.resolve(
                  onSelectIcon?.(index === 0 ? null : preset.id),
                ).catch(() => undefined);
              }}
            >
              {portrait ? (
                <img src={preset.src} alt="" />
              ) : PresetIcon ? (
                <PresetIcon size={plain ? 22 : 18} />
              ) : null}
            </button>
          );
        })}
      </div>
      {kind === "user" ? (
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
      ) : null}
    </div>
  );
}
