import { createContext, useContext, useState, type ReactNode } from "react";
import { createApiServices, type ApiServices } from "./services";

const ApiContext = createContext<ApiServices | null>(null);

export function ApiProvider({ services, children }: { services?: ApiServices; children: ReactNode }) {
  const [value] = useState(() => services ?? createApiServices());
  return <ApiContext.Provider value={value}>{children}</ApiContext.Provider>;
}

export function useApi(): ApiServices {
  const services = useContext(ApiContext);
  if (!services) {
    throw new Error("useApi must be used inside <ApiProvider>");
  }
  return services;
}
