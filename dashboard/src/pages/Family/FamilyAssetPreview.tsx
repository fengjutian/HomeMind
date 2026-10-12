import { App, Button, Image, Modal, Progress, Space, Spin, Typography } from "antd";
import { Film, Music, Play } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import {
  homeMindFamilyApi,
  type FamilyAsset,
} from "../../api/modules/homeMindFamily";
import styles from "./index.module.less";

interface FamilyAssetPreviewProps {
  familyId: string;
  asset: Pick<FamilyAsset, "id" | "name" | "asset_type">;
  size?: number;
  showName?: boolean;
}

export default function FamilyAssetPreview({
  familyId,
  asset,
  size = 56,
  showName = true,
}: FamilyAssetPreviewProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [visible, setVisible] = useState(false);
  const [src, setSrc] = useState("");
  const [originalSrc, setOriginalSrc] = useState("");
  const [previewFailed, setPreviewFailed] = useState(false);
  const [mediaUrl, setMediaUrl] = useState("");
  const [playerOpen, setPlayerOpen] = useState(false);
  const [mediaLoading, setMediaLoading] = useState(false);
  const [transferProgress, setTransferProgress] = useState(0);
  const { message } = App.useApp();
  const { t } = useTranslation();

  useEffect(() => {
    const element = containerRef.current;
    if (!element || typeof IntersectionObserver === "undefined") {
      setVisible(true);
      return;
    }
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          setVisible(true);
          observer.disconnect();
        }
      },
      { rootMargin: "200px" },
    );
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    if (!visible || asset.asset_type !== "PHOTO") return;
    let cancelled = false;
    setSrc("");
    setOriginalSrc("");
    setPreviewFailed(false);
    void Promise.all([
      homeMindFamilyApi.getAssetThumbnailUrl(familyId, asset.id),
      homeMindFamilyApi.getAssetContentUrl(familyId, asset.id),
    ])
      .then(([thumbnail, original]) => {
        if (!cancelled) {
          setSrc(thumbnail);
          setOriginalSrc(original);
        }
      })
      .catch(() => {
        if (!cancelled) setPreviewFailed(true);
      });
    return () => {
      cancelled = true;
    };
  }, [asset.asset_type, asset.id, familyId, visible]);

  const openPlayer = async () => {
    setPlayerOpen(true);
    setMediaLoading(true);
    setTransferProgress(0);
    try {
      setMediaUrl(await homeMindFamilyApi.getAssetContentUrl(familyId, asset.id));
    } catch (error) {
      setPlayerOpen(false);
      setMediaLoading(false);
      message.error(error instanceof Error ? error.message : String(error));
    }
  };

  const handleMediaError = () => {
    setMediaLoading(false);
    setTransferProgress(0);
    message.error(t("family.mediaLoadFailed", "媒体加载失败"));
  };

  const closePlayer = () => {
    setPlayerOpen(false);
    setMediaLoading(false);
    setTransferProgress(0);
    setMediaUrl("");
  };

  const updateBufferedProgress = (media: HTMLMediaElement) => {
    if (
      !Number.isFinite(media.duration) ||
      media.duration <= 0 ||
      media.buffered.length === 0
    )
      return;
    setTransferProgress(
      Math.min(
        100,
        Math.round(
          (media.buffered.end(media.buffered.length - 1) / media.duration) *
            100,
        ),
      ),
    );
  };

  if (asset.asset_type === "VIDEO" || asset.asset_type === "AUDIO") {
    return (
      <>
        <Space align="center">
          {asset.asset_type === "VIDEO" ? (
            <Film size={20} />
          ) : (
            <Music size={20} />
          )}
          {showName && <Typography.Text>{asset.name}</Typography.Text>}
          <Button
            type="text"
            icon={<Play size={15} />}
            aria-label={asset.name}
            onClick={() => void openPlayer()}
          />
        </Space>
        <Modal
          title={asset.name}
          open={playerOpen}
          footer={null}
          destroyOnHidden
          width={880}
          onCancel={closePlayer}
        >
          <Spin spinning={mediaLoading}>
            {mediaLoading && mediaUrl && (
              <div className={styles.mediaTransferProgress}>
                <Progress percent={transferProgress} status="active" />
              </div>
            )}
            {mediaUrl && asset.asset_type === "VIDEO" && (
              <video
                className={styles.mediaPlayer}
                src={mediaUrl}
                controls
                autoPlay
                preload="metadata"
                onCanPlay={() => setMediaLoading(false)}
                onError={handleMediaError}
                onProgress={(event) =>
                  updateBufferedProgress(event.currentTarget)
                }
              />
            )}
            {mediaUrl && asset.asset_type === "AUDIO" && (
              <audio
                className={styles.mediaPlayer}
                src={mediaUrl}
                controls
                autoPlay
                preload="metadata"
                onCanPlay={() => setMediaLoading(false)}
                onError={handleMediaError}
                onProgress={(event) =>
                  updateBufferedProgress(event.currentTarget)
                }
              />
            )}
          </Spin>
        </Modal>
      </>
    );
  }

  if (asset.asset_type !== "PHOTO") {
    return showName ? <>{asset.name}</> : null;
  }

  return (
    <Space ref={containerRef} align="center">
      {src ? (
        <Image
          width={size}
          height={size}
          src={src}
          fallback={originalSrc}
          onError={() => setPreviewFailed(true)}
          alt={asset.name}
          className={styles.assetThumbnail}
        />
      ) : (
        <div
          className={styles.albumThumbnailPlaceholder}
          style={{ width: size, height: size }}
        >
          {previewFailed ? "!" : <Spin size="small" />}
        </div>
      )}
      {showName && <Typography.Text>{asset.name}</Typography.Text>}
    </Space>
  );
}
