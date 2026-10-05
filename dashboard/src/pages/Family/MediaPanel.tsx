import { useCallback, useEffect, useState } from "react";
import {
  App,
  Button,
  Card,
  Empty,
  Input,
  List,
  Modal,
  Segmented,
  Space,
  Spin,
  Tag,
  Typography,
} from "antd";
import { Film, Music, Play, RefreshCw, Search } from "lucide-react";
import { useTranslation } from "react-i18next";

import {
  homeMindFamilyApi,
  type FamilyAsset,
} from "../../api/modules/homeMindFamily";
import styles from "./index.module.less";

interface MediaPanelProps {
  familyId: string;
}

type MediaFilter = "ALL" | "VIDEO" | "AUDIO";

const formatBytes = (bytes: number) => {
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
  return `${(bytes / 1024 ** 3).toFixed(1)} GB`;
};

export default function MediaPanel({ familyId }: MediaPanelProps) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const [assets, setAssets] = useState<FamilyAsset[]>([]);
  const [filter, setFilter] = useState<MediaFilter>("ALL");
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(false);
  const [playing, setPlaying] = useState<FamilyAsset | null>(null);
  const [mediaUrl, setMediaUrl] = useState("");
  const [mediaLoading, setMediaLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [videos, audio] = await Promise.all([
        homeMindFamilyApi.listAssets(familyId, { asset_type: "VIDEO" }),
        homeMindFamilyApi.listAssets(familyId, { asset_type: "AUDIO" }),
      ]);
      setAssets([...videos, ...audio]);
    } catch (error) {
      message.error(error instanceof Error ? error.message : String(error));
    } finally {
      setLoading(false);
    }
  }, [familyId, message]);

  useEffect(() => void load(), [load]);
  useEffect(
    () => () => {
      if (mediaUrl) URL.revokeObjectURL(mediaUrl);
    },
    [mediaUrl],
  );

  const openMedia = async (asset: FamilyAsset) => {
    setPlaying(asset);
    setMediaLoading(true);
    try {
      const blob = await homeMindFamilyApi.getAssetContent(familyId, asset.id);
      setMediaUrl((current) => {
        if (current) URL.revokeObjectURL(current);
        return URL.createObjectURL(blob);
      });
    } catch (error) {
      setPlaying(null);
      message.error(error instanceof Error ? error.message : String(error));
    } finally {
      setMediaLoading(false);
    }
  };

  const closePlayer = () => {
    setPlaying(null);
    setMediaUrl((current) => {
      if (current) URL.revokeObjectURL(current);
      return "";
    });
  };

  const normalizedQuery = query.trim().toLocaleLowerCase();
  const visibleAssets = assets.filter(
    (asset) =>
      (filter === "ALL" || asset.asset_type === filter) &&
      (!normalizedQuery ||
        asset.name.toLocaleLowerCase().includes(normalizedQuery)),
  );

  return (
    <Card className={styles.panelCard}>
      <div className={styles.mediaToolbar}>
        <Segmented<MediaFilter>
          value={filter}
          onChange={setFilter}
          options={[
            { label: t("family.mediaAll", "全部"), value: "ALL" },
            { label: t("family.mediaVideos", "视频"), value: "VIDEO" },
            { label: t("family.mediaAudio", "音频"), value: "AUDIO" },
          ]}
        />
        <Input
          allowClear
          prefix={<Search size={15} />}
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder={t("family.searchMedia", "搜索影音文件")}
        />
        <Button icon={<RefreshCw size={15} />} onClick={() => void load()}>
          {t("common.refresh", "刷新")}
        </Button>
      </div>

      <Spin spinning={loading}>
        <List
          dataSource={visibleAssets}
          locale={{
            emptyText: (
              <Empty
                description={t(
                  "family.noMedia",
                  "暂无影音，请先在家庭资产中扫描服务器目录",
                )}
              />
            ),
          }}
          renderItem={(asset) => (
            <List.Item
              actions={[
                <Button
                  key="play"
                  type="primary"
                  ghost
                  icon={<Play size={15} />}
                  onClick={() => void openMedia(asset)}
                >
                  {t("family.playMedia", "播放")}
                </Button>,
              ]}
            >
              <List.Item.Meta
                avatar={
                  asset.asset_type === "VIDEO" ? (
                    <Film size={24} />
                  ) : (
                    <Music size={24} />
                  )
                }
                title={asset.name}
                description={
                  <Space wrap>
                    <Tag>{asset.asset_type}</Tag>
                    <span>{formatBytes(asset.size_bytes)}</span>
                    <Typography.Text type="secondary" ellipsis>
                      {asset.uri}
                    </Typography.Text>
                  </Space>
                }
              />
            </List.Item>
          )}
        />
      </Spin>

      <Modal
        title={playing?.name}
        open={playing !== null}
        footer={null}
        destroyOnHidden
        width={880}
        onCancel={closePlayer}
      >
        <Spin spinning={mediaLoading}>
          {mediaUrl && playing?.asset_type === "VIDEO" && (
            <video
              className={styles.mediaPlayer}
              src={mediaUrl}
              controls
              autoPlay
            />
          )}
          {mediaUrl && playing?.asset_type === "AUDIO" && (
            <audio
              className={styles.mediaPlayer}
              src={mediaUrl}
              controls
              autoPlay
            />
          )}
        </Spin>
      </Modal>
    </Card>
  );
}
