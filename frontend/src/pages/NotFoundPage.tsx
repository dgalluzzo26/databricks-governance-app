/**
 * @file Catch-all 404 page for unknown application routes.
 */
import { Link } from "react-router-dom";
import { PageHeader } from "@/components/ui";

/**
 * Displays a simple "page not found" message with a link back to the home route.
 */
export function NotFoundPage() {
  return (
    <>
      <PageHeader title="Page not found" description="The page you are looking for does not exist." />
      <Link to="/">Back to home</Link>
    </>
  );
}
