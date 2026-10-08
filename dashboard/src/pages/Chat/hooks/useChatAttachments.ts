import { useCallback, useRef, useState } from "react";
import type { ChangeEvent, ClipboardEvent, DragEvent } from "react";
import { useTranslation } from "react-i18next";
import { uploadFile } from "../../../api/modules/upload";
import {
  RESUMABLE_THRESHOLD_BYTES,
  ResumableUpload,
  type UploadProgress,
} from "../../../api/resumableUploader";
import { agentAttachmentAccessUrl } from "../../../utils/toolMediaBlocks";
import type { ChatAttachment } from "./useChat";
import { message as antMessage } from "@/utils/antdMessage";
import { apiErrorMessage } from "../../../utils/apiError";

import { inferAttachmentKind } from "../utils/chatAttachments";
import { useServerUploadLimit } from "../../../hooks/useServerUploadLimit";

interface ChatUploadResult {
  path: string;
  workspace_path: string;
  filename: string;
  media_type: string;
  url: string;
  access_url: string;
}

/** Resumable path for files above the threshold; small files keep XHR. */
async function uploadViaSession(
  file: File,
  agentId: string,
  onProgress: (p: UploadProgress) => void,
  register: (upload: ResumableUpload) => void,
): Promise<ChatUploadResult> {
  const upload = new ResumableUpload(file, {
    purpose: "CHAT_ATTACHMENT",
    agentId,
  });
  upload.onProgress(onProgress);
  register(upload);
  const completed = await upload.start();
  return {
    path: completed.path,
    workspace_path: completed.workspace_path,
    filename: completed.filename,
    media_type: completed.media_type,
    url: completed.url,
    access_url: completed.access_url,
  };
}

export function useChatAttachments(agentId: string | null | undefined) {
  const { t } = useTranslation();
  const { maxUploadBytes, maxUploadMb } = useServerUploadLimit();
  const [attachments, setAttachments] = useState<ChatAttachment[]>([]);
  const [uploading, setUploading] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const [uploadProgress, setUploadProgress] = useState<UploadProgress | null>(
    null,
  );
  const activeUploadRef = useRef<ResumableUpload | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const processFiles = useCallback(
    async (files: FileList | File[]) => {
      const fileArr = Array.from(files).filter((f) => {
        if (f.size > maxUploadBytes) {
          antMessage.error(
            t("upload.tooLarge", "File too large (max {{maxMb}}MB): {{name}}", {
              name: f.name,
              maxMb: maxUploadMb,
            }),
          );
          return false;
        }
        return true;
      });

      if (fileArr.length === 0) return;

      if (!agentId) {
        antMessage.error(t("upload.failed", "Upload failed"));
        return;
      }

      setUploading(true);
      try {
        const results = await Promise.all(
          fileArr.map(async (file) => {
            // Small files keep the single-request XHR experience; anything
            // above the threshold goes through the resumable session API so a
            // dropped connection does not restart the whole upload.
            const res =
              file.size > RESUMABLE_THRESHOLD_BYTES
                ? await uploadViaSession(
                    file,
                    agentId,
                    (p) =>
                      setUploadProgress(
                        p.status === "done" || p.status === "cancelled"
                          ? null
                          : p,
                      ),
                    (u) => {
                      activeUploadRef.current = u;
                    },
                  )
                : await uploadFile(agentId, file);
            const workspacePath = res.path || res.workspace_path;
            const previewUrl =
              res.access_url ||
              res.url ||
              (workspacePath
                ? agentAttachmentAccessUrl(
                    agentId,
                    workspacePath,
                    res.media_type,
                  )
                : "");
            return {
              url: previewUrl,
              filename: res.filename,
              mediaType: res.media_type,
              workspacePath,
              kind: inferAttachmentKind(file, res.media_type),
            } satisfies ChatAttachment;
          }),
        );
        setAttachments((prev) => [...prev, ...results]);
      } catch (err: unknown) {
        antMessage.error(
          apiErrorMessage(err, t("upload.failed", "Upload failed"), t),
        );
      } finally {
        setUploading(false);
        activeUploadRef.current = null;
        setUploadProgress(null);
      }
    },
    [agentId, maxUploadBytes, maxUploadMb, t],
  );

  const handleFileSelect = useCallback(() => {
    fileInputRef.current?.click();
  }, []);

  const handleFileChange = useCallback(
    (e: ChangeEvent<HTMLInputElement>) => {
      if (e.target.files && e.target.files.length > 0) {
        void processFiles(e.target.files);
      }
      e.target.value = "";
    },
    [processFiles],
  );

  const removeAttachment = useCallback((index: number) => {
    setAttachments((prev) => prev.filter((_, i) => i !== index));
  }, []);

  const clearAttachments = useCallback(() => {
    setAttachments([]);
  }, []);

  const restoreAttachments = useCallback((next: ChatAttachment[]) => {
    setAttachments(next.map((a) => ({ ...a })));
  }, []);

  const handlePaste = useCallback(
    (e: ClipboardEvent) => {
      const items = e.clipboardData?.items;
      if (!items) return;
      const pastedFiles: File[] = [];
      for (let i = 0; i < items.length; i++) {
        const item = items[i];
        if (item.kind === "file") {
          const file = item.getAsFile();
          if (file) pastedFiles.push(file);
        }
      }
      if (pastedFiles.length > 0) {
        e.preventDefault();
        void processFiles(pastedFiles);
      }
    },
    [processFiles],
  );

  const handleDragEnter = useCallback((e: DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDragOver(true);
  }, []);

  const handleDragLeave = useCallback((e: DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDragOver(false);
  }, []);

  const handleDragOver = useCallback((e: DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
  }, []);

  const handleDrop = useCallback(
    (e: DragEvent) => {
      e.preventDefault();
      e.stopPropagation();
      setDragOver(false);
      if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
        void processFiles(e.dataTransfer.files);
      }
    },
    [processFiles],
  );

  const cancelUpload = useCallback(() => {
    void activeUploadRef.current?.cancel();
  }, []);

  return {
    attachments,
    uploading,
    uploadProgress,
    cancelUpload,
    dragOver,
    fileInputRef,
    processFiles,
    handleFileSelect,
    handleFileChange,
    removeAttachment,
    clearAttachments,
    restoreAttachments,
    handlePaste,
    handleDragEnter,
    handleDragLeave,
    handleDragOver,
    handleDrop,
  };
}
