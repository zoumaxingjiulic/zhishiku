import { ref } from "vue";
import { api } from "./api";

export const authUser = ref<any>(null);
export const authReady = ref(false);

let restored = false;
let restorePromise: Promise<void> | null = null;

export function setAuthUser(user: any) {
  authUser.value = user;
  authReady.value = true;
  restored = true;
}

export function clearAuthUser() {
  authUser.value = null;
  authReady.value = true;
  restored = true;
}

export async function ensureAuth() {
  if (restored) return;
  if (!restorePromise) {
    restorePromise = api<any>("/api/v1/auth/me")
      .then(user => { authUser.value = user; })
      .catch(() => { authUser.value = null; })
      .finally(() => {
        restored = true;
        authReady.value = true;
      });
  }
  await restorePromise;
}
