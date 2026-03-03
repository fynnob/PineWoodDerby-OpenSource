/**
 * Pinewood Derby — Universal API Shim
 * Provides the same interface (sb.from, sb.channel, sb.storage, sb.functions)
 * whether the backend is Supabase or local FastAPI.
 *
 * Usage in every page:
 *   <script src="../config.js"></script>  (or "config.js" for root pages)
 *   <script src="../lib/api.js"></script>
 *   const sb = createClient();
 */

function createClient() {
  if (CONFIG.backend_mode === "supabase") {
    // Use the real Supabase JS client — loaded via CDN in each page
    return supabase.createClient(CONFIG.backend_url, CONFIG.anon_key);
  } else {
    return new LocalClient(CONFIG.backend_url);
  }
}

/* ================================================================
   LocalClient — mirrors the Supabase JS API using fetch + WebSocket
   ================================================================ */
class LocalClient {
  constructor(baseUrl) {
    this._base = baseUrl.replace(/\/$/, "");
    this._ws   = null;
    this._subs = [];   // [{table, event, filter, callback}]
    this._connectWs();
  }

  // ---- WebSocket realtime ------------------------------------
  _connectWs() {
    const wsUrl = this._base.replace(/^http/, "ws") + "/api/ws";
    const connect = () => {
      this._ws = new WebSocket(wsUrl);
      this._ws.onmessage = (e) => {
        try {
          const msg = JSON.parse(e.data);
          this._subs.forEach(sub => {
            if (sub.table === msg.table &&
                (sub.event === "*" || sub.event === msg.event)) {
              sub.callback({ new: msg.record, old: msg.record, eventType: msg.event });
            }
          });
        } catch {}
      };
      this._ws.onclose  = () => setTimeout(connect, 2000);
      this._ws.onerror  = () => {};
    };
    connect();
  }

  channel(name) {
    const subs = this._subs;
    return {
      on(event, filter, callback) {
        const tableFilter = typeof filter === "object" ? filter.table : filter;
        const eventFilter = typeof filter === "object" ? (filter.event || "*") : "*";
        subs.push({ table: tableFilter, event: eventFilter, callback });
        return this;
      },
      subscribe() { return this; },
      // Broadcast (announcer page uses ch.send)
      send(msg) {
        // For local mode, broadcast is NOT needed (no screen.html in local context by default)
        // We still expose the method so the page doesn't throw.
        return Promise.resolve();
      },
    };
  }

  // ---- Query builder -----------------------------------------
  from(table) { return new QueryBuilder(this._base, table); }

  // ---- Storage -----------------------------------------------
  get storage() {
    const base = this._base;
    return {
      from(bucket) {
        return {
          async upload(name, file, opts = {}) {
            const form = new FormData();
            form.append("file", file);
            const r = await fetch(
              `${base}/api/storage/upload?bucket=${encodeURIComponent(bucket)}&name=${encodeURIComponent(name)}`,
              { method: "POST", body: form }
            );
            if (!r.ok) return { error: { message: await r.text() } };
            return { error: null };
          },
          getPublicUrl(name) {
            return { data: { publicUrl: `${base}/api/storage/${bucket}/${name}` } };
          },
          async list(prefix, opts) {
            const r = await fetch(`${base}/api/storage/${bucket}?prefix=${prefix||""}`);
            if (!r.ok) return { data: null, error: { message: await r.text() } };
            return { data: await r.json(), error: null };
          },
        };
      },
    };
  }

  // ---- Edge functions ----------------------------------------
  get functions() {
    const base = this._base;
    return {
      async invoke(name, { body } = {}) {
        try {
          const r = await fetch(`${base}/api/functions/${name}`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
          });
          const data = await r.json();
          if (!r.ok) return { data: null, error: { message: data.detail || data.error || r.statusText } };
          return { data, error: null };
        } catch (e) {
          return { data: null, error: { message: e.message } };
        }
      },
    };
  }
}

/* ================================================================
   QueryBuilder — fluent interface matching Supabase JS
   sb.from("cars").select("*").eq("id", x).single()
   ================================================================ */
class QueryBuilder {
  constructor(base, table) {
    this._base       = base;
    this._table      = table;
    this._method     = "GET";
    this._body       = null;
    this._filters    = [];
    this._selectCols = "*";
    this._orderCol   = null;
    this._orderAsc   = true;
    this._single     = false;
    this._maySingle  = false;
    this._limitN     = null;
    this._inFilter   = null;
  }

