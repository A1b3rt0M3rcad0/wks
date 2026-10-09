export const availability = {
  text_ready: ["Texto disponível", "green"],
  text_partial: ["Cobertura parcial", "orange"],
  metadata_only: ["Somente metadados", ""],
  unsupported_processing: ["Formato não processado", "violet"],
};
export const processing = {
  queued: "Na fila",
  running: "Processando",
  retry_wait: "Aguardando nova tentativa",
  succeeded: "Concluído",
  partial: "Parcial",
  failed: "Falhou",
  cancelled: "Cancelado",
};
export const kinds = {
  text: "Texto",
  upload: "Arquivo",
  bookmark: "Link de referência",
};
export const origins = {
  native_text: "Texto nativo",
  ocr: "Texto reconhecido por OCR",
  transcript: "Transcrição",
  metadata: "Metadados",
  ai_description: "Descrição por IA",
};
export function el(tag, text, cls) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = String(text);
  if (cls) node.className = cls;
  return node;
}
export function badge(value) {
  const [label, tone] = availability[value] || [value, ""];
  return el("span", label, `badge ${tone}`);
}
export function empty(title, body) {
  const node = el("div", undefined, "empty");
  node.append(el("h3", title), el("p", body));
  return node;
}
export function button(label, fn, cls = "quiet") {
  const node = el("button", label, cls);
  node.type = "button";
  node.addEventListener("click", fn);
  return node;
}
export function locator(value = {}) {
  if (value.kind === "time_range")
    return `${formatTime(value.start_ms)} – ${formatTime(value.end_ms)}`;
  if (value.time_ms !== undefined) return formatTime(value.time_ms);
  const names = {
    page: "Página",
    slide: "Slide",
    section: "Seção",
    line: "Linha",
    row: "Linha",
    image: "Imagem",
    metadata: "Metadados",
  };
  return `${names[value.kind] || value.kind || "Passagem"} ${value.value || ""}`.trim();
}
export function formatTime(ms) {
  return `${Math.floor(ms / 60000)}:${String(Math.floor((ms % 60000) / 1000)).padStart(2, "0")}`;
}
export function size(bytes) {
  return (
    new Intl.NumberFormat("pt-BR", { maximumFractionDigits: 1 }).format(
      bytes / (bytes > 1048576 ? 1048576 : 1024),
    ) + (bytes > 1048576 ? " MB" : " KB")
  );
}
