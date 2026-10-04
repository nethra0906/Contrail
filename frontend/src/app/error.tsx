"use client";

/**
 * App Router render-error boundary. Catches exceptions thrown while
 * rendering anything under this layout (e.g. a deck.gl layer in LiveMap.tsx
 * blowing up on bad data) so visitors see something in the app's own visual
 * language instead of Next's generic unstyled error page. This is a safety
 * net, not a feature - keep it minimal.
 */
export default function Error({ reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return (
    <div
      className="flex min-h-full flex-1 flex-col items-center justify-center gap-4 px-6 text-center"
      style={{ background: "var(--bg-base)" }}
    >
      <div
        className="text-sm font-semibold uppercase tracking-wide"
        style={{ color: "var(--status-error)" }}
      >
        Something went wrong
      </div>
      <p className="max-w-sm text-sm" style={{ color: "var(--text-secondary)" }}>
        Contrail hit an unexpected error while rendering this page. You can try again.
      </p>
      <button
        onClick={reset}
        data-cursor-hover
        className="rounded-lg border px-4 py-2 text-sm font-medium transition-colors"
        style={{
          borderColor: "var(--border-default)",
          background: "var(--bg-overlay)",
          color: "var(--text-primary)",
        }}
      >
        Try again
      </button>
    </div>
  );
}
