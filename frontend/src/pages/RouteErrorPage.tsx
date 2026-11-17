/**
 * @file Route error boundary: shows a crash in a page instead of a blank screen.
 */
import { useEffect } from "react";
import { isRouteErrorResponse, useRouteError } from "react-router-dom";
import { recordError } from "@/app/errorLog";
import { ErrorAlert, PageHeader } from "@/components/ui";
import styles from "./RouteErrorPage.module.css";

/**
 * Rendered by React Router when a page throws while rendering or loading.
 * @remarks The error is also recorded in the session error log for the Activity panel.
 */
export function RouteErrorPage() {
  const error = useRouteError();
  const shown = isRouteErrorResponse(error) ? new Error(`${error.status} ${error.statusText}`) : error;

  useEffect(() => {
    recordError(shown, "render", window.location.pathname);
  }, [shown]);

  return (
    <>
      <PageHeader title="This page hit an error" description="The rest of the app still works." />
      <ErrorAlert error={shown} title="Something went wrong while showing this page" />
      <div className={styles.actions}>
        <button type="button" className={styles.button} onClick={() => window.location.reload()}>
          Reload the page
        </button>
      </div>
    </>
  );
}
