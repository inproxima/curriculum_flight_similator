import { useEffect, useState, type ReactNode } from "react";
import { initAuth, signIn, type AuthConfig } from "../lib/auth";

export function AuthGate({ children }: { children: ReactNode }) {
  const [state, setState] = useState<{ loading: boolean; config?: AuthConfig; signedIn: boolean; error?: string }>({ loading: true, signedIn: false });
  useEffect(() => {
    initAuth()
      .then(({ config, user }) => setState({ loading: false, config, signedIn: config.mode !== "oidc" || !!user }))
      .catch((e) => setState({ loading: false, signedIn: false, error: String(e) }));
  }, []);
  if (state.loading) return <div className="page muted">Loading…</div>;
  if (!state.signedIn) {
    return (
      <div className="page" style={{ maxWidth: 520, margin: "10vh auto" }}>
        <div className="card col">
          <h2>Curriculum Flight Simulator</h2>
          <p>Sign in with your institutional account to continue.</p>
          {state.error && <p className="error small">{state.error}</p>}
          <button className="primary" onClick={() => signIn()}>Sign in</button>
        </div>
      </div>
    );
  }
  return <>{children}</>;
}
