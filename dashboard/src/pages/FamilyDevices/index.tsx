/**
 * Devices page (Stage 8).
 *
 * Wraps the existing `Family/DevicesPanel` rather than copying it —
 * the spec asks for reusable components, not duplicated panels.
 */
import DevicesPanel from "../Family/DevicesPanel";
import FamilyPageShell from "../../components/family/FamilyPageShell";
import { useActiveFamily } from "../../hooks/useActiveFamily";
import { Button } from "antd";
import { Wifi } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";

export default function FamilyDevices() {
  const navigate = useNavigate();
  const { t } = useTranslation();
  return (
    <FamilyPageShell
      title={t("smartHome.pairedDevices")}
      actions={
        <Button
          icon={<Wifi size={16} />}
          onClick={() => navigate("/smart-home")}
        >
          {t("smartHome.title")}
        </Button>
      }
    >
      <DevicesPanelHoster />
    </FamilyPageShell>
  );
}

/** Reads the active family itself so the panel keeps working if it is
 *  reused outside this page. */
function DevicesPanelHoster() {
  const { familyId } = useActiveFamily();
  return familyId === null ? null : <DevicesPanel familyId={familyId} />;
}
