export function formatDate(value?: string | null) {
  return value ? new Date(value).toLocaleString("zh-CN", { hour12: false }) : "—";
}

export function formatPageRange(start?: number | null, end?: number | null) {
  if (!start) return "";
  return end && end !== start ? `第 ${start}—${end} 页` : `第 ${start} 页`;
}

export function formatSize(bytes?: number | null) {
  if (bytes == null) return "—";
  return bytes < 1024 * 1024 ? `${(bytes / 1024).toFixed(1)} KB` : `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

export const statusMap: Record<string, [string, string]> = {
  succeeded: ["已完成", "success"], active: ["启用", "success"], indexed: ["已索引", "success"],
  queued: ["排队中", "pending"], running: ["处理中", "pending"], processing: ["处理中", "pending"],
  pending: ["待处理", "pending"], failed: ["失败", "failed"], "0": ["停用", "failed"], "1": ["启用", "success"],
};
