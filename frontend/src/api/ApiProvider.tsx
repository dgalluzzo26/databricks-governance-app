/**
 * @file React context that supplies the {@link ApiServices} bundle to the component tree.
 *
 * Tests inject fake services through {@link ApiProvider}; production code gets real
 * HTTP-backed services by default.
 */
import { createContext, useContext, useState, type ReactNode } from "react";
import { createApiServices, type ApiServices } from "./services";

/** Holds the active services bundle; `null` means no {@link ApiProvider} is mounted above the consumer. */
const ApiContext = createContext<ApiServices | null>(null);

/**
 * Provides API services to descendants via {@link useApi}.
 *
 * @remarks
 * The services value is captured once on first render and never changes afterwards, so
 * swapping the `services` prop later has no effect.
 *
 * @param props.services - Optional services to use instead of {@link createApiServices} (e.g. test fakes).
 * @param props.children - Subtree that may call {@link useApi}.
 */
export function ApiProvider({ services, children }: { services?: ApiServices; children: ReactNode }) {
  const [value] = useState(() => services ?? createApiServices());
  return <ApiContext.Provider value={value}>{children}</ApiContext.Provider>;
}

/**
 * Returns the {@link ApiServices} from the nearest {@link ApiProvider}.
 *
 * @returns The services bundle.
 * @throws Error When called outside an `<ApiProvider>`.
 */
export function useApi(): ApiServices {
  const services = useContext(ApiContext);
  if (!services) {
    throw new Error("useApi must be used inside <ApiProvider>");
  }
  return services;
}
