import { Link } from "react-router-dom";
import { PageHeader } from "@/components/ui";

export function NotFoundPage() {
  return (
    <>
      <PageHeader title="Page not found" description="The page you are looking for does not exist." />
      <Link to="/">Back to home</Link>
    </>
  );
}
