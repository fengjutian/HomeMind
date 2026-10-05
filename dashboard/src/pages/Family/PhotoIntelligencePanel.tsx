import { useCallback, useEffect, useState } from "react";
import {
  App,
  Button,
  Card,
  Empty,
  Form,
  Input,
  List,
  Modal,
  Select,
  Space,
  Switch,
  Tag,
  Typography,
} from "antd";
import { ScanFace, Sparkles } from "lucide-react";
import { useTranslation } from "react-i18next";

import {
  homeMindFamilyApi,
  type FamilyAsset,
  type FamilyMember,
  type PhotoIntelligence,
  type PhotoProvider,
  type SimilarPhoto,
} from "../../api/modules/homeMindFamily";
import FamilyAssetPreview from "./FamilyAssetPreview";
import styles from "./index.module.less";

interface PhotoIntelligencePanelProps {
  familyId: string;
  members: FamilyMember[];
}

export default function PhotoIntelligencePanel({
  familyId,
  members,
}: PhotoIntelligencePanelProps) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const [photos, setPhotos] = useState<FamilyAsset[]>([]);
  const [selectedId, setSelectedId] = useState<string>();
  const [analysis, setAnalysis] = useState<PhotoIntelligence | null>(null);
  const [similar, setSimilar] = useState<SimilarPhoto[]>([]);
  const [aiOpen, setAiOpen] = useState(false);
  const [working, setWorking] = useState(false);
  const [providers, setProviders] = useState<PhotoProvider[]>([]);
  const [aiForm] = Form.useForm();
  const visionProviderId = Form.useWatch("vision_provider_id", aiForm);

  const load = useCallback(async () => {
    try {
      const [rows, nextProviders] = await Promise.all([
        homeMindFamilyApi.listAssets(familyId, { asset_type: "PHOTO" }),
        homeMindFamilyApi.listPhotoProviders(),
      ]);
      setPhotos(rows);
      setProviders(nextProviders.filter((provider) => provider.enabled));
      setSelectedId((current) =>
        current && rows.some((row) => row.id === current)
          ? current
          : rows[0]?.id,
      );
    } catch (error) {
      message.error(error instanceof Error ? error.message : String(error));
    }
  }, [familyId, message]);

  useEffect(() => void load(), [load]);

  const selected = photos.find((photo) => photo.id === selectedId);

  const runLocal = async () => {
    if (!selectedId) return;
    setWorking(true);
    try {
      setAnalysis(
        await homeMindFamilyApi.analyzePhotoLocal(familyId, selectedId),
      );
      setSimilar(
        await homeMindFamilyApi.listSimilarPhotos(familyId, selectedId),
      );
    } finally {
      setWorking(false);
    }
  };

  if (photos.length === 0) {
    return (
      <Card className={styles.panelCard}>
        <Empty
          description={t(
            "family.noIndexedPhotos",
            "请先在家庭资产中扫描照片目录",
          )}
        />
      </Card>
    );
  }

  return (
    <div className={styles.photoLayout}>
      <Card
        className={styles.panelCard}
        title={t("family.photoIntelligence", "照片智能")}
      >
        <Space wrap className={styles.photoToolbar}>
          <Select
            showSearch
            optionFilterProp="label"
            className={styles.photoSelect}
            value={selectedId}
            onChange={(value) => {
              setSelectedId(value);
              setAnalysis(null);
              setSimilar([]);
            }}
            options={photos.map((photo) => ({
              value: photo.id,
              label: photo.name,
            }))}
          />
          <Button
            icon={<ScanFace size={16} />}
            loading={working}
            onClick={runLocal}
          >
            {t("family.localAnalysis", "本地相似度分析")}
          </Button>
          <Button
            type="primary"
            icon={<Sparkles size={16} />}
            onClick={() => setAiOpen(true)}
          >
            {t("family.aiAnalysis", "AI 内容分析")}
          </Button>
        </Space>
        {selected && (
          <div className={styles.selectedPhotoPreview}>
            <FamilyAssetPreview
              familyId={familyId}
              asset={selected}
              size={240}
              showName={false}
            />
            <Typography.Paragraph type="secondary" copyable>
              {selected.uri}
            </Typography.Paragraph>
          </div>
        )}
        {analysis && (
          <div className={styles.analysisCard}>
            <Typography.Paragraph>
              {analysis.description ||
                t("family.localAnalysisComplete", "本地特征已生成")}
            </Typography.Paragraph>
            <Space wrap>
              {analysis.scenes.map((value) => (
                <Tag color="blue" key={value}>
                  {value}
                </Tag>
              ))}
              {analysis.objects.map((value) => (
                <Tag key={value}>{value}</Tag>
              ))}
              {analysis.location_name && (
                <Tag color="green">{analysis.location_name}</Tag>
              )}
            </Space>
          </div>
        )}
      </Card>

      <Card
        className={styles.panelCard}
        title={t("family.faceReference", "家庭成员人脸参考")}
      >
        <Form
          form={aiForm}
          layout="inline"
          onFinish={async ({ member_id }) => {
            if (selectedId) {
              await homeMindFamilyApi.setFaceReference(
                familyId,
                selectedId,
                member_id,
              );
              message.success(t("family.faceReferenceSaved", "人脸参考已保存"));
            }
          }}
        >
          <Form.Item name="member_id" rules={[{ required: true }]}>
            <Select
              className={styles.inlineSelect}
              placeholder={t("family.selectMember", "选择成员")}
              options={members.map((member) => ({
                value: member.id,
                label: member.display_name,
              }))}
            />
          </Form.Item>
          <Button type="primary" htmlType="submit" disabled={!selectedId}>
            {t("common.save", "保存")}
          </Button>
          <Button
            danger
            disabled={!selectedId}
            onClick={async () => {
              if (selectedId) {
                await homeMindFamilyApi.deleteFaceReference(
                  familyId,
                  selectedId,
                );
                message.success(
                  t("family.faceReferenceRemoved", "人脸参考已移除"),
                );
              }
            }}
          >
            {t("common.delete", "删除")}
          </Button>
        </Form>
      </Card>

      <Card
        className={styles.panelCard}
        title={t("family.similarPhotos", "相似照片")}
      >
        <List
          dataSource={similar}
          locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} /> }}
          renderItem={(item) => {
            const photo = photos.find((row) => row.id === item.asset_id);
            return (
              <List.Item>
                <List.Item.Meta
                  title={
                    photo ? (
                      <FamilyAssetPreview familyId={familyId} asset={photo} />
                    ) : (
                      item.asset_id
                    )
                  }
                  description={`${t("family.hammingDistance", "差异距离")}: ${
                    item.hamming_distance
                  }`}
                />
              </List.Item>
            );
          }}
        />
      </Card>

      <Modal
        title={t("family.aiAnalysis", "AI 内容分析")}
        open={aiOpen}
        onCancel={() => setAiOpen(false)}
        footer={null}
      >
        <Form
          layout="vertical"
          initialValues={{ reverse_geocode: false, recognize_faces: false }}
          onFinish={async (values) => {
            if (!selectedId) return;
            setWorking(true);
            try {
              setAnalysis(
                await homeMindFamilyApi.analyzePhoto(
                  familyId,
                  selectedId,
                  values,
                ),
              );
              setAiOpen(false);
            } finally {
              setWorking(false);
            }
          }}
        >
          <Form.Item
            name="vision_provider_id"
            label={t("family.providerId", "视觉服务商数字 ID")}
            rules={[{ required: true }]}
          >
            <Select
              options={providers.map((provider) => ({
                value: provider.id,
                label: provider.name,
              }))}
              onChange={() => aiForm.setFieldValue("vision_model", undefined)}
            />
          </Form.Item>
          <Form.Item
            name="vision_model"
            label={t("family.visionModel", "视觉模型")}
            rules={[{ required: true }]}
          >
            <Select
              showSearch
              optionFilterProp="label"
              options={(
                providers.find((provider) => provider.id === visionProviderId)
                  ?.models ?? []
              ).map((model) => ({
                value: model.id,
                label: model.name ?? model.id,
              }))}
            />
          </Form.Item>
          <Form.Item
            name="embedding_provider_id"
            label={t("family.embeddingProviderId", "向量服务商数字 ID")}
          >
            <Select
              allowClear
              options={providers.map((provider) => ({
                value: provider.id,
                label: provider.name,
              }))}
            />
          </Form.Item>
          <Form.Item
            name="embedding_model"
            label={t("family.embeddingModel", "向量模型")}
          >
            <Input />
          </Form.Item>
          <Form.Item
            name="reverse_geocode"
            label={t("family.reverseGeocode", "解析拍摄地点")}
            valuePropName="checked"
          >
            <Switch />
          </Form.Item>
          <Form.Item
            name="recognize_faces"
            label={t("family.recognizeFaces", "识别家庭成员")}
            valuePropName="checked"
          >
            <Switch />
          </Form.Item>
          <Button type="primary" htmlType="submit" loading={working} block>
            {t("family.startAnalysis", "开始分析")}
          </Button>
        </Form>
      </Modal>
    </div>
  );
}
