import { useEffect, useRef, useState } from "react";
import { api, post } from "./api";

type Identity = {
  initialize: (config: {
    client_id: string;
    nonce: string;
    ux_mode: "popup";
    auto_select: false;
    button_auto_select: false;
    callback: (result: { credential: string }) => void;
  }) => void;
  renderButton: (
    element: HTMLElement,
    options: {
      type: "standard";
      theme: "outline";
      size: "large";
      text: "continue_with";
      shape: "rectangular";
      width: number;
    },
  ) => void;
  disableAutoSelect: () => void;
};
declare global {
  interface Window {
    google?: { accounts: { id: Identity } };
  }
}
let scriptPromise: Promise<Identity> | undefined;

function loadIdentity(): Promise<Identity> {
  if (window.google?.accounts.id)
    return Promise.resolve(window.google.accounts.id);
  if (!scriptPromise)
    scriptPromise = new Promise<Identity>((resolve, reject) => {
      const script = document.createElement("script");
      const fail = () => {
        clearTimeout(timer);
        script.remove();
        scriptPromise = undefined;
        reject(
          new Error(
            "Google sign-in could not load. Check your connection or browser content blocker, then retry.",
          ),
        );
      };
      const timer = setTimeout(fail, 15000);
      script.src = "https://accounts.google.com/gsi/client";
      script.async = true;
      script.defer = true;
      script.onload = () => {
        clearTimeout(timer);
        if (window.google?.accounts.id) resolve(window.google.accounts.id);
        else fail();
      };
      script.onerror = fail;
      document.head.appendChild(script);
    });
  return scriptPromise;
}

export function disableGoogleAutoSelect() {
  window.google?.accounts.id.disableAutoSelect();
}

export function GoogleSignIn({
  onSuccess,
  onDenied,
}: {
  onSuccess: () => Promise<void>;
  onDenied?: (message: string) => void;
}) {
  const container = useRef<HTMLDivElement>(null),
    config = useRef<Promise<{ client_id: string; nonce: string }> | null>(null),
    pending = useRef(false),
    denied = useRef(onDenied),
    success = useRef(onSuccess);
  const [error, setError] = useState(""),
    [ready, setReady] = useState(false),
    [busy, setBusy] = useState(false),
    [attempt, setAttempt] = useState(0);
  useEffect(() => {
    success.current = onSuccess;
    denied.current = onDenied;
  }, [onSuccess, onDenied]);
  useEffect(() => {
    let disposed = false;
    setReady(false);
    // Reuse the same nonce request across React StrictMode's effect replay.
    config.current ??= api("/auth/google/config");
    Promise.all([loadIdentity(), config.current])
      .then(([identity, settings]) => {
        if (disposed || !container.current) return;
        identity.initialize({
          client_id: settings.client_id,
          nonce: settings.nonce,
          ux_mode: "popup",
          auto_select: false,
          button_auto_select: false,
          callback: async (result) => {
            if (disposed || pending.current) return;
            pending.current = true;
            setBusy(true);
            setError("");
            try {
              await post("/auth/google", { credential: result.credential });
              if (!disposed) await success.current();
            } catch (e) {
              if (!disposed) {
                if (
                  e instanceof Error &&
                  e.message.startsWith("Access denied.") &&
                  denied.current
                ) {
                  denied.current(e.message);
                  return;
                }
                setError((e as Error).message);
                config.current = null;
                setAttempt((n) => n + 1);
              }
            } finally {
              pending.current = false;
              if (!disposed) setBusy(false);
            }
          },
        });
        identity.renderButton(container.current, {
          type: "standard",
          theme: "outline",
          size: "large",
          text: "continue_with",
          shape: "rectangular",
          width: Math.min(320, container.current.clientWidth),
        });
        setReady(true);
      })
      .catch((e) => {
        if (!disposed) {
          setError((e as Error).message);
          config.current = null;
        }
      });
    const target = container.current;
    return () => {
      disposed = true;
      target?.replaceChildren();
    };
  }, [attempt]);
  return (
    <div className="google-sign-in">
      {error && (
        <div className="notice" role="alert">
          {error}
        </div>
      )}
      <div
        ref={container}
        className={"google-button-host" + (busy ? " busy" : "")}
        aria-busy={busy}
      />
      {busy ? (
        <p className="small" role="status">
          Verifying your Google account…
        </p>
      ) : (
        !ready && (
          <p className="small" role="status">
            {error
              ? "Sign-in is unavailable."
              : "Loading secure Google sign-in…"}
          </p>
        )
      )}
      {!ready && error && (
        <button
          className="button secondary"
          onClick={() => {
            setError("");
            setAttempt((n) => n + 1);
          }}
        >
          Retry Google sign-in
        </button>
      )}
      <p className="small">
        Choose a Google account in the popup. You can choose another account if
        access is denied.
      </p>
    </div>
  );
}
