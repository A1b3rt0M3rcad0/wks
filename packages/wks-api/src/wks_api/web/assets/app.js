import {
  el,
  button,
  badge,
  empty,
  availability,
  processing,
  kinds,
  origins,
  locator,
  size,
} from "./presentation.js";
const $ = (id) => document.getElementById(id);
const state = {
  csrf: "",
  namespace: "",
  namespaces: [],
  nsCursor: null,
  collections: [],
  collectionsCursor: null,
  summary: null,
  sources: [],
  sourceCursor: null,
  results: [],
  searchCursor: null,
  searchBody: null,
  view: "summary",
  collection: null,
  generation: 0,
  detailGeneration: 0,
  selected: null,
  selectedVersion: null,
  rid: null,
  readCursor: null,
  uploadMax: 64 * 1024 * 1024,
};
const errors = {
  "auth.unauthenticated": "Sua sessão terminou. Entre novamente.",
  "auth.csrf_invalid": "A sessão mudou. Recarregue a página e tente novamente.",
  "auth.scope_mismatch": "Esta credencial não permite acessar o workspace.",
  "source.not_found": "Esta fonte não está mais disponível no seu contexto.",
  "search.cursor_invalid": "Esta leitura expirou. Atualize para continuar.",
  "search.query_not_indexable": "Use palavras mais específicas para a busca.",
  "upload.too_large": "O arquivo excede o limite permitido.",
  "upload.checksum_mismatch":
    "O arquivo mudou durante o envio. Selecione-o novamente.",
  "operation.infrastructure_unavailable":
    "O serviço está temporariamente indisponível. Tente novamente.",
};
function notice(text) {
  $("notice").textContent = text;
  $("notice").hidden = !text;
}
function report(error) {
  notice(error.message || "Não foi possível concluir esta ação.");
}
let editorKeys = new Map();
let activeKeys = null;
let editorBusy = false;
async function request(path, options = {}) {
  const headers = new Headers(options.headers);
  if (options.body !== undefined && !(options.body instanceof Blob)) {
    headers.set("Content-Type", "application/json");
    options.body = JSON.stringify(options.body);
  }
  if (options.method && options.method !== "GET") {
    headers.set("X-WKS-CSRF", state.csrf);
    if (path.startsWith("/v1") && !(options.body instanceof Blob)) {
      let key = options.key;
      if (!key && activeKeys) {
        const keys = activeKeys;
        const digest = await crypto.subtle.digest(
          "SHA-256",
          new TextEncoder().encode(
            options.method + path + (options.body || ""),
          ),
        );
        const fingerprint = Array.from(new Uint8Array(digest), (b) =>
          b.toString(16).padStart(2, "0"),
        ).join("");
        key = keys.get(fingerprint) || crypto.randomUUID();
        keys.set(fingerprint, key);
      }
      headers.set("Idempotency-Key", key || crypto.randomUUID());
    }
  }
  const response = await fetch(path, {
    ...options,
    headers,
    credentials: "same-origin",
    cache: "no-store",
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    if (response.status === 401 && path !== "/app/session") {
      disconnect();
      $("login-error").textContent = errors["auth.unauthenticated"];
    }
    const error = new Error(
      errors[data.code] || "Não foi possível concluir a solicitação.",
    );
    error.code = data.code;
    throw error;
  }
  return data;
}
function disconnect() {
  state.generation++;
  state.detailGeneration++;
  state.csrf = "";
  state.selected = null;
  state.namespace = "";
  state.namespaces = [];
  state.collections = [];
  state.sources = [];
  state.results = [];
  $("detail").close();
  $("editor").close();
  $("workspace").hidden = true;
  $("connect").hidden = false;
  $("logout").hidden = true;
  $("token").value = "";
  for (const id of [
    "source-list",
    "recent-sources",
    "search-results",
    "detail-content",
    "collection-nav",
  ])
    $(id).replaceChildren();
}
async function connect() {
  const user = await request("/app/session");
  state.csrf = user.csrf_token;
  state.uploadMax = user.upload_max_bytes;
  $("client-name").textContent = user.name;
  $("connect").hidden = true;
  $("workspace").hidden = false;
  $("logout").hidden = false;
  const list = await request("/v1/namespaces");
  state.namespaces = list.items;
  state.nsCursor = list.next_cursor;
  renderNamespaces();
  await switchNamespace(state.namespaces[0]?.id || "");
}
$("login").addEventListener("submit", async (event) => {
  event.preventDefault();
  const submit = $("login").querySelector("button");
  submit.disabled = true;
  $("login-error").textContent = "";
  try {
    await request("/app/session", {
      method: "POST",
      body: { token: $("token").value },
    });
    $("token").value = "";
    await connect();
  } catch (error) {
    $("token").value = "";
    $("login-error").textContent = error.message;
  } finally {
    submit.disabled = false;
  }
});
$("logout").onclick = async () => {
  try {
    await request("/app/session", { method: "DELETE" });
    disconnect();
  } catch (error) {
    report(error);
  }
};
function renderNamespaces() {
  const select = $("namespace");
  select.replaceChildren();
  for (const ns of state.namespaces) {
    const option = el("option", ns.title);
    option.value = ns.id;
    select.append(option);
  }
  if (!state.namespaces.length) select.append(el("option", "Nenhum contexto"));
  select.value = state.namespace || state.namespaces[0]?.id || "";
  $("more-contexts").hidden = !state.nsCursor;
}
$("more-contexts").onclick = async () => {
  try {
    const page = await request(
      "/v1/namespaces?cursor=" + encodeURIComponent(state.nsCursor),
    );
    state.namespaces.push(...page.items);
    state.nsCursor = page.next_cursor;
    renderNamespaces();
  } catch (error) {
    report(error);
  }
};
$("namespace").onchange = () =>
  switchNamespace($("namespace").value).catch(report);
async function switchNamespace(id) {
  state.generation++;
  state.detailGeneration++;
  state.namespace = id;
  state.collection = null;
  state.collections = [];
  state.sources = [];
  state.results = [];
  state.searchCursor = null;
  state.sourceCursor = null;
  $("detail").close();
  $("editor").close();
  notice("");
  $("context-title").textContent =
    state.namespaces.find((n) => n.id === id)?.title || "Seu contexto";
  $("new-source").disabled = !id;
  $("namespace").value = id;
  showView("summary");
  $("metrics").replaceChildren();
  $("coverage").replaceChildren();
  $("recent-sources").replaceChildren(
    empty("Carregando biblioteca", "Consultando as fontes do contexto."),
  );
  $("source-list").replaceChildren();
  $("search-results").replaceChildren();
  $("collection-nav").replaceChildren();
  $("collections-list").replaceChildren();
  if (!id) {
    $("recent-sources").replaceChildren(
      empty(
        "Crie seu primeiro contexto",
        "Organize aqui as fontes que humanos e agentes poderão consultar.",
      ),
    );
    return;
  }
  await refresh();
}
async function refresh() {
  if (!state.namespace) return;
  const generation = state.generation,
    ns = state.namespace;
  const [summary, sources, collections] = await Promise.all([
    request(`/v1/namespaces/${ns}/summary`),
    request(sourcePath(false)),
    request(`/v1/namespaces/${ns}/collections`),
  ]);
  if (generation !== state.generation) return;
  state.summary = summary;
  state.sources = sources.items;
  state.sourceCursor = sources.next_cursor;
  state.collections = collections.items;
  state.collectionsCursor = collections.next_cursor;
  renderSummary();
  renderSources();
  renderCollections();
}
$("refresh").onclick = () => refresh().catch(report);
function showView(view) {
  state.view = view;
  for (const v of ["summary", "sources", "search", "collections"])
    $(v + "-view").hidden = view !== v;
  document.querySelectorAll("[data-view]").forEach((node) => {
    const selected = node.dataset.view === view;
    node.classList.toggle("selected", selected);
    if (node.getAttribute("role") === "tab")
      node.setAttribute("aria-selected", String(selected));
  });
  const titles = {
    summary: "Sua biblioteca de conhecimento",
    sources: "Fontes do contexto",
    search: "Encontre a passagem certa",
    collections: "Coleções da biblioteca",
  };
  $("page-title").textContent =
    state.collection && view === "sources"
      ? state.collection.title
      : titles[view];
  $("page-subtitle").textContent =
    state.collection && view === "sources"
      ? "Fontes selecionadas nesta coleção, sem duplicar originais."
      : "Fontes, versões e referências do seu contexto.";
}
document.querySelectorAll("[data-view]").forEach(
  (node) =>
    (node.onclick = () => {
      if (node.dataset.view === "sources" && state.collection) {
        state.collection = null;
        loadSources().catch(report);
      }
      showView(node.dataset.view);
    }),
);
$("see-all").onclick = () => showView("sources");
$("summary-search").onclick = () => {
  showView("search");
  $("query").focus();
};
function renderSummary() {
  const summary = state.summary,
    counts = summary.availability_counts;
  $("metrics").replaceChildren();
  for (const [label, count, caption] of [
    ["Fontes preservadas", summary.total_sources, "No contexto inteiro"],
    ["Texto disponível", counts.text_ready || 0, "Com representação publicada"],
    [
      "Cobertura parcial",
      counts.text_partial || 0,
      "Consulte a origem e os avisos",
    ],
    [
      "Processamentos ativos",
      summary.pending_jobs,
      "Na fila, em execução ou nova tentativa",
    ],
  ]) {
    const node = el("div", undefined, "metric");
    node.append(
      el("span", label, "metric-label"),
      el("strong", count),
      el("small", caption),
    );
    $("metrics").append(node);
  }
  $("coverage").replaceChildren();
  for (const [value, [label]] of Object.entries(availability)) {
    const row = el("div", undefined, "coverage-row");
    row.append(badge(value), el("strong", counts[value] || 0));
    $("coverage").append(row);
  }
  $("recent-sources").replaceChildren(
    ...state.sources.slice(0, 5).map(sourceRow),
  );
  if (!state.sources.length)
    $("recent-sources").append(
      empty(
        "Sua biblioteca está pronta para começar",
        "Adicione um texto, arquivo ou link de referência.",
      ),
    );
}
function sourceRow(source) {
  const row = el("article", undefined, "source-row");
  const info = el("div", undefined, "source-info");
  const title = button(
    source.title,
    () => openSource(source.id).catch(report),
    "source-name",
  );
  info.append(title);
  if (source.description) info.append(el("p", source.description));
  const version =
    source.versions.find((v) => v.id === source.current_version_id) ||
    source.versions.at(-1);
  info.append(
    el(
      "small",
      `${kinds[source.kind] || source.kind} · ${version ? "Versão " + version.revision_number : "Sem versão"}${version?.byte_size ? " · " + size(version.byte_size) : ""}`,
    ),
  );
  row.append(
    el("span", "▤", "source-symbol"),
    info,
    badge(source.availability),
  );
  if (
    source.processing &&
    ["queued", "running", "retry_wait", "failed"].includes(
      source.processing.state,
    )
  )
    info.append(el("p", processing[source.processing.state], "footnote"));
  return row;
}
function sourcePath(more) {
  const params = new URLSearchParams({
    namespace_id: state.namespace,
    page_size: "20",
  });
  if (state.collection) params.set("collection_id", state.collection.id);
  if ($("availability-filter").value)
    params.set("availability", $("availability-filter").value);
  if (more && state.sourceCursor) params.set("cursor", state.sourceCursor);
  return "/v1/sources?" + params;
}
let sourceRequest = 0;
async function loadSources(more = false) {
  if (!state.namespace) return;
  const generation = state.generation,
    sequence = ++sourceRequest;
  const result = await request(sourcePath(more));
  if (generation !== state.generation || sequence !== sourceRequest) return;
  state.sources = more ? [...state.sources, ...result.items] : result.items;
  state.sourceCursor = result.next_cursor;
  renderSources();
}
function renderSources() {
  $("source-list").replaceChildren(...state.sources.map(sourceRow));
  if (!state.sources.length)
    $("source-list").append(
      empty(
        "Nenhuma fonte nesta seleção",
        "Adicione uma fonte ou altere o filtro de cobertura.",
      ),
    );
  $("more-sources").hidden = !state.sourceCursor;
  $("list-caption").textContent =
    `${state.sources.length} fontes carregadas${state.sourceCursor ? " · há mais fontes" : ""}`;
}
$("more-sources").onclick = async () => {
  const node = $("more-sources");
  node.disabled = true;
  try {
    await loadSources(true);
  } catch (error) {
    report(error);
  } finally {
    node.disabled = false;
  }
};
$("availability-filter").onchange = () => loadSources().catch(report);
function renderCollections() {
  for (const id of ["collections-list", "collection-nav"])
    $(id).replaceChildren();
  const option = el("option", "Todo o contexto");
  option.value = "";
  $("search-collection").replaceChildren(option);
  for (const collection of state.collections) {
    const open = () => {
      state.collection = collection;
      showView("sources");
      loadSources().catch(report);
    };
    const card = button("", open, "collection-card");
    card.append(
      el("strong", collection.title),
      el("small", "Consultar fontes →"),
    );
    $("collections-list").append(card);
    $("collection-nav").append(button(collection.title, open));
    const opt = el("option", collection.title);
    opt.value = collection.id;
    $("search-collection").append(opt);
  }
  if (!state.collections.length)
    $("collections-list").append(
      empty(
        "Organize sua biblioteca",
        "Uma fonte pode participar de várias coleções.",
      ),
    );
  $("more-collections").hidden = !state.collectionsCursor;
}
$("more-collections").onclick = async () => {
  const generation = state.generation;
  try {
    const page = await request(
      `/v1/namespaces/${state.namespace}/collections?cursor=${encodeURIComponent(state.collectionsCursor)}`,
    );
    if (generation !== state.generation) return;
    state.collections.push(...page.items);
    state.collectionsCursor = page.next_cursor;
    renderCollections();
  } catch (error) {
    report(error);
  }
};
let searchRequest = 0;
$("search-form").onsubmit = (event) => {
  event.preventDefault();
  state.searchBody = {
    query: $("query").value,
    mode: $("search-mode").value,
    language: $("search-language").value,
    filters: {
      namespace_id: state.namespace,
      collection_ids: $("search-collection").value
        ? [$("search-collection").value]
        : [],
    },
    page_size: 10,
  };
  state.results = [];
  state.searchCursor = null;
  $("search-results").replaceChildren(
    empty(
      "Buscando passagens…",
      "Consultando as fontes autorizadas deste contexto.",
    ),
  );
  $("more-search").hidden = true;
  search(false).catch(report);
};
async function search(more) {
  if (!state.namespace) return;
  const generation = state.generation,
    sequence = ++searchRequest;
  const result = await request("/v1/search", {
    method: "POST",
    body: {
      ...state.searchBody,
      ...(more ? { cursor: state.searchCursor } : {}),
    },
  });
  if (generation !== state.generation || sequence !== searchRequest) return;
  state.results = more ? [...state.results, ...result.items] : result.items;
  state.searchCursor = result.next_cursor;
  $("search-results").replaceChildren();
  for (const hit of state.results) {
    const node = el("article", undefined, "result");
    node.append(
      button(
        hit.title || "Passagem da fonte",
        () =>
          openSource(
            hit.source_id,
            hit.source_version_id,
            hit.representation_id,
          ).catch(report),
        "source-name",
      ),
      el("p", hit.snippet),
    );
    const footer = el("div", undefined, "result-footer");
    footer.append(
      el("span", locator(hit.locator)),
      el("span", origins[hit.origin_kind] || hit.origin_kind),
      button(
        "Ler na fonte →",
        () =>
          openSource(
            hit.source_id,
            hit.source_version_id,
            hit.representation_id,
          ).catch(report),
        "text-button",
      ),
    );
    node.append(footer);
    $("search-results").append(node);
  }
  if (!state.results.length)
    $("search-results").append(
      empty(
        "Nenhuma passagem encontrada",
        "Tente palavras diferentes ou consulte a cobertura da fonte.",
      ),
    );
  $("search-caption").textContent =
    `${state.results.length} passagens carregadas${state.searchCursor ? " · há mais resultados" : ""}. A busca considera somente fontes autorizadas.`;
  $("more-search").hidden = !state.searchCursor;
}
$("more-search").onclick = async () => {
  const node = $("more-search");
  node.disabled = true;
  try {
    await search(true);
  } catch (error) {
    report(error);
  } finally {
    node.disabled = false;
  }
};
let editorSave = null;
function field(name, label, type = "text", value = "") {
  const wrap = el("div", undefined, "field"),
    caption = el("label", label);
  caption.htmlFor = "edit-" + name;
  let input;
  if (type === "textarea") input = el("textarea");
  else {
    input = el("input");
    input.type = type;
  }
  input.id = "edit-" + name;
  input.name = name;
  if (type !== "file") input.value = value;
  input.required = true;
  wrap.append(caption, input);
  $("editor-fields").append(wrap);
  return input;
}
function openEditor(title, setup, save) {
  if (editorBusy) return;
  editorKeys = new Map();
  $("editor-title").textContent = title;
  $("editor-fields").replaceChildren();
  $("editor-error").textContent = "";
  $("save-editor").textContent = "Salvar";
  editorSave = save;
  setup();
  $("editor").showModal();
}
$("close-editor").onclick = $("cancel-editor").onclick = () => {
  if (!editorBusy) $("editor").close();
};
$("editor").addEventListener("cancel", (event) => {
  if (editorBusy) event.preventDefault();
});
$("editor-form").onsubmit = async (event) => {
  event.preventDefault();
  const submit = $("save-editor");
  submit.disabled = true;
  editorBusy = true;
  activeKeys = editorKeys;
  $("editor-error").textContent = "";
  try {
    await editorSave();
    $("editor").close();
    await refresh();
  } catch (error) {
    $("editor-error").textContent =
      error instanceof TypeError
        ? "Conexão interrompida. Tente novamente para retomar a mesma solicitação."
        : error.message;
  } finally {
    editorBusy = false;
    activeKeys = null;
    submit.disabled = false;
  }
};
$("new-context").onclick = () =>
  openEditor(
    "Criar contexto",
    () => field("title", "Nome do contexto"),
    async () => {
      const ns = await request("/v1/namespaces", {
        method: "POST",
        body: { title: $("edit-title").value },
      });
      state.namespaces.push(ns);
      renderNamespaces();
      await switchNamespace(ns.id);
    },
  );
$("new-collection").onclick = () => {
  if (!state.namespace) return;
  openEditor(
    "Criar coleção",
    () => field("title", "Nome da coleção"),
    () =>
      request("/v1/collections", {
        method: "POST",
        body: { namespace_id: state.namespace, title: $("edit-title").value },
      }),
  );
};
function newSource() {
  if (!state.namespace) return;
  openEditor(
    "Adicionar fonte",
    () => {
      field("title", "Título da fonte");
      const wrap = el("div", undefined, "field"),
        label = el("label", "Tipo da fonte");
      label.htmlFor = "edit-kind";
      const select = el("select");
      select.id = "edit-kind";
      for (const [value, name] of Object.entries(kinds)) {
        const option = el("option", name);
        option.value = value;
        select.append(option);
      }
      wrap.append(label, select);
      $("editor-fields").append(wrap);
      const content = el("div");
      content.id = "edit-content";
      $("editor-fields").append(content);
      const contentField = () => {
        const parent = $("editor-fields");
        content.replaceChildren();
        const input = field(
          "payload",
          select.value === "text"
            ? "Conteúdo"
            : select.value === "upload"
              ? "Arquivo original"
              : "Endereço público HTTP(S)",
          select.value === "text"
            ? "textarea"
            : select.value === "upload"
              ? "file"
              : "url",
        );
        content.append(parent.lastElementChild);
        if (select.value === "upload") input.required = true;
      };
      select.onchange = contentField;
      contentField();
    },
    async () => {
      const kind = $("edit-kind").value;
      const body = {
        namespace_id: state.namespace,
        title: $("edit-title").value,
        kind,
      };
      let file;
      if (kind === "text")
        body.text = $("edit-content").querySelector("textarea").value;
      else if (kind === "bookmark")
        body.external_uri = $("edit-content").querySelector("input").value;
      else {
        file = $("edit-content").querySelector("input").files[0];
        if (file.size > state.uploadMax)
          throw new Error(`Limite de envio: ${size(state.uploadMax)}.`);
        const hash = await crypto.subtle.digest(
          "SHA-256",
          await file.arrayBuffer(),
        );
        body.upload = {
          filename: file.name,
          declared_media_type: file.type || "application/octet-stream",
          byte_size: file.size,
          checksum_sha256: Array.from(new Uint8Array(hash), (b) =>
            b.toString(16).padStart(2, "0"),
          ).join(""),
        };
      }
      const receipt = await request("/v1/sources", { method: "POST", body });
      if (file) {
        await request(`/v1/uploads/${receipt.upload_id}/content`, {
          method: "PUT",
          body: file,
        });
        await request(`/v1/uploads/${receipt.upload_id}/commit`, {
          method: "POST",
        });
      }
      notice(
        kind === "bookmark"
          ? "Referência salva. O endereço não foi capturado."
          : "Fonte preservada. Atualize para acompanhar o processamento.",
      );
    },
  );
}
$("new-source").onclick = $("summary-add").onclick = newSource;
$("close-detail").onclick = () => {
  state.detailGeneration++;
  $("detail").close();
};
async function openSource(id, versionId = null, representationId = null) {
  const sequence = ++state.detailGeneration,
    generation = state.generation;
  $("detail-title").textContent = "Consultando fonte…";
  $("detail-content").replaceChildren();
  if (!$("detail").open) $("detail").showModal();
  const source = await request("/v1/sources/" + id);
  if (sequence !== state.detailGeneration || generation !== state.generation)
    return;
  state.selected = source;
  state.selectedVersion =
    source.versions.find((v) => v.id === versionId) ||
    source.versions.find((v) => v.id === source.current_version_id) ||
    source.versions.at(-1);
  state.rid = representationId || state.selectedVersion?.representation_id;
  state.readCursor = null;
  $("detail-title").textContent = source.title;
  renderDetail();
  if (state.rid) await readPage(false, sequence);
}
function renderDetail() {
  const source = state.selected,
    version = state.selectedVersion,
    content = $("detail-content");
  content.replaceChildren();
  const meta = el("div", undefined, "detail-meta");
  meta.append(
    version?.id === source.current_version_id
      ? badge(source.availability)
      : el("span", "Versão histórica", "badge violet"),
    el("span", kinds[source.kind], "footnote"),
  );
  content.append(meta);
  if (source.description) content.append(el("p", source.description));
  if (source.processing && version?.id === source.current_version_id)
    content.append(
      el(
        "p",
        "Processamento: " +
          (processing[source.processing.state] || source.processing.state),
        "footnote",
      ),
    );
  const select = el("select", undefined, "version-select");
  select.id = "source-version";
  select.setAttribute("aria-label", "Versão da fonte");
  for (const v of source.versions) {
    const option = el(
      "option",
      `Versão ${v.revision_number}${v.id === source.current_version_id ? " · atual" : ""}`,
    );
    option.value = v.id;
    select.append(option);
  }
  select.value = version?.id || "";
  select.onchange = () => openSource(source.id, select.value).catch(report);
  content.append(select);
  if (version?.committed && source.kind !== "bookmark") {
    const link = el("a", "Baixar original");
    link.href = `/v1/sources/${source.id}/versions/${version.id}/original`;
    link.setAttribute("download", "");
    content.append(link);
  }
  const collection = el("div", undefined, "detail-section");
  collection.append(el("h3", "Adicionar à coleção"));
  const choice = el("select");
  choice.setAttribute("aria-label", "Coleção para esta fonte");
  for (const c of state.collections) {
    const opt = el("option", c.title);
    opt.value = c.id;
    choice.append(opt);
  }
  const add = button("Adicionar", async () => {
    add.disabled = true;
    try {
      await request(`/v1/collections/${choice.value}/sources/${source.id}`, {
        method: "PUT",
      });
      notice("Fonte adicionada à coleção.");
    } catch (error) {
      report(error);
    } finally {
      add.disabled = false;
    }
  });
  add.disabled = !state.collections.length;
  collection.append(choice, add);
  content.append(collection);
  const actions = el("div", undefined, "actions");
  actions.append(
    button("Adicionar versão", () => {
      const sid = source.id;
      openEditor(
        "Adicionar versão de texto",
        () => field("text", "Conteúdo da nova versão", "textarea"),
        async () => {
          await request(`/v1/sources/${sid}/versions`, {
            method: "POST",
            body: { text: $("edit-text").value },
          });
          await openSource(sid);
        },
      );
    }),
  );
  content.append(actions);
  const reading = el("div", undefined, "detail-section");
  reading.append(el("h3", "Conteúdo da representação"));
  const blocks = el("div");
  blocks.id = "blocks";
  reading.append(blocks);
  content.append(reading);
  if (!state.rid)
    blocks.append(
      empty(
        "Texto ainda indisponível",
        "O original foi preservado. Acompanhe o processamento ou consulte a cobertura.",
      ),
    );
  const technical = el("details", undefined, "technical");
  technical.append(
    el("summary", "Referências e integridade"),
    el(
      "pre",
      JSON.stringify(
        {
          source_ref: source.source_ref,
          source_version_id: version?.id,
          representation_id: state.rid,
          checksum: version?.checksum,
          media_type: version?.media_type,
        },
        null,
        2,
      ),
    ),
  );
  content.append(technical);
  $("reprocess").disabled = !version?.committed;
}
async function readPage(more, sequence) {
  const rid = state.rid;
  const data = await request(
    `/v1/representations/${rid}/read?page_size=20${more ? "&cursor=" + encodeURIComponent(state.readCursor) : ""}`,
  );
  if (sequence !== state.detailGeneration) return;
  state.readCursor = data.next_cursor;
  const blocks = $("blocks");
  if (!more && data.warnings?.length) {
    blocks.append(
      el(
        "p",
        "Extração com avisos: consulte o original e os detalhes de cobertura.",
        "footnote",
      ),
    );
    const coverage = el("details", undefined, "technical");
    coverage.append(
      el("summary", "Cobertura e avisos"),
      el(
        "pre",
        JSON.stringify(
          { coverage: data.coverage, warnings: data.warnings },
          null,
          2,
        ),
      ),
    );
    blocks.append(coverage);
  }
  blocks.querySelector(".read-more")?.remove();
  const assetRefs = new Set(
    blocks.dataset.assets ? JSON.parse(blocks.dataset.assets) : [],
  );
  for (const block of data.blocks) {
    const node = el("div", undefined, "block");
    node.append(
      el(
        "small",
        `${locator(block.locator)} · ${origins[block.origin_kind] || block.origin_kind || "Origem preservada"}`,
        "block-locator",
      ),
    );
    node.append(el(block.type === "heading" ? "h3" : "div", block.text));
    if (block.data?.preview_only)
      node.append(
        el(
          "p",
          "Prévia limitada. Consulte o original para o bloco completo.",
          "footnote",
        ),
      );
    if (block.data?.rows && Array.isArray(block.data.rows)) {
      const table = el("table");
      for (const row of block.data.rows) {
        if (!Array.isArray(row)) continue;
        const tr = el("tr");
        for (const cell of row) tr.append(el("td", cell));
        table.append(tr);
      }
      node.append(table);
    }
    for (const ref of block.asset_refs || [])
      if (!assetRefs.has(ref)) {
        assetRefs.add(ref);
        node.append(
          button(
            "Abrir mídia da passagem",
            () => openAsset(ref, node, sequence).catch(report),
            "text-button",
          ),
        );
      }
    blocks.append(node);
  }
  blocks.dataset.assets = JSON.stringify([...assetRefs]);
  if (state.readCursor)
    blocks.append(
      button(
        "Ler mais conteúdo",
        () => readPage(true, sequence).catch(report),
        "quiet read-more",
      ),
    );
}
async function openAsset(ref, parent, sequence) {
  const id = ref.split("/").at(-1);
  const meta = await request(`/v1/assets/${id}/metadata`);
  if (sequence !== state.detailGeneration) return;
  const link = el("a", "Baixar mídia original");
  link.href = meta.content_path;
  link.setAttribute("download", "");
  const preview = el("div");
  preview.append(
    el("p", `${meta.media_type} · ${size(meta.byte_size)}`, "footnote"),
  );
  if (["image/png", "image/jpeg", "image/webp"].includes(meta.media_type)) {
    const image = el("img", undefined, "media");
    image.src = meta.content_path;
    image.alt = meta.caption || "Mídia da passagem";
    preview.append(image);
  } else if (
    meta.media_type.startsWith("audio/") ||
    meta.media_type.startsWith("video/")
  ) {
    const media = el(
      meta.media_type.startsWith("audio/") ? "audio" : "video",
      undefined,
      "media",
    );
    media.controls = true;
    media.preload = "metadata";
    media.src = meta.content_path;
    preview.append(media);
  }
  preview.append(link);
  parent.append(preview);
}
$("detail-refresh").onclick = () => {
  if (state.selected)
    openSource(state.selected.id, state.selectedVersion?.id).catch(report);
};
$("reprocess").onclick = async () => {
  try {
    await request(`/v1/sources/${state.selected.id}/process`, {
      method: "POST",
      body: { source_version_id: state.selectedVersion.id },
    });
    notice("Reprocessamento solicitado. O histórico permanece disponível.");
    await openSource(state.selected.id, state.selectedVersion.id);
  } catch (error) {
    report(error);
  }
};
$("delete-source").onclick = async () => {
  if (
    !state.selected ||
    !confirm(
      "Excluir esta fonte, seus originais e representações? Essa ação não pode ser desfeita.",
    )
  )
    return;
  try {
    await request("/v1/sources/" + state.selected.id, { method: "DELETE" });
    state.detailGeneration++;
    $("detail").close();
    state.selected = null;
    notice("Fonte excluída e conteúdo expurgado.");
    await refresh();
  } catch (error) {
    report(error);
  }
};
connect().catch(() => {
  $("connect").hidden = false;
  $("workspace").hidden = true;
});
