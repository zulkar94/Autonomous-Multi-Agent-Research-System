import { useCallback, useEffect, useRef, useState } from "react";
import { ReportView } from "./components/ReportView";
import { RunHistory } from "./components/RunHistory";
import { RunLauncher } from "./components/RunLauncher";
import { SignIn } from "./components/SignIn";
import { TraceLedger } from "./components/TraceLedger";
import {
  api,
  clearTokens,
  hasSession,
  openTrace,
  setSignOutHandler,
  type RunDetail,
  type RunSummary,
  type TraceEvent,
  type User,
} from "./lib/api";

export default function App() {
  const [user, setUser] = useState<User | null>(null);
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [events, setEvents] = useState<TraceEvent[]>([]);
  const [run, setRun] = useState<RunDetail | null>(null);
  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const stream = useRef<EventSource | null>(null);

  const signOut = useCallback(() => {
    stream.current?.close();
    clearTokens();
    setUser(null);
    setRuns([]);
    setRun(null);
    setEvents([]);
    setActiveRunId(null);
  }, []);

  useEffect(() => setSignOutHandler(signOut), [signOut]);

  const refreshHistory = useCallback(async () => {
    setRuns(await api.listRuns());
  }, []);

  const loadSession = useCallback(async () => {
    try {
      setUser(await api.me());
      await refreshHistory();
    } catch {
      signOut();
    }
  }, [refreshHistory, signOut]);

  useEffect(() => {
    if (hasSession()) void loadSession();
  }, [loadSession]);

  useEffect(() => () => stream.current?.close(), []);

  const openRun = useCallback(async (id: string) => {
    setError(null);
    setRun(await api.getRun(id));
  }, []);

  async function start(query: string, depth: number) {
    setError(null);
    setEvents([]);
    setRun(null);
    try {
      const created = await api.createRun(query, depth);
      setActiveRunId(created.id);
      const { ticket } = await api.streamTicket(created.id);
      stream.current?.close();
      stream.current = openTrace(
        created.id,
        ticket,
        (event) => setEvents((previous) => [...previous, event]),
        () => {
          setActiveRunId(null);
          void openRun(created.id);
          void refreshHistory();
        },
      );
      await refreshHistory();
    } catch (err) {
      setActiveRunId(null);
      setError(
        err instanceof Error && err.message.includes("quota")
          ? "Run quota reached. Wait for the window to reset before starting another."
          : "The run could not be started. Check the question length and try again.",
      );
    }
  }

  async function cancel() {
    if (!activeRunId) return;
    await api.cancelRun(activeRunId).catch(() => undefined);
  }

  async function remove(id: string) {
    await api.deleteRun(id).catch(() => undefined);
    if (run?.id === id) setRun(null);
    await refreshHistory();
  }

  return (
    <>
      <header className="masthead">
        <h1>Research console</h1>
        <span className="identity">
          {user ? (
            <>
              {user.email} ({user.role}){" "}
              <button
                className="ghost"
                onClick={() => {
                  void api.logout().then(signOut);
                }}
              >
                Sign out
              </button>
            </>
          ) : (
            "not signed in"
          )}
        </span>
      </header>

      <div className="docket">
        <TraceLedger events={events} />

        <main className="stage">
          {!user ? (
            <SignIn onSignedIn={() => void loadSession()} />
          ) : (
            <>
              {error && <p className="notice">{error}</p>}
              <RunLauncher
                running={activeRunId !== null}
                onStart={(query, depth) => void start(query, depth)}
                onCancel={() => void cancel()}
              />
              {run && <ReportView run={run} />}
              <h3>Recent runs</h3>
              <RunHistory
                runs={runs}
                onOpen={(id) => void openRun(id)}
                onDelete={(id) => void remove(id)}
              />
            </>
          )}
        </main>
      </div>
    </>
  );
}
