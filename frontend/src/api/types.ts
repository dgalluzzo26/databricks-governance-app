export interface CurrentUser {
  userName: string;
  email: string;
  displayName: string;
  userId: string | null;
  groups: string[];
  isAdmin: boolean;
  workspaceHost: string | null;
}

export interface StorageHealth {
  backend: string;
  healthy: boolean;
  detail: string | null;
}

export interface AppInfo {
  appName: string;
  version: string;
  workspaceHost: string | null;
  authMode: string;
  warehouseId: string | null;
  appCatalog: string;
  appSchema: string;
  adminGroup: string;
  adminSecretScope: string;
  adminSpConfigured: boolean;
  storage: StorageHealth;
}