  select(cols = "*")  { this._selectCols = cols; return this; }
  eq(col, val) {
    // Strip table qualifier (e.g. "heats.round_id" becomes "round_id")
    const c = col.includes(".") ? col.split(".").pop() : col;
    this._filters.push([c, "eq", val]); return this;
  }
  neq(col, val)        { this._filters.push([col, "neq", val]); return this; }
  in(col, vals)        { const c = col.includes('.') ? col.split('.').pop() : col; this._inFilter = { col: c, vals }; return this; }
  order(col, { ascending = true } = {}) {
    this._orderCol = col; this._orderAsc = ascending; return this;
  }
  limit(n)             { this._limitN = n; return this; }
  single()             { this._single    = true; return this; }
  maybeSingle()        { this._maySingle = true; return this; }

  insert(data) { this._method = "POST";   this._body = data; return this; }
  update(data) { this._method = "PATCH";  this._body = data; return this; }
  delete()     { this._method = "DELETE";                    return this; }
  upsert(data) { this._method = "POST";   this._body = data; return this; }

  then(resolve, reject) { return this._exec().then(resolve, reject); }

  async _exec() {
    const base = `${this._base}/api/${this._table}`;

    // ---- GET -------------------------------------------------
    if (this._method === "GET") {
      let fetchUrl = base;

      // race_state with embedded joins -> use /api/state (pre-joined endpoint)
      if (this._table === "race_state" && this._selectCols.includes("(")) {
        fetchUrl = `${this._base}/api/state`;
      }

      const params = new URLSearchParams();
      this._filters.forEach(([col, , val]) => {
        if (val !== undefined && val !== null) params.set(col, String(val));
      });
      if (this._inFilter) {
        this._inFilter.vals.forEach(v => params.append(this._inFilter.col, v));
      }
      // heats with embedded rounds -> ask server to include round data
      if (this._table === "heats" && this._selectCols.includes("rounds(")) {
        params.set("include_round", "true");
      }

      const url = fetchUrl + (params.toString() ? "?" + params : "");
      const r = await fetch(url, { headers: { "Content-Type": "application/json" } });
      if (!r.ok) return { data: null, error: { message: await r.text() } };

      let data = await r.json();
      if (!Array.isArray(data)) data = data !== null && data !== undefined ? [data] : [];

      // Client-side in-filter (server may already have filtered, but re-apply for safety)
      if (this._inFilter) {
        const { col, vals } = this._inFilter;
        const s = new Set(vals.map(String));
        data = data.filter(row => s.has(String(row[col])));
      }

      if (this._orderCol) {
        const k = this._orderCol, asc = this._orderAsc;
        data = [...data].sort((a, b) => {
          if (a[k] < b[k]) return asc ? -1 : 1;
          if (a[k] > b[k]) return asc ? 1  : -1;
          return 0;
        });
      }
      if (this._limitN !== null) data = data.slice(0, this._limitN);

      if (this._single)    return data.length ? { data: data[0], error: null } : { data: null, error: { message: "Not found" } };
      if (this._maySingle) return { data: data.length ? data[0] : null, error: null };
      return { data, error: null };
    }

    // ---- POST (insert / upsert) ------------------------------
    if (this._method === "POST") {
      let url = base;
      // heat_entries bulk insert
      if (this._table === "heat_entries" && Array.isArray(this._body)) {
        url = `${base}/bulk`;
      }
      const r = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(this._body),
      });
      if (!r.ok) return { data: null, error: { message: await r.text() } };
      const raw = await r.json();
      if (this._single) {
        const d = Array.isArray(raw) ? raw[0] : raw;
        return { data: d || null, error: null };
      }
      return { data: Array.isArray(raw) ? raw : [raw], error: null };
    }

    // ---- PATCH: mass update via in() -> loop individual PATCHes
    if (this._method === "PATCH" && this._inFilter && this._inFilter.col === "id") {
      await Promise.all(this._inFilter.vals.map(id =>
        fetch(`${base}/${id}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(this._body),
        })
      ));
      return { data: null, error: null };
    }

    // ---- PATCH / DELETE: build URL from first eq filter ------
    const eqFilters = this._filters.filter(([, op]) => op === "eq");
    let url;
    if (eqFilters.length > 0) {
      const [col, , val] = eqFilters[0];
      // race_state is a singleton — server does not use ID in path
      url = this._table === "race_state" ? base : `${base}/${val}`;
    } else {
      url = base;
    }

    const r = await fetch(url, {
      method:  this._method,
      headers: { "Content-Type": "application/json" },
      body: this._body ? JSON.stringify(this._body) : undefined,
    });
    if (!r.ok) return { data: null, error: { message: await r.text() } };

    if (this._method === "PATCH") {
      const data = await r.json();
      if (this._single) return { data, error: null };
      return { data: Array.isArray(data) ? data : [data], error: null };
    }
    return { data: null, error: null };
  }
}
