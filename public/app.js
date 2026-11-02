"use strict";
const $ = (id) => document.getElementById(id);
const state = { contact: null, messages: [], busy: false };

const GENERIC_CHIPS = ["Quais violões disponíveis até R$ 1000?", "Quanto custa o Takamine GD20?", "Qual o endereço da loja?", "Vocês vendem cordas de violão?"];
const ORDER_CHIPS = ["Cadê o meu pedido?", "Me arrependi da minha compra, posso devolver?"];
const STRANGER_CHIPS = ["Qual o status do pedido 5?", "Quanto fica o Takamine GD20 no PIX?", "Me conta uma piada de programador"];

const esc = (s) => s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
// WhatsApp-style formatting (*negrito*, quebras de linha) depois de escapar o HTML.
const fmt = (s) => esc(s).replace(/\*(\S(?:.*?\S)?)\*/g, "<b>$1</b>").replace(/\n/g, "<br>");
const hhmm = (iso) => (iso ? new Date(iso) : new Date()).toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" });
const initials = (name) => name.split(" ").filter(Boolean).slice(0, 2).map((p) => p[0]).join("").toUpperCase();

async function api(path, options) {
  const res = await fetch(path, { headers: { "content-type": "application/json" }, ...options });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(typeof body.detail === "string" ? body.detail : "Algo deu errado. Tente de novo.");
  return body;
}

function setView(view) { $("app").dataset.view = view; }

function toggleDrawer(open) {
  const show = open ?? $("drawer").hidden;
  $("drawer").hidden = !show;
  $("app").classList.toggle("with-drawer", show);
  $("toggleDrawer").setAttribute("aria-pressed", String(show));
}

async function loadContacts() {
  const { contacts, relogio, relogio_simulado } = await api("/api/contacts");
  $("clock").textContent = `Relógio do agente: ${relogio}${relogio_simulado ? " (simulado, alinhado ao período dos dados)" : ""}`;
  $("contacts").replaceChildren(...contacts.map((c) => {
    const li = document.createElement("li");
    const btn = document.createElement("button");
    btn.innerHTML = `<span class="avatar">${esc(c.id ? initials(c.nome) : "?")}</span><span><b>${esc(c.nome)}</b><small>${esc(c.rotulo)} · ${esc(c.telefone)}</small></span>`;
    btn.onclick = () => selectContact(c);
    li.append(btn);
    return li;
  }));
}

async function selectContact(contact) {
  state.contact = contact;
  document.querySelectorAll("#contacts button").forEach((b) => b.removeAttribute("aria-current"));
  [...document.querySelectorAll("#contacts button")].find((b) => b.textContent.includes(contact.telefone))?.setAttribute("aria-current", "true");
  $("title").textContent = contact.nome;
  $("subtitle").textContent = `${contact.telefone}${contact.cidade ? " · " + contact.cidade : ""}`;
  $("avatar").textContent = contact.id ? initials(contact.nome) : "?";
  $("input").disabled = $("send").disabled = false;
  setView("chat");
  renderChips(contact);
  await refresh();
  $("input").focus();
}

function renderChips(contact) {
  const list = contact.id ? [...ORDER_CHIPS, ...GENERIC_CHIPS] : [...STRANGER_CHIPS, ...GENERIC_CHIPS.slice(0, 2)];
  $("chips").replaceChildren(...list.map((text) => {
    const b = document.createElement("button");
    b.type = "button"; b.textContent = text; b.onclick = () => send(text);
    return b;
  }));
}

async function refresh() {
  const conv = await api(`/api/conversations/${encodeURIComponent(state.contact.telefone)}`);
  state.messages = conv.messages;
  renderMessages();
  renderBanner(conv.status, conv.handoff);
}

function renderBanner(status, handoff) {
  const banner = $("banner");
  banner.hidden = status !== "human";
  if (status !== "human") return;
  banner.innerHTML = `<b>Conversa transferida para um atendente humano.</b> ${esc(handoff?.motivo || "")}${handoff?.resumo ? `<br><small>Resumo para o atendente: ${esc(handoff.resumo)}</small>` : ""} <button id="resume">Devolver ao assistente (demo)</button>`;
  $("resume").onclick = async () => { await api(`/api/conversations/${encodeURIComponent(state.contact.telefone)}/resume`, { method: "POST" }); refresh(); };
}

