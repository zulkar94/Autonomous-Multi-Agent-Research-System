import { useState } from "react";
import { api, storeTokens } from "../lib/api";

interface Props {
  onSignedIn: () => void;
}

export function SignIn({ onSignedIn }: Props) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(mode: "login" | "register") {
    setError(null);
    setBusy(true);
    try {
      if (mode === "register") await api.register(email, password);
      storeTokens(await api.login(email, password));
      onSignedIn();
    } catch (err) {
      setError(
        mode === "register"
          ? "Could not create the account. Use a different email, or a longer password."
          : "Email and password do not match an active account.",
      );
      void err;
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <h2>Sign in</h2>
      {error && <p className="notice">{error}</p>}
      <label htmlFor="email">Email</label>
      <input
        id="email"
        type="email"
        autoComplete="username"
        value={email}
        onChange={(event) => setEmail(event.target.value)}
      />
      <label htmlFor="password">Password (12 characters or more, mixed types)</label>
      <input
        id="password"
        type="password"
        autoComplete="current-password"
        value={password}
        onChange={(event) => setPassword(event.target.value)}
      />
      <div className="actions">
        <button disabled={busy} onClick={() => void submit("login")}>
          Sign in
        </button>
        <button className="ghost" disabled={busy} onClick={() => void submit("register")}>
          Create account
        </button>
      </div>
    </div>
  );
}
