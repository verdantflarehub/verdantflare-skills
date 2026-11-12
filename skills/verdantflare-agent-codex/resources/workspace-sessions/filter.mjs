// VerdantFlare optional UI filter. No storage, authentication or execution writes.
export function normalizePath(value) {
  if (typeof value !== "string" || value.includes("\0")) return null;
  let path = value.replaceAll("\\", "/");
  if (path.toLowerCase().startsWith("//?/unc/")) path = "//" + path.slice(8);
  else if (path.startsWith("//?/")) path = path.slice(4);
  if (!/^(?:[a-z]:\/|\/\/[^/]+\/[^/]+(?:\/|$))/i.test(path)) return null;
  const unc = path.startsWith("//");
  const parts = path.split("/").filter(Boolean);
  const minimum = unc ? 2 : 1;
  const out = parts.slice(0, minimum);
  for (const part of parts.slice(minimum)) {
    if (part === ".") continue;
    if (part === "..") { if (out.length === minimum) return null; out.pop(); }
    else out.push(part);
  }
  return (unc ? "//" : "") + out.join("/").toLowerCase();
}

export function belongs(cwd, roots, canonical = {}) {
  const candidates = [normalizePath(cwd), normalizePath(canonical[cwd])].filter(Boolean);
  return (roots ?? []).some(root => [normalizePath(root), normalizePath(canonical[root])]
    .filter(Boolean).some(base => candidates.some(path => path === base || path.startsWith(base + "/"))));
}

export function visible(row, roots, canonical = {}) {
  return row?.hostId === "local" && row.threadSource !== "chatgpt_hidden" &&
    row.threadSource !== "ambient_suggestions" && row.ephemeral !== true &&
    belongs(row.cwd, roots, canonical);
}

export async function readAllThreads(send, cancelled = () => false) {
  const rows = new Map(), cursors = new Set();
  let cursor = null;
  do {
    if (cancelled()) return null;
    const page = await send("thread/list", {
      limit: 100, cursor, archived: false, sortKey: "updated_at",
      modelProviders: null, sourceKinds: null, useStateDbOnly: true,
    });
    if (cancelled()) return null;
    if (!Array.isArray(page?.data)) throw new Error("Invalid thread list response");
    for (const thread of page.data) {
      if (typeof thread?.id !== "string") throw new Error("Invalid thread identity");
      rows.set(thread.id, thread);
    }
    cursor = page.nextCursor ?? null;
    if (cursor !== null) {
      if (typeof cursor !== "string" || cursors.has(cursor)) throw new Error("Repeated thread cursor");
      cursors.add(cursor);
    }
  } while (cursor !== null);
  return [...rows.values()];
}

export function mergeRows(scanned, live, roots, canonical, suppressed = new Set()) {
  const rows = new Map();
  for (const row of [...scanned, ...live]) {
    if (visible(row, roots, canonical) && !suppressed.has(row.conversationId)) rows.set(row.conversationId, row);
  }
  return [...rows.values()].sort((a, b) => (b.recencyAt ?? b.updatedAt ?? 0) - (a.recencyAt ?? a.updatedAt ?? 0));
}

export function allowsTask(task, roots, canonical) {
  if (task.kind !== "local") return false;
  if (task.conversation) return visible(task.conversation, roots, canonical);
  // Unknown pending records are hidden until their real conversation is published.
  const pending = task.pendingThreadStart ?? task.pendingWorktree;
  return pending?.hostId === "local" && belongs(pending.cwd, roots, canonical);
}

