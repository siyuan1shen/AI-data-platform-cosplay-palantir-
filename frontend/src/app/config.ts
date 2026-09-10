const trimTrailingSlash = (value: string) => value.replace(/\/+$/, "");

export const appConfig = {
  apiBaseUrl: trimTrailingSlash(__API_BASE_URL__),
  useMocks: import.meta.env.VITE_USE_MOCKS === "true",
} as const;
