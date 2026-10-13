import { Audio, AudioPlayer, AudioSkin } from "@videojs/react/audio";
import "@videojs/react/audio/skin.css";
import { Video, VideoPlayer, VideoSkin } from "@videojs/react/video";
import "@videojs/react/video/skin.css";
import { App, Button, Image, Modal, Space, Spin, Typography } from "antd";
import {
  Download,
  Eye,
  FileText,
  Film,
  Music,
  Play,
  Share2,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import {
  homeMindFamilyApi,
  type FamilyAsset,
} from "../../api/modules/homeMindFamily";
import DocumentPreviewCore from "../../components/DocumentPreviewCore";
import { getDocKind } from "../../utils/docKind";
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
  const [documentOpen, setDocumentOpen] = useState(false);
  const { message } = App.useApp();
  const { t } = useTranslation();
  const documentKind = getDocKind(asset.name);

  const fetchDocument = useCallback(
    (
      onProgress?: (loaded: number, total: number) => void,
      signal?: AbortSignal,
    ) =>
      homeMindFamilyApi.getAssetContent(
        familyId,
        asset.id,
        onProgress,
        signal ? { signal } : undefined,
      ),
    [asset.id, familyId],
  );

  const downloadDocument = useCallback(async () => {
    try {
      const blob = await homeMindFamilyApi.getAssetContent(familyId, asset.id);
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = asset.name;
      anchor.click();
      URL.revokeObjectURL(url);
    } catch (error) {
      message.error(error instanceof Error ? error.message : String(error));
    }
  }, [asset.id, asset.name, familyId, message]);

  const shareDocument = useCallback(async () => {
    try {
      const blob = await homeMindFamilyApi.getAssetContent(familyId, asset.id);
      const file = new File([blob], asset.name, {
        type: blob.type || "application/octet-stream",
      });
      const shareData: ShareData = {
        title: asset.name,
        files: [file],
      };
      if (navigator.share && navigator.canShare?.(shareData)) {
        await navigator.share(shareData);
        return;
      }
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = asset.name;
      anchor.click();
      URL.revokeObjectURL(url);
      message.info(
        t(
          "family.shareDownloadFallback",
          "当前设备不支持直接分享，文件已下载，可从下载目录发送。",
        ),
      );
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") return;
      message.error(error instanceof Error ? error.message : String(error));
    }
  }, [asset.id, asset.name, familyId, message, t]);

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
      setMediaUrl(
        await homeMindFamilyApi.getAssetContentUrl(familyId, asset.id),
      );
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

  if (documentKind) {
    const previewSupported = !asset.name.toLowerCase().endsWith(".doc");
    return (
      <>
        <div className={styles.familyDocumentEntry}>
          <FileText size={22} aria-hidden="true" />
          {showName && (
            <Typography.Text className={styles.familyDocumentName} ellipsis>
              {asset.name}
            </Typography.Text>
          )}
          <div className={styles.familyDocumentActions}>
            {previewSupported && (
              <Button
                type="text"
                icon={<Eye size={15} />}
                aria-label={t("family.previewDocument", "Preview {{name}}", {
                  name: asset.name,
                })}
                onClick={(event) => {
                  event.stopPropagation();
                  setDocumentOpen(true);
                }}
              />
            )}
            <Button
              type="text"
              icon={<Share2 size={15} />}
              aria-label={t("family.shareDocument", "Share {{name}}", {
                name: asset.name,
              })}
              onClick={(event) => {
                event.stopPropagation();
                void shareDocument();
              }}
            />
            <Button
              type="text"
              icon={<Download size={15} />}
              aria-label={t("family.downloadDocument", "Download {{name}}", {
                name: asset.name,
              })}
              onClick={(event) => {
                event.stopPropagation();
                void downloadDocument();
              }}
            />
          </div>
        </div>
        {previewSupported && (
          <Modal
            title={asset.name}
            open={documentOpen}
            footer={null}
            destroyOnHidden
            width="min(1120px, calc(100vw - 32px))"
            onCancel={() => setDocumentOpen(false)}
          >
            <div className={styles.familyDocumentPreview}>
              <DocumentPreviewCore
                kind={documentKind}
                filename={asset.name}
                fetchBlob={fetchDocument}
                onDownload={downloadDocument}
              />
            </div>
          </Modal>
        )}
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
