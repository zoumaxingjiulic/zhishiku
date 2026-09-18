export class ApiError extends Error {
  constructor(message: string, public status: number) {
    super(message);
  }
}

export async function api<T = any>(path: string, options: RequestInit = {}): Promise<T> {
  const isForm = options.body instanceof FormData;
  const response = await fetch(path, {
    ...options,
    credentials: "same-origin",
    headers: {
      ...(isForm ? {} : { "Content-Type": "application/json" }),
      ...(options.headers || {}),
    },
  });
  const contentType = response.headers.get("content-type") || "";
  const body = contentType.includes("json") ? await response.json() : await response.text();
  if (!response.ok) {
    if (response.status === 401 && !path.includes("/auth/login")) {
      window.dispatchEvent(new CustomEvent("auth-expired"));
    }
    const detail = body?.detail;
    const readable = Array.isArray(detail) ? detail.map((x:any)=>`${x.loc?.slice(1).join('.')||'输入'}: ${x.msg}`).join('；') : detail;
    const message = response.status >= 500
      ? "平台服务暂时不可用，请稍后重试"
      : (typeof body === "string" ? body : readable) || `请求失败 (${response.status})`;
    throw new ApiError(message, response.status);
  }
  return body as T;
}
