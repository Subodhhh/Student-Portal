// Photo papers: types and the in-browser photo shrinking (phone photos are several MB; the server accepts up to 2 MB per photo).

export type DraftFile = { draft_file_id: string; kind: "PAGE" | "ANSWER_KEY"; position: number; file_id: string; lines: number; ocr_error: string | null };
export type DraftQuestion = {
  number: number; question: string; option_a: string; option_b: string; option_c: string; option_d: string; correct: string; marks: string;
  negative_marks: string; explanation: string; topic: string; confidence: number; page: number | null; issues: string[]; needs_review: boolean;
  reviewed: boolean; has_diagram: boolean;
};
export type PhotoDraft = {
  draft_id: string; title: string; status: "DRAFT" | "ACCEPTED" | "DISCARDED"; provider_id: string; branch_id: string; subject_id: string;
  created_at: string; extracted_at: string | null; accepted_paper_version_id: string | null; files: DraftFile[]; questions: DraftQuestion[];
  issues: string[]; corrections: number; needs_review: number[];
};

export const MAX_BYTES = 1_900_000;
const MAX_EDGE = 2400;

/** Downscale to at most 2400 px on the long edge and re-encode as JPEG until under the size limit, honouring the camera orientation.
 * Falls back to the original file where the browser cannot decode it (the server then validates it as is). */
export async function shrinkPhoto(file: File): Promise<Blob> {
  if (!file.type.startsWith("image/") || typeof createImageBitmap !== "function") return file;
  try {
    const bitmap = await createImageBitmap(file, { imageOrientation: "from-image" } as ImageBitmapOptions);
    const scale = Math.min(1, MAX_EDGE / Math.max(bitmap.width, bitmap.height));
    const canvas = document.createElement("canvas");
    canvas.width = Math.round(bitmap.width * scale);
    canvas.height = Math.round(bitmap.height * scale);
    canvas.getContext("2d")!.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
    for (const quality of [0.88, 0.8, 0.7, 0.6]) {
      const blob = await new Promise<Blob | null>((r) => canvas.toBlob(r, "image/jpeg", quality));
      if (blob && blob.size <= MAX_BYTES) return blob;
    }
    return file;
  } catch {
    return file;
  }
}
