import { fireEvent, screen, waitFor } from "@testing-library/react";

import type { ChatAttachmentDraftProjection } from "@/lib/api";

export function draftProjection(
  objectId: string,
  filename: string,
  mediaType = "image/png",
  ingestionStatus = "none",
  ingestionError: string | null = null
): ChatAttachmentDraftProjection {
  return {
    object_id: objectId,
    original_filename: filename,
    media_type: mediaType,
    content_length: 1024,
    content_hash: "hash",
    ingestion_status: ingestionStatus,
    ingestion_error: ingestionError,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  };
}

export const PDF_MEDIA_TYPE = "application/pdf";

export function pngFile(name = "截图.png", size = 100): File {
  return new File([new Uint8Array(size)], name, { type: "image/png" });
}

export function pdfFile(name = "讲义.pdf", size = 100): File {
  return new File([new Uint8Array(size)], name, { type: "application/pdf" });
}

export function pickFiles(files: File[]) {
  const composer = screen.getByTestId("composer");
  const fileInput = composer.querySelector('input[type="file"]') as HTMLInputElement;
  if (!fileInput) throw new Error("缺少隐藏的文件选择 input");
  Object.defineProperty(fileInput, "files", { value: files, configurable: true });
  fireEvent.change(fileInput);
}

export async function waitForThumb(filename: string) {
  return await waitFor(() => screen.getAllByText(filename)[0]);
}