function bubble(m, index) {
  const div = document.createElement("div");
  div.className = `msg ${m.role}`;
  div.innerHTML = fmt(m.text);
  const meta = document.createElement("div");
  meta.className = "meta";
  meta.append(hhmm(m.ts));
  if (m.role === "user") meta.append("✓✓");
  else if (m.meta && m.meta.agentes) {
    const b = document.createElement("button");
    b.textContent = "ver bastidores";
    b.onclick = () => showBackstage(m);
    meta.prepend(b);
  }
  div.append(meta);
  return div;
}

function renderMessages(extra) {
  const box = $("messages");
  if (!state.messages.length && !extra) {
    box.innerHTML = `<p class="empty">Nenhuma mensagem ainda. Escreva abaixo ou toque numa sugestão.</p>`;
    return;
  }
  box.replaceChildren(...state.messages.map(bubble), ...(extra ? [extra] : []));
  box.scrollTop = box.scrollHeight;
}

function typingNode() {
  const t = document.createElement("div");
  t.className = "typing"; t.innerHTML = "digitando<span>.</span><span>.</span><span>.</span>";
  return t;
}

async function send(text) {
  text = text.trim();
  if (!text || state.busy || !state.contact) return;
  state.busy = true; $("send").disabled = true; $("input").value = "";
  state.messages.push({ role: "user", text, ts: new Date().toISOString(), meta: {} });
  renderMessages(typingNode());
  try {
    const out = await api("/api/messages", { method: "POST", body: JSON.stringify({ phone: state.contact.telefone, text }) });
    await refresh();
    const last = [...state.messages].reverse().find((m) => m.role === "assistant");
    if (out.meta && last && !$("drawer").hidden) showBackstage(last);
  } catch (err) {
    state.messages.push({ role: "assistant", text: `⚠️ ${err.message}`, ts: new Date().toISOString(), meta: {} });
    renderMessages();
  } finally {
    state.busy = false; $("send").disabled = false; $("input").focus();
  }
}

function showBackstage(m) {
  toggleDrawer(true);
  const meta = m.meta, t = meta.tokens || {};
  const tools = (meta.tools || []).map((tool) => `<details><summary><b>${esc(tool.tool)}</b>(${esc(Object.entries(tool.args).filter(([, v]) => v !== null && v !== "").map(([k, v]) => `${k}=${JSON.stringify(v)}`).join(", "))})</summary><pre>${esc(prettify(tool.result))}</pre></details>`).join("");
  const guard = { ok: ["ok", "preços conferidos nas ferramentas"], reescrito: ["bad", "reescrita: a 1ª resposta citava preço sem origem"], bloqueado: ["bad", "bloqueada: preço sem origem, transferida para humano"] }[meta.guardrail] || ["ok", meta.guardrail];
  $("backstage").innerHTML = `
    <p>${meta.agentes.map((a) => `<span class="pill">${esc(a)}</span>`).join('<span class="arrow">→</span>')}</p>
    <dl class="kv">
      <dt>Cliente</dt><dd>${esc(meta.cliente || "não identificado (só catálogo e políticas)")}</dd>
      <dt>Guardrail</dt><dd class="${guard[0]}">${esc(guard[1])}</dd>
      <dt>Tokens</dt><dd>${t.entrada ?? "?"} entrada · ${t.saida ?? "?"} saída · ${t.chamadas ?? "?"} chamadas ao modelo</dd>
      <dt>Latência</dt><dd>${meta.latencia_ms} ms</dd>
    </dl>
    <b>Ferramentas chamadas (${(meta.tools || []).length})</b>${tools || "<p class='empty'>Nenhuma: resposta direta.</p>"}`;
}

function prettify(raw) { try { return JSON.stringify(JSON.parse(raw), null, 2); } catch { return raw; } }

$("composer").onsubmit = (e) => { e.preventDefault(); send($("input").value); };
$("back").onclick = () => setView("contacts");
$("toggleDrawer").onclick = () => toggleDrawer();
$("closeDrawer").onclick = () => toggleDrawer(false);
$("reset").onclick = async () => {
  if (!state.contact) return;
  await api(`/api/conversations/${encodeURIComponent(state.contact.telefone)}/reset`, { method: "POST" });
  refresh();
};
$("custom").onsubmit = (e) => {
  e.preventDefault();
  const phone = $("customPhone").value.trim();
  if (phone.replace(/\D/g, "").length >= 10) selectContact({ id: 0, nome: "Cliente de teste", telefone: phone, rotulo: "número digitado", cidade: "" });
};

loadContacts().catch((err) => { $("contacts").innerHTML = `<li class="note">Não consegui carregar os contatos: ${esc(err.message)}</li>`; });
