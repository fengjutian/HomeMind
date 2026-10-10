import { Avatar } from "@dicebear/core";
import botttsNeutral from "@dicebear/styles/bottts-neutral.json";
import loreleiNeutral from "@dicebear/styles/lorelei-neutral.json";
import notionistsNeutral from "@dicebear/styles/notionists-neutral.json";
import pixelArtNeutral from "@dicebear/styles/pixel-art-neutral.json";
import shapes from "@dicebear/styles/shapes.json";
import { useMemo } from "react";
import { useTranslation } from "react-i18next";

import styles from "./DiceBearAvatarPresets.module.less";

const PRESETS = [
  { id: "bottts-neutral", definition: botttsNeutral },
  { id: "notionists-neutral", definition: notionistsNeutral },
  { id: "lorelei-neutral", definition: loreleiNeutral },
  { id: "pixel-art-neutral", definition: pixelArtNeutral },
  { id: "shapes", definition: shapes },
] as const;

function createSvg(
  definition: (typeof PRESETS)[number]["definition"],
  seed: string,
) {
  return new Avatar(definition, { seed, size: 128 }).toString();
}

function svgDataUri(svg: string): string {
  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
}

async function svgToPngFile(svg: string, name: string): Promise<File> {
  const url = URL.createObjectURL(new Blob([svg], { type: "image/svg+xml" }));
  try {
    const image = new Image();
    await new Promise<void>((resolve, reject) => {
      image.onload = () => resolve();
      image.onerror = () => reject(new Error("Unable to render avatar"));
      image.src = url;
    });
    const canvas = document.createElement("canvas");
    canvas.width = 256;
    canvas.height = 256;
    const context = canvas.getContext("2d");
    if (!context) throw new Error("Canvas is unavailable");
    context.drawImage(image, 0, 0, canvas.width, canvas.height);
    const blob = await new Promise<Blob>((resolve, reject) => {
      canvas.toBlob(
        (value) =>
          value ? resolve(value) : reject(new Error("Unable to encode avatar")),
        "image/png",
      );
    });
    return new File([blob], `dicebear-${name}.png`, { type: "image/png" });
  } finally {
    URL.revokeObjectURL(url);
  }
}

export default function DiceBearAvatarPresets({
  seed,
  disabled = false,
  onPick,
}: {
  seed: string;
  disabled?: boolean;
  onPick: (file: File) => void | Promise<void>;
}) {
  const { t } = useTranslation();
  const normalizedSeed = seed.trim() || "homemind";
  const previews = useMemo(
    () =>
      PRESETS.map((preset) => {
        const svg = createSvg(preset.definition, normalizedSeed);
        return { ...preset, svg, src: svgDataUri(svg) };
      }),
    [normalizedSeed],
  );

  return (
    <div className={styles.root}>
      <span className={styles.label}>{t("avatarPresets.label")}</span>
      <div className={styles.grid}>
        {previews.map((preset) => {
          const label = t(`avatarPresets.styles.${preset.id}`);
          return (
            <button
              key={preset.id}
              type="button"
              className={styles.preset}
              disabled={disabled}
              aria-label={label}
              title={label}
              onClick={() => {
                void svgToPngFile(preset.svg, preset.id).then(onPick);
              }}
            >
              <img src={preset.src} alt="" />
            </button>
          );
        })}
      </div>
    </div>
  );
}
