import { App, Button, Image, Modal, Space, Spin, Typography } from "antd";
import { Film, Music, Play } from "lucide-react";
import { useEffect, useRef, useState } from "react";

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
  const [previewFailed, setPreviewFailed] = useState(false);
  const [mediaUrl, setMediaUrl] = useState("");
  const [playerOpen, setPlayerOpen] = useState(false);
  const [mediaLoading, setMediaLoading] = useState(false);
  const { message } = App.useApp();

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
    setPreviewFailed(false);
    void homeMindFamilyApi
      .getAssetContent(familyId, asset.id)
      .then(
        (blob) =>
          new Promise<string>((resolve, reject) => {
            const reader = new FileReader();
            reader.onload = () => resolve(String(reader.result));
            reader.onerror = () => reject(reader.error);
            reader.readAsDataURL(blob);
          }),
      )
      .then((dataUrl) => {
        if (!cancelled) setSrc(dataUrl);
      })
      .catch(() => {
        if (!cancelled) setPreviewFailed(true);
      });
    return () => {
      cancelled = true;
    };
  }, [asset.asset_type, asset.id, familyId, visible]);

  useEffect(
    () => () => {
      if (mediaUrl) URL.revokeObjectURL(mediaUrl);
    },
    [mediaUrl],
  );

  const openPlayer = async () => {
    setPlayerOpen(true);
    setMediaLoading(true);
    try {
      const blob = await homeMindFamilyApi.getAssetContent(familyId, asset.id);
      setMediaUrl(URL.createObjectURL(blob));
    } catch (error) {
      setPlayerOpen(false);
      message.error(error instanceof Error ? error.message : String(error));
    } finally {
      setMediaLoading(false);
    }
  };

  const closePlayer = () => {
    setPlayerOpen(false);
    setMediaUrl((current) => {
      if (current) URL.revokeObjectURL(current);
      return "";
    });
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
              <video
                className={styles.mediaPlayer}
                src={mediaUrl}
                controls
                autoPlay
              />
            )}
            {mediaUrl && asset.asset_type === "AUDIO" && (
              <audio
                className={styles.mediaPlayer}
                src={mediaUrl}
                controls
                autoPlay
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
