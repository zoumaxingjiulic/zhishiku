import { readonly, ref } from "vue";

export interface TemporaryPasswordNotice {
  username: string;
  temporaryPassword: string;
}

export function useTemporaryPassword() {
  const notice = ref<TemporaryPasswordNotice | null>(null);

  function show(username: string, temporaryPassword: string): void {
    notice.value = { username, temporaryPassword };
  }

  function clear(): void {
    notice.value = null;
  }

  return { notice: readonly(notice), show, clear };
}
