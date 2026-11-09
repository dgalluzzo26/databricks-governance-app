import { HttpClient } from "./httpClient";
import type { AppInfo, CurrentUser } from "./types";

export class IdentityApi {
  constructor(private readonly http: HttpClient) {}

  me(signal?: AbortSignal): Promise<CurrentUser> {
    return this.http.get<CurrentUser>("/me", signal);
  }
}

export class AppInfoApi {
  constructor(private readonly http: HttpClient) {}

  get(signal?: AbortSignal): Promise<AppInfo> {
    return this.http.get<AppInfo>("/app-info", signal);
  }
}

export interface ApiServices {
  identity: IdentityApi;
  appInfo: AppInfoApi;
}

export function createApiServices(http: HttpClient = new HttpClient()): ApiServices {
  return {
    identity: new IdentityApi(http),
    appInfo: new AppInfoApi(http),
  };
}
