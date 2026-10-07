/**
 * Devices page (Stage 8).
 *
 * Wraps the existing `Family/DevicesPanel` rather than copying it —
 * the spec asks for reusable components, not duplicated panels.
 */
import DevicesPanel from "../Family/DevicesPanel";
import FamilyPageShell from "../../components/family/FamilyPageShell";
import { useActiveFamily } from "../../hooks/useActiveFamily";

export default function FamilyDevices() {
  return (
    <FamilyPageShell title="设备">
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
