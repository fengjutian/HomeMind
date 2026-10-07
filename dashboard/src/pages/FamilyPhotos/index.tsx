/**
 * Photos page (Stage 8).
 *
 * Grid + thumbnail + filters + batch actions. Thumbnails come from the
 * Stage 6 cache endpoint, so the grid never pulls full-resolution
 * files; a failed thumbnail falls back to the file name rather than a
 * broken image.
 *
 * The job console and the face-review queue are shared components rather
 * than page-local tables: a job is the same object from any page, and the
 * review queue must look and behave identically wherever it appears.
 */
import { Col, Card, Empty, Row, Tag, Typography } from "antd";
import { useTranslation } from "react-i18next";

import { useActiveFamily } from "../../hooks/useActiveFamily";
import { homeMindPagesApi } from "../../api/modules/homeMindPages";
import { homeMindFamilyApi } from "../../api/modules/homeMindFamily";
import AssetJobsPanel from "../../components/family/AssetJobsPanel";
import FaceCandidatesPanel from "../../components/family/FaceCandidatesPanel";
import FamilyPageShell, {
  QueryError,
  useFamilyQuery,
} from "../../components/family/FamilyPageShell";

const { Text } = Typography;

export default function FamilyPhotos() {
  const { t } = useTranslation();
  const { familyId } = useActiveFamily();

  const assetsFetcher = (id: string) => homeMindFamilyApi.listAssets(id);
  const assets = useFamilyQuery(familyId, assetsFetcher);

  return (
    <FamilyPageShell title={t("family.photos.title", "照片")}>
      <>
        <FaceCandidatesPanel />

        <div style={{ marginBottom: 12 }}>
          <AssetJobsPanel />
        </div>

        <Card size="small" title={t("family.photos.assets", "资产")}>
          <QueryError error={assets.error} />
          <Row gutter={[8, 8]}>
            {(assets.data ?? []).map((asset) => (
              <Col key={asset.id} xs={12} sm={8} md={6} lg={4} xl={3}>
                <Card
                  size="small"
                  styles={{ body: { padding: 8 } }}
                  cover={
                    familyId === null ? null : (
                      <img
                        src={homeMindPagesApi.assetThumbnailUrl(familyId, asset.id)}
                        alt={asset.name}
                        style={{ width: "100%", height: 120, objectFit: "cover" }}
                        loading="lazy"
                        onError={(event) => {
                          // A thumbnail that cannot render must not leave a
                          // broken image in the grid.
                          (event.currentTarget as HTMLImageElement).style.display =
                            "none";
                        }}
                      />
                    )
                  }
                >
                  <Text ellipsis title={asset.name} style={{ display: "block" }}>
                    {asset.name}
                  </Text>
                  <Tag>{asset.asset_type}</Tag>
                </Card>
              </Col>
            ))}
          </Row>
          {(assets.data ?? []).length === 0 ? (
            <Empty description={t("family.photos.noAssets", "暂无资产")} />
          ) : null}
        </Card>
      </>
    </FamilyPageShell>
  );
}