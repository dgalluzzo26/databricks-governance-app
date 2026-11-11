/**
 * @file Renders a ```mermaid fenced block from the help docs as an SVG diagram.
 *
 * `mermaid` is imported lazily so the library only downloads when a diagram is on screen.
 * The diagram source is shown as a code block until rendering finishes, and stays visible if
 * rendering fails.
 */
import { useEffect, useId, useState } from "react";
import { usePreferences } from "@/app/preferences";
import styles from "./HelpPage.module.css";

/**
 * One Mermaid diagram, re-rendered when the app theme changes.
 *
 * @param props.source - Mermaid diagram definition.
 */
export function MermaidDiagram({ source }: { source: string }) {
  const { resolvedTheme } = usePreferences();
  const id = `mermaid-${useId().replace(/[^a-zA-Z0-9]/g, "")}`;
  const [svg, setSvg] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const { default: mermaid } = await import("mermaid");
        mermaid.initialize({
          startOnLoad: false,
          securityLevel: "strict",
          theme: resolvedTheme === "dark" ? "dark" : "default",
          flowchart: { htmlLabels: true, useMaxWidth: true },
        });
        const result = await mermaid.render(`${id}-${resolvedTheme}`, source);
        if (!cancelled) {
          setSvg(result.svg);
          setFailed(false);
        }
      } catch {
        if (!cancelled) setFailed(true);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [id, source, resolvedTheme]);

  if (svg && !failed) {
    return (
      <figure
        className={styles.diagram}
        aria-label="Flow diagram"
        // Mermaid output is sanitized (securityLevel "strict") and the source is our own docs.
        dangerouslySetInnerHTML={{ __html: svg }}
      />
    );
  }
  return (
    <pre data-lang="mermaid">
      <code>{source}</code>
    </pre>
  );
}
