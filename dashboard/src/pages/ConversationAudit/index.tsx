import { Navigate, useNavigate, useParams } from "react-router-dom";
import ConversationAuditDrawer from "../Chat/components/ConversationAuditDrawer";

export default function ConversationAuditPage() {
  const navigate = useNavigate();
  const { agentId, threadId } = useParams<{
    agentId: string;
    threadId: string;
  }>();

  if (!agentId || !threadId) return <Navigate to="/chat" replace />;

  return (
    <div style={{ position: "relative", width: "100%", height: "100%" }}>
      <ConversationAuditDrawer
        threadId={threadId}
        open
        standalone
        onClose={() => navigate(`/chat/${agentId}/${threadId}`)}
      />
    </div>
  );
}