export function createStore({ manager, roots, canonicalize }) {
  let state = { rows: [], canonical: {}, suppressed: new Set(), loading: true, error: false };
  let generation = 0, pending = null, disposed = false;
  const listeners = new Set(), observed = new Map();
  const publish = update => { state = { ...state, ...update }; for (const fn of listeners) fn(); };
  async function refresh() {
    if (disposed) return;
    if (pending) return pending;
    const current = ++generation;
    const cancelled = () => disposed || generation !== current;
    publish({ loading: true, error: false });
    pending = (async () => {
      try {
        const threads = await readAllThreads((method, params) => manager.sendRequest(method, params), cancelled);
        if (threads === null) return;
        const paths = [...new Set([...roots, ...threads.map(t => t.cwd).filter(p => typeof p === "string")])];
        const canonical = await canonicalize(paths);
        if (cancelled()) return;
        const selected = threads.filter(t => visible({ ...t, hostId: "local" }, roots, canonical));
        const changed = selected.filter(t => observed.get(t.id) !== JSON.stringify(t));
        if (changed.length) await manager.observeCatalogThreads(changed);
        if (cancelled()) return;
        const rows = (await Promise.all(selected.map(t => manager.getThreadListSummary(t.id)))).filter(Boolean);
        const suppressed = new Set();
        for (const row of rows) if (await manager.isConversationSuppressedAfterArchive(row.conversationId)) suppressed.add(row.conversationId);
        if (cancelled()) return;
        for (const thread of changed) observed.set(thread.id, JSON.stringify(thread));
        publish({ rows, canonical, suppressed, loading: false, error: false });
      } catch {
        if (!cancelled()) publish({ loading: false, error: true });
      } finally { pending = null; }
    })();
    return pending;
  }
  return {
    snapshot: () => state,
    subscribe(fn) { listeners.add(fn); return () => listeners.delete(fn); },
    refresh,
    async updateSuppression() {
      const current = generation;
      try {
        const checks = await Promise.all(state.rows.map(async row => [row.conversationId,
          await manager.isConversationSuppressedAfterArchive(row.conversationId)]));
        if (disposed || current !== generation) return;
        const suppressed = new Set(checks.filter(([, yes]) => yes).map(([id]) => id));
        if ([...suppressed].join() !== [...state.suppressed].join()) publish({ suppressed });
      } catch { if (!disposed) publish({ error: true }); }
    },
    dispose() { disposed = true; generation++; listeners.clear(); },
  };
}

// One scanner per React scope and workspace. Hook state stays outside native caches.
export function createHooks({ React, useRoots, useScope, rawQuery, managerFor, canonicalize }) {
  const scopes = new WeakMap();
  function useContext() {
    const roots = useRoots();
    const scope = useScope();
    const key = JSON.stringify(roots ?? null);
    let entries = scopes.get(scope);
    if (!entries) { entries = new Map(); scopes.set(scope, entries); }
    let entry = entries.get(key);
    if (!entry) {
      const store = createStore({ manager: managerFor(scope), roots: roots ?? [], canonicalize });
      entry = { store, users: 0, roots: roots ?? [], key, timer: null };
      entries.set(key, entry);
    }
    const current = entry;
    const snapshot = React.useSyncExternalStore(current.store.subscribe, current.store.snapshot);
    React.useEffect(() => {
      current.users++;
      const refresh = () => { if (document.visibilityState !== "hidden" && current.roots.length) current.store.refresh(); };
      if (current.users === 1) {
        refresh();
        current.timer = setInterval(refresh, 30000);
        window.addEventListener("focus", refresh);
        window.addEventListener("open-recent-tasks-menu", refresh);
        document.addEventListener("visibilitychange", refresh);
        current.cleanup = () => {
          clearInterval(current.timer);
          window.removeEventListener("focus", refresh);
          window.removeEventListener("open-recent-tasks-menu", refresh);
          document.removeEventListener("visibilitychange", refresh);
        };
      }
      return () => {
        if (--current.users === 0) {
          current.cleanup();
          // Do not dispose a store captured by a React StrictMode effect remount.
          queueMicrotask(() => { if (current.users === 0) { current.store.dispose(); entries.delete(key); } });
        }
      };
    }, [current]);
    return React.useMemo(() => ({ ...snapshot, roots: current.roots, refresh: current.store.refresh,
      store: current.store, pendingRoots: roots === undefined }), [snapshot, current, roots === undefined]);
  }
  function useRows() {
    const query = rawQuery();
    const ctx = useContext();
    React.useEffect(() => { ctx.store.updateSuppression(); }, [ctx.store, query.data]);
    const data = React.useMemo(() => mergeRows(ctx.rows, query.data ?? [], ctx.roots, ctx.canonical, ctx.suppressed),
      [ctx.rows, query.data, ctx.roots, ctx.canonical, ctx.suppressed]);
    return { ...query, data };
  }
  function Notice() {
    const ctx = useContext();
    if (ctx.pendingRoots || (ctx.roots.length && ctx.loading && !ctx.rows.length))
      return React.createElement("div", { role: "status", className: "px-panel text-xs text-tertiary" }, "正在读取当前项目的会话…");
    if (!ctx.roots.length)
      return React.createElement("div", { role: "status", className: "px-panel text-xs text-tertiary" }, "打开项目文件夹后显示该项目的会话。");
    if (ctx.error) return React.createElement("div", { role: "alert", className: "px-panel text-xs text-tertiary" },
      "项目会话未能完整读取。", React.createElement("button", { type: "button", onClick: ctx.refresh }, "重试"));
    return null;
  }
  return { useRows, useContext, Notice };
}
