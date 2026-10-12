import { Audio, AudioPlayer, AudioSkin } from "@videojs/react/audio";
import "@videojs/react/audio/skin.css";
import { Video, VideoPlayer, VideoSkin } from "@videojs/react/video";
import "@videojs/react/video/skin.css";
import { App, Button, Image, Modal, Space, Spin, Typography } from "antd";
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
    message.error(t("family.mediaLoadFailed", "媒体加载失败"));
  };

  const closePlayer = () => {
    setPlayerOpen(false);
    setMediaLoading(false);
    setMediaUrl("");
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
            {mediaUrl && asset.asset_type === "VIDEO" && (
              <VideoPlayer>
                <VideoSkin className={styles.mediaPlayer}>
                  <Video
                    src={mediaUrl}
                    autoPlay
                    playsInline
                    preload="metadata"
                    onCanPlay={() => setMediaLoading(false)}
                    onError={handleMediaError}
                  />
                </VideoSkin>
              </VideoPlayer>
            )}
            {mediaUrl && asset.asset_type === "AUDIO" && (
              <AudioPlayer>
                <AudioSkin className={styles.mediaPlayer}>
                  <Audio
                    src={mediaUrl}
                    autoPlay
                    preload="metadata"
                    onCanPlay={() => setMediaLoading(false)}
                    onError={handleMediaError}
                  />
                </AudioSkin>
              </AudioPlayer>
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
