import { useQuery } from "@tanstack/react-query";
import { useApi } from "./ApiProvider";

export const queryKeys = {
  me: ["me"] as const,
  appInfo: ["app-info"] as const,
};

export function useCurrentUser() {
  const api = useApi();
  return useQuery({
    queryKey: queryKeys.me,
    queryFn: ({ signal }) => api.identity.me(signal),
    staleTime: 5 * 60_000,
  });
}

export function useAppInfo() {
  const api = useApi();
  return useQuery({
    queryKey: queryKeys.appInfo,
    queryFn: ({ signal }) => api.appInfo.get(signal),
    staleTime: 30_000,
  });
}
