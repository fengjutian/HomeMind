/**
 * Memory page (Stage 8).
 *
 * Thin wrapper: the four-view review queue lives in the shared
 * `MemoryCandidateReview` component so `/family`'s overview panel and
 * this standalone page stay in sync.
 */
import MemoryCandidateReview from "../../components/family/MemoryCandidateReview";
import FamilyPageShell from "../../components/family/FamilyPageShell";

export default function FamilyMemory() {
  return (
    <FamilyPageShell title="家庭记忆">
      <MemoryCandidateReview />
    </FamilyPageShell>
  );
}
