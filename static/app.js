function $(id) {
  return document.getElementById(id);
}

function esc(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/** When set, «Gerar lição» updates this row instead of inserting. */
let editingLessonId = null;
/** Deep copy of the lesson last shown — merge base for save; used on cancel to re-render. */
let lastLoadedLessonSnapshot = null;
/** Opened via random training spin. */
let trainingModeActive = false;
/** All tracks for the random trainer ({ id, title, artist, label }). */
let libraryTrainingPool = [];

let isLoggedIn = false;
let isAdmin = false;
/** @type {{ id: number, username: string, display_name: string, role: string } | null} */
let currentUser = null;

function apiFetch(url, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (options.body != null && !headers["Content-Type"]) {
    headers["Content-Type"] = "application/json";
  }
  return fetch(url, { ...options, headers, credentials: "same-origin" });
}

async function refreshAuth() {
  try {
    const res = await apiFetch("/api/auth/me");
    const data = await res.json();
    isLoggedIn = Boolean(data.authenticated);
    isAdmin = Boolean(data.is_admin);
    currentUser = data.user && typeof data.user === "object" ? data.user : null;
  } catch (_e) {
    isLoggedIn = false;
    isAdmin = false;
    currentUser = null;
  }
  syncAuthUi();
  if (isLoggedIn) {
    loadLibrary();
  }
}

function setLoginError(msg) {
  const el = $("login-error");
  if (!el) return;
  if (msg) {
    el.textContent = msg;
    el.classList.remove("hidden");
  } else {
    el.textContent = "";
    el.classList.add("hidden");
  }
}

function setModalError(id, msg) {
  const el = $(id);
  if (!el) return;
  if (msg) {
    el.textContent = msg;
    el.classList.remove("hidden");
  } else {
    el.textContent = "";
    el.classList.add("hidden");
  }
}

function openModal(id) {
  const modal = $(id);
  if (!modal) return;
  modal.classList.remove("hidden");
  modal.hidden = false;
  document.body.classList.add("modal-open");
}

function closeModal(id) {
  const modal = $(id);
  if (!modal) return;
  modal.classList.add("hidden");
  modal.hidden = true;
  if (
    (!$("settings-modal") || $("settings-modal").classList.contains("hidden")) &&
    (!$("users-modal") || $("users-modal").classList.contains("hidden"))
  ) {
    document.body.classList.remove("modal-open");
  }
}

let adminToastTimer = null;

function showAdminToast(message) {
  const toast = $("admin-toast");
  if (!toast) return;
  toast.textContent = message;
  toast.classList.remove("hidden");
  if (adminToastTimer) clearTimeout(adminToastTimer);
  adminToastTimer = setTimeout(() => {
    adminToastTimer = null;
    toast.classList.add("hidden");
  }, 4500);
}

function syncAuthUi() {
  document.body.classList.remove("auth-pending");
  document.body.classList.toggle("is-admin", isAdmin);
  document.body.classList.toggle("is-logged-in", isLoggedIn);

  const gate = $("login-gate");
  const shell = $("app-shell");
  const header = $("site-header");
  if (gate) {
    gate.hidden = isLoggedIn;
    gate.classList.toggle("hidden", isLoggedIn);
  }
  if (shell) {
    shell.hidden = !isLoggedIn;
    shell.classList.toggle("hidden", !isLoggedIn);
  }
  if (header) {
    header.hidden = !isLoggedIn;
    header.classList.toggle("hidden", !isLoggedIn);
  }

  const userWrap = $("admin-auth-user");
  const bar = $("admin-active-bar");
  const pill = $("user-display-pill");
  const usersBtn = $("btn-open-users");
  if (userWrap) userWrap.classList.toggle("hidden", !isLoggedIn);
  if (bar) bar.classList.toggle("hidden", !isAdmin);
  if (pill) {
    if (currentUser) {
      pill.textContent = currentUser.display_name || currentUser.username;
      pill.title = "@" + currentUser.username;
    } else {
      pill.textContent = "Conta";
      pill.title = "";
    }
  }
  if (usersBtn) usersBtn.classList.toggle("hidden", !isAdmin);

  const ro = !isLoggedIn;
  for (const id of ["lyrics", "title", "artist"]) {
    const el = $(id);
    if (el) el.readOnly = ro;
  }

  const gen = $("btn-generate");
  const newLesson = $("btn-new-lesson");
  if (gen) gen.disabled = !isLoggedIn;
  if (newLesson) newLesson.disabled = !isLoggedIn;

  const backupPanel = $("library-backup-panel");
  if (backupPanel) backupPanel.classList.toggle("hidden", !isAdmin);

  if (!isLoggedIn && editingLessonId != null) {
    clearEditingMode();
    lastLoadedLessonSnapshot = null;
    $("result")?.classList.add("hidden");
  }
  syncEditToolbar();
}

async function doLogin(ev) {
  if (ev) ev.preventDefault();
  const username = ($("login-username")?.value || "").trim();
  const password = $("login-password")?.value || "";
  if (!username || !password) {
    setLoginError("Preencha utilizador e senha.");
    return;
  }
  const btn = $("btn-login");
  if (btn) btn.disabled = true;
  setLoginError("");
  try {
    const res = await apiFetch("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    });
    const data = await res.json();
    if (!res.ok || !data.ok) {
      setLoginError(data.error || "Falha ao entrar.");
      return;
    }
    isLoggedIn = true;
    isAdmin = Boolean(data.is_admin);
    currentUser = data.user || null;
    if ($("login-password")) $("login-password").value = "";
    syncAuthUi();
    showAdminToast("Bem-vindo" + (currentUser?.display_name ? `, ${currentUser.display_name}` : "") + ".");
    loadLibrary();
  } catch (e) {
    setLoginError(String(e));
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function doLogout() {
  try {
    await apiFetch("/api/auth/logout", { method: "POST" });
  } catch (_e) {
    /* ignore */
  }
  isLoggedIn = false;
  isAdmin = false;
  currentUser = null;
  clearEditingMode();
  clearTrainingMode();
  libraryTrainingPool = [];
  lastLoadedLessonSnapshot = null;
  $("result")?.classList.add("hidden");
  syncAuthUi();
  const toast = $("admin-toast");
  if (toast) toast.classList.add("hidden");
  if (adminToastTimer) clearTimeout(adminToastTimer);
  setTimeout(() => $("login-username")?.focus(), 50);
}

function openSettingsModal() {
  if (!isLoggedIn || !currentUser) return;
  setModalError("settings-modal-error", "");
  if ($("settings-display-name")) $("settings-display-name").value = currentUser.display_name || "";
  if ($("settings-password")) $("settings-password").value = "";
  if ($("settings-current-password")) $("settings-current-password").value = "";
  openModal("settings-modal");
  setTimeout(() => $("settings-display-name")?.focus(), 50);
}

function closeSettingsModal() {
  closeModal("settings-modal");
  setModalError("settings-modal-error", "");
}

async function saveSettings(ev) {
  if (ev) ev.preventDefault();
  const display_name = ($("settings-display-name")?.value || "").trim();
  const password = $("settings-password")?.value || "";
  const current_password = $("settings-current-password")?.value || "";
  if (!current_password) {
    setModalError("settings-modal-error", "Indique a senha actual.");
    return;
  }
  try {
    const res = await apiFetch("/api/auth/me", {
      method: "PATCH",
      body: JSON.stringify({
        display_name,
        password: password || null,
        current_password,
      }),
    });
    const data = await res.json();
    if (!res.ok || !data.ok) {
      setModalError("settings-modal-error", data.error || "Falha ao guardar.");
      return;
    }
    currentUser = data.user;
    isAdmin = Boolean(data.is_admin);
    syncAuthUi();
    closeSettingsModal();
    showAdminToast("Conta actualizada.");
  } catch (e) {
    setModalError("settings-modal-error", String(e));
  }
}

async function openUsersModal() {
  if (!isAdmin) return;
  setModalError("users-modal-error", "");
  openModal("users-modal");
  await loadUsersList();
}

function closeUsersModal() {
  closeModal("users-modal");
  setModalError("users-modal-error", "");
}

async function loadUsersList() {
  const tbody = $("users-tbody");
  if (!tbody) return;
  tbody.innerHTML = "<tr><td colspan=\"4\" class=\"muted\">A carregar…</td></tr>";
  try {
    const res = await apiFetch("/api/users");
    const data = await res.json();
    if (!res.ok || !data.ok) {
      tbody.innerHTML =
        "<tr><td colspan=\"4\" class=\"muted\">" + esc(data.error || "Falha.") + "</td></tr>";
      return;
    }
    const users = data.users || [];
    if (!users.length) {
      tbody.innerHTML = "<tr><td colspan=\"4\" class=\"muted\">Sem utilizadores.</td></tr>";
      return;
    }
    tbody.innerHTML = "";
    for (const u of users) {
      const tr = document.createElement("tr");
      tr.className = "users-row";
      const isSelf = currentUser && Number(u.id) === Number(currentUser.id);
      tr.innerHTML = `
        <td><code>@${esc(u.username)}</code></td>
        <td>${esc(u.display_name || "")}</td>
        <td>${u.role === "admin" ? "Admin" : "Utilizador"}</td>
        <td class="cell-actions library-cell-actions">
          <button type="button" class="btn btn-sm btn-secondary btn-user-reset" data-id="${esc(String(u.id))}">Senha</button>
          <button type="button" class="btn btn-sm btn-ghost-danger btn-user-del" data-id="${esc(String(u.id))}" ${isSelf ? "disabled" : ""}>Apagar</button>
        </td>`;
      tr.querySelector(".btn-user-reset")?.addEventListener("click", () => resetUserPassword(u));
      tr.querySelector(".btn-user-del")?.addEventListener("click", () => removeUser(u));
      tbody.appendChild(tr);
    }
  } catch (e) {
    tbody.innerHTML = "<tr><td colspan=\"4\" class=\"muted\">" + esc(String(e)) + "</td></tr>";
  }
}

async function createUserFromForm(ev) {
  if (ev) ev.preventDefault();
  setModalError("users-modal-error", "");
  const username = ($("new-user-username")?.value || "").trim();
  const display_name = ($("new-user-display")?.value || "").trim();
  const password = $("new-user-password")?.value || "";
  const role = $("new-user-role")?.value || "user";
  try {
    const res = await apiFetch("/api/users", {
      method: "POST",
      body: JSON.stringify({ username, display_name, password, role }),
    });
    const data = await res.json();
    if (!res.ok || !data.ok) {
      setModalError("users-modal-error", data.error || "Falha ao criar.");
      return;
    }
    if ($("new-user-username")) $("new-user-username").value = "";
    if ($("new-user-display")) $("new-user-display").value = "";
    if ($("new-user-password")) $("new-user-password").value = "";
    if ($("new-user-role")) $("new-user-role").value = "user";
    showAdminToast("Utilizador criado: @" + (data.user?.username || username));
    await loadUsersList();
  } catch (e) {
    setModalError("users-modal-error", String(e));
  }
}

async function resetUserPassword(u) {
  const pwd = window.prompt(`Nova senha para @${u.username}:`);
  if (pwd == null) return;
  if (!pwd.trim()) {
    setModalError("users-modal-error", "Senha vazia.");
    return;
  }
  try {
    const res = await apiFetch("/api/users/" + encodeURIComponent(String(u.id)), {
      method: "PATCH",
      body: JSON.stringify({ password: pwd }),
    });
    const data = await res.json();
    if (!res.ok || !data.ok) {
      setModalError("users-modal-error", data.error || "Falha ao alterar senha.");
      return;
    }
    showAdminToast("Senha actualizada para @" + u.username);
  } catch (e) {
    setModalError("users-modal-error", String(e));
  }
}

async function removeUser(u) {
  if (!confirm(`Apagar @${u.username} e todas as lições desta conta?`)) return;
  try {
    const res = await apiFetch("/api/users/" + encodeURIComponent(String(u.id)), {
      method: "DELETE",
    });
    const data = await res.json();
    if (!res.ok || !data.ok) {
      setModalError("users-modal-error", data.error || "Falha ao apagar.");
      return;
    }
    showAdminToast("Utilizador apagado.");
    await loadUsersList();
  } catch (e) {
    setModalError("users-modal-error", String(e));
  }
}

function setEditBanner(on, id) {
  const wrap = $("edit-mode-banner");
  const t = $("edit-mode-banner-text");
  if (!wrap || !t) return;
  if (!on || id == null) {
    wrap.classList.add("hidden");
    t.textContent = "";
    return;
  }
  wrap.classList.remove("hidden");
  t.textContent = `A editar a lição nº ${id}. Edite as abas abaixo; «Salvar alterações» grava tudo na base local. «Gerar lição» chama o modelo de novo e substitui a lição completa (o ID mantém-se).`;
}

function clearEditingMode() {
  editingLessonId = null;
  setEditBanner(false);
  syncEditToolbar();
}

/** Limpa o formulário «Nova lição» para colar a próxima música. */
function clearNewLessonForm({ hideResult = true, focusLyrics = true } = {}) {
  const lyricsEl = $("lyrics");
  const titleEl = $("title");
  const artistEl = $("artist");
  if (lyricsEl) lyricsEl.value = "";
  if (titleEl) titleEl.value = "";
  if (artistEl) artistEl.value = "";
  const fetchSt = $("lyrics-fetch-status");
  if (fetchSt) fetchSt.textContent = "";
  clearEditingMode();
  clearTrainingMode();
  $("error-panel")?.classList.add("hidden");
  if (hideResult) {
    $("result")?.classList.add("hidden");
    setResultMeta("");
    lastLoadedLessonSnapshot = null;
  }
  if (focusLyrics && lyricsEl && isLoggedIn) {
    setTimeout(() => lyricsEl.focus(), 50);
  }
}

let lyricsInputMode = "paste";

function setLyricsInputMode(mode) {
  lyricsInputMode = mode === "search" ? "search" : "paste";
  document.body.classList.toggle("lyrics-mode-search", lyricsInputMode === "search");
  document.querySelectorAll(".lyrics-mode-btn").forEach((btn) => {
    const on = btn.dataset.lyricsMode === lyricsInputMode;
    btn.classList.toggle("active", on);
  });
  const panel = $("lyrics-search-panel");
  if (panel) panel.classList.toggle("hidden", lyricsInputMode !== "search");
  const hint = $("lyrics-field-hint");
  if (hint) {
    hint.textContent =
      lyricsInputMode === "search"
        ? "Preencha título e artista, depois busque"
        : "Cole ou digite a letra abaixo";
  }
  const lyricsEl = $("lyrics");
  if (lyricsEl) {
    lyricsEl.placeholder =
      lyricsInputMode === "search"
        ? "A letra aparece aqui após a busca — revise antes de gerar…"
        : "Paste the English lyrics here…";
  }
  const fetchSt = $("lyrics-fetch-status");
  if (fetchSt && lyricsInputMode === "paste") fetchSt.textContent = "";
}

async function fetchLyricsFromWeb() {
  if (!isLoggedIn) {
    $("lyrics-fetch-status").textContent = "Entre na sua conta para buscar.";
    return;
  }
  const title = ($("title")?.value || "").trim();
  const artist = ($("artist")?.value || "").trim();
  const st = $("lyrics-fetch-status");
  const btn = $("btn-fetch-lyrics");
  if (!title || !artist) {
    if (st) st.textContent = "Preencha título e artista.";
    return;
  }
  if (btn) btn.disabled = true;
  if (st) st.textContent = "A buscar letra…";
  try {
    const res = await apiFetch("/api/lyrics/fetch", {
      method: "POST",
      body: JSON.stringify({ title, artist }),
    });
    const data = await res.json().catch(() => ({}));
    if (res.status === 401) {
      if (st) st.textContent = "Sessão expirada — entre novamente.";
      return;
    }
    if (!res.ok || !data.ok) {
      if (st) st.textContent = data.error || "Não encontrada.";
      return;
    }
    if ($("lyrics")) $("lyrics").value = data.lyrics || "";
    if (data.title && $("title")) $("title").value = data.title;
    if (data.artist && $("artist")) $("artist").value = data.artist;
    if (st) st.textContent = "Letra carregada. Revise e gere a lição.";
    $("lyrics")?.scrollIntoView({ behavior: "smooth", block: "center" });
  } catch (e) {
    if (st) st.textContent = String(e);
  } finally {
    if (btn) btn.disabled = false;
  }
}

function startNextLessonAfterGenerate(saved, { title = "", artist = "", elapsedMs = null } = {}) {
  const titleHint = String(title).trim() || "Sem título";
  const artistHint = String(artist).trim();
  clearNewLessonForm({ hideResult: false, focusLyrics: true });
  const status = $("status");
  const timeBit =
    elapsedMs != null && elapsedMs > 0 ? ` · gerada em ${formatDurationShort(elapsedMs)}` : "";
  if (saved && saved.id != null) {
    const label = artistHint ? `${artistHint} — ${titleHint}` : titleHint;
    if (status) {
      status.textContent = `«${label}» guardada (#${saved.id})${timeBit}. Cole a próxima letra acima.`;
    }
  } else if (status) {
    status.textContent = `Pronto${timeBit}. Cole a próxima letra acima.`;
  }
  $("create-form-surface")?.scrollIntoView({ behavior: "smooth", block: "start" });
}

function formatTrainLabel(entry) {
  if (!entry) return "—";
  const title = entry.title && String(entry.title).trim() ? entry.title : "Sem título";
  const artist = entry.artist && String(entry.artist).trim() ? entry.artist : "Artista desconhecido";
  return `${artist} — ${title}`;
}

function setTrainingBanner(on, label) {
  const wrap = $("training-mode-banner");
  const text = $("training-mode-banner-text");
  if (!wrap || !text) return;
  if (!on) {
    wrap.classList.add("hidden");
    text.textContent = "";
    return;
  }
  wrap.classList.remove("hidden");
  text.textContent = label
    ? `Modo treino · ${label}. Comece pela tradução ou use as outras abas para praticar.`
    : "Modo treino — pratique inglês com esta faixa.";
}

function clearTrainingMode() {
  trainingModeActive = false;
  setTrainingBanner(false);
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function lessonToTrainEntry(r) {
  const title = r.title_hint && String(r.title_hint).trim() ? r.title_hint : "Sem título";
  const artist = r.artist_hint && String(r.artist_hint).trim() ? r.artist_hint : "";
  return {
    id: r.id,
    title,
    artist,
    label: formatTrainLabel({ title, artist: artist || "Artista desconhecido" }),
  };
}

async function refreshTrainingPool() {
  try {
    const res = await apiFetch("/api/lessons?flat=1&limit=2000");
    const data = await res.json();
    if (!res.ok || !data.ok) return [];
    const lessons = data.lessons || [];
    libraryTrainingPool = lessons.map(lessonToTrainEntry);
    return libraryTrainingPool;
  } catch (_e) {
    return libraryTrainingPool;
  }
}

function syncTrainPoolHint() {
  const hint = $("train-pool-hint");
  const btn = $("btn-random-train");
  if (!hint) return;
  const n = libraryTrainingPool.length;
  if (n === 0) {
    hint.textContent = "Adicione lições à biblioteca para treinar.";
    if (btn) btn.disabled = true;
  } else if (n === 1) {
    hint.textContent = "1 faixa disponível.";
    if (btn) btn.disabled = false;
  } else {
    hint.textContent = `${n} faixas na roleta.`;
    if (btn) btn.disabled = false;
  }
}

async function spinRandomTraining() {
  const btn = $("btn-random-train");
  const roulette = $("train-roulette");
  const labelEl = $("train-roulette-label");
  const st = $("library-status");
  if (btn) btn.disabled = true;

  let pool = libraryTrainingPool;
  if (!pool.length) {
    pool = await refreshTrainingPool();
    syncTrainPoolHint();
  }
  if (!pool.length) {
    if (st) st.textContent = "Nenhuma faixa na biblioteca para treinar.";
    if (btn) btn.disabled = true;
    return;
  }

  if (pool.length === 1) {
    await openLesson(pool[0].id, { training: true, trainLabel: pool[0].label });
    if (btn) btn.disabled = false;
    return;
  }

  if (roulette) {
    roulette.classList.remove("hidden", "is-final");
    roulette.hidden = false;
  }
  if (labelEl) labelEl.textContent = "…";

  const finalIdx = Math.floor(Math.random() * pool.length);
  const steps = Math.min(32, Math.max(14, pool.length * 3));

  for (let i = 0; i < steps; i++) {
    const idx = i === steps - 1 ? finalIdx : Math.floor(Math.random() * pool.length);
    if (labelEl) {
      labelEl.textContent = pool[idx].label;
      labelEl.style.animation = "none";
      void labelEl.offsetWidth;
      labelEl.style.animation = "";
    }
    await sleep(55 + Math.floor((i * i) / 2.2));
  }

  if (roulette) roulette.classList.add("is-final");
  const picked = pool[finalIdx];
  if (st) st.textContent = `Treino: ${picked.label}`;
  await sleep(420);
  await openLesson(picked.id, { training: true, trainLabel: picked.label });
  if (roulette) {
    roulette.classList.add("hidden");
    roulette.classList.remove("is-final");
    roulette.hidden = true;
  }
  if (btn) btn.disabled = false;
}

function syncEditToolbar() {
  const b = $("btn-save-lesson");
  if (b) b.classList.toggle("hidden", editingLessonId == null || !isLoggedIn);
}

const GEN_DUR_KEY = "trusicas-gen-durations";
const GEN_DUR_MAX = 20;
const GEN_FALLBACK_AVG_MS = 55_000;

let generateStartedAt = 0;
let generateTickTimer = null;
let generateAvgMs = null;

function getGenDurations() {
  try {
    const raw = JSON.parse(localStorage.getItem(GEN_DUR_KEY) || "[]");
    if (!Array.isArray(raw)) return [];
    return raw.filter((n) => typeof n === "number" && n >= 5_000 && n <= 600_000);
  } catch {
    return [];
  }
}

function recordGenDuration(ms) {
  if (!(ms >= 5_000 && ms <= 600_000)) return;
  const arr = getGenDurations();
  arr.push(Math.round(ms));
  while (arr.length > GEN_DUR_MAX) arr.shift();
  try {
    localStorage.setItem(GEN_DUR_KEY, JSON.stringify(arr));
  } catch {
    /* ignore quota */
  }
}

function averageGenDurationMs() {
  const arr = getGenDurations();
  if (!arr.length) return null;
  return arr.reduce((a, b) => a + b, 0) / arr.length;
}

function formatDurationShort(ms) {
  const s = Math.max(0, Math.round(ms / 1000));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const r = s % 60;
  return r ? `${m} min ${r}s` : `${m} min`;
}

function stopGenerateTicker() {
  if (generateTickTimer) {
    clearInterval(generateTickTimer);
    generateTickTimer = null;
  }
}

function updateGenerateLoadingUi() {
  const elapsed = Date.now() - generateStartedAt;
  const elapsedEl = $("generate-loading-elapsed");
  const bar = $("generate-loading-bar");
  const panel = $("generate-loading");
  if (elapsedEl) elapsedEl.textContent = `Decorrido: ${formatDurationShort(elapsed)}`;

  const avg = generateAvgMs || GEN_FALLBACK_AVG_MS;
  const hasHistory = generateAvgMs != null;
  if (panel) panel.classList.toggle("is-estimating", !hasHistory);

  if (bar && hasHistory) {
    const pct = Math.min(92, Math.max(6, (elapsed / avg) * 100));
    bar.style.width = `${pct}%`;
  }
}

function setGenerateLoading(on, { updating = false } = {}) {
  const panel = $("generate-loading");
  const sheet = $("create-form-surface");
  const save = $("btn-save-lesson");
  const neu = $("btn-new-lesson");
  const title = $("generate-loading-title");
  const avgEl = $("generate-loading-avg");
  const bar = $("generate-loading-bar");

  if (sheet) {
    sheet.classList.toggle("is-generating", on);
    sheet.setAttribute("aria-busy", on ? "true" : "false");
  }
  if (save && !save.classList.contains("hidden")) save.disabled = on;
  if (neu) neu.disabled = on;

  if (!on) {
    stopGenerateTicker();
    if (panel) {
      panel.classList.add("hidden");
      panel.classList.remove("is-estimating");
      panel.setAttribute("aria-hidden", "true");
    }
    if (bar) bar.style.width = "8%";
    return;
  }

  generateStartedAt = Date.now();
  generateAvgMs = averageGenDurationMs();
  if (title) {
    title.textContent = updating ? "Atualizando a lição…" : "Gerando a lição…";
  }
  if (avgEl) {
    if (generateAvgMs != null) {
      const n = getGenDurations().length;
      avgEl.textContent = `Tempo médio nas últimas ${n} geraç${n === 1 ? "ão" : "ões"}: ~${formatDurationShort(generateAvgMs)}`;
    } else {
      avgEl.textContent = "Primeira vez neste dispositivo — costuma levar alguns minutos.";
    }
  }
  if (panel) {
    panel.classList.remove("hidden");
    panel.setAttribute("aria-hidden", "false");
    panel.classList.toggle("is-estimating", generateAvgMs == null);
  }
  if (bar) bar.style.width = generateAvgMs != null ? "6%" : "";
  updateGenerateLoadingUi();
  stopGenerateTicker();
  generateTickTimer = setInterval(updateGenerateLoadingUi, 400);
  panel?.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function setView(name) {
  const create = $("view-create");
  const lib = $("view-library");
  document.querySelectorAll(".segmented-btn[data-view]").forEach((b) => {
    b.classList.toggle("active", b.dataset.view === name);
  });
  if (name === "library") {
    create.classList.add("hidden");
    lib.classList.remove("hidden");
    loadLibrary();
    if (!libraryTrainingPool.length) refreshTrainingPool().then(() => syncTrainPoolHint());
  } else {
    lib.classList.add("hidden");
    create.classList.remove("hidden");
  }
}

document.querySelectorAll(".segmented-btn[data-view]").forEach((b) => {
  b.addEventListener("click", () => setView(b.dataset.view));
});

function normalizeLyricsNewlines(text) {
  return String(text || "").replace(/\r\n/g, "\n").replace(/\r/g, "\n");
}

function parseLyricStanzas(lyricsText) {
  const normalized = normalizeLyricsNewlines(lyricsText).trim();
  if (!normalized) return [];
  return normalized
    .split(/\n(?:[ \t]*\n)+/)
    .map((block) =>
      block
        .split("\n")
        .map((l) => l.trim())
        .filter((l) => l.length > 0)
    )
    .filter((stanza) => stanza.length > 0);
}

function stanzaBreakIndices(lyricsEn) {
  const stanzas = parseLyricStanzas(lyricsEn);
  if (stanzas.length <= 1) return new Set();
  const breaks = new Set();
  let idx = 0;
  for (let s = 0; s < stanzas.length - 1; s++) {
    idx += stanzas[s].length;
    breaks.add(idx - 1);
  }
  return breaks;
}

function wholePtPreservesStanzas(wholePt, stanzaCount) {
  if (stanzaCount <= 1) return true;
  const normalized = normalizeLyricsNewlines(wholePt).trim();
  if (!normalized) return false;
  const blocks = normalized.split(/\n(?:[ \t]*\n)+/).filter((b) => b.trim());
  return blocks.length >= stanzaCount;
}

function buildWholePtFromLines(lyricsEn, lineByLine) {
  const stanzas = parseLyricStanzas(lyricsEn);
  const rows = Array.isArray(lineByLine) ? lineByLine : [];
  if (!stanzas.length) {
    return rows
      .map((row) => (row && typeof row === "object" ? String(row.pt || "").trim() : ""))
      .filter(Boolean)
      .join("\n");
  }
  let i = 0;
  const parts = [];
  for (const stanza of stanzas) {
    const ptLines = [];
    for (let j = 0; j < stanza.length; j++) {
      if (i >= rows.length) break;
      const row = rows[i++];
      if (row && typeof row === "object") ptLines.push(String(row.pt ?? ""));
    }
    if (ptLines.length) parts.push(ptLines.join("\n"));
  }
  while (i < rows.length) {
    const row = rows[i++];
    const pt = row && typeof row === "object" ? String(row.pt || "").trim() : "";
    if (!pt) continue;
    if (parts.length) parts[parts.length - 1] += "\n" + pt;
    else parts.push(pt);
  }
  return parts.join("\n\n");
}

function wholePtForLesson(lesson) {
  const t = lesson?.translation || {};
  const lyricsEn = lyricsEnForLesson(lesson);
  const lines = t.line_by_line || [];
  const built = buildWholePtFromLines(lyricsEn, lines).trim();
  if (built) return built;
  return normalizeLyricsNewlines(t.whole_song_pt).trim();
}

function lyricsEnForLesson(lesson) {
  const raw = $("lyrics")?.value;
  if (raw != null && String(raw).trim()) {
    return normalizeLyricsNewlines(raw).trimEnd();
  }
  const lines = Array.isArray(lesson?.translation?.line_by_line) ? lesson.translation.line_by_line : [];
  return lines
    .map((row) => (row && typeof row === "object" ? String(row.en || "").trim() : ""))
    .filter(Boolean)
    .join("\n");
}

function renderTranslationLineRows(lesson, lines, { editable = false } = {}) {
  const lyricsEn = lyricsEnForLesson(lesson);
  const breaks = stanzaBreakIndices(lyricsEn);
  let html = "";
  lines.forEach((row, idx) => {
    if (!row || typeof row !== "object") return;
    if (editable) {
      html += `<tr class="lesson-edit-row">
        <td><textarea class="input textarea line-en" rows="2" spellcheck="false">${esc(row.en || "")}</textarea></td>
        <td><textarea class="input textarea line-pt" rows="2" spellcheck="false">${esc(row.pt || "")}</textarea></td>
      </tr>`;
    } else {
      html += `<tr><td>${esc(row.en || "")}</td><td>${esc(row.pt || "")}</td></tr>`;
    }
    if (breaks.has(idx)) {
      html += '<tr class="stanza-gap-row" aria-hidden="true"><td colspan="2"></td></tr>';
    }
  });
  return html;
}

function renderTranslationFullDuo(lesson, { editable = false } = {}) {
  const t = lesson.translation || {};
  const lyricsEn = lyricsEnForLesson(lesson);
  const wholePt = wholePtForLesson(lesson);
  let html = '<div class="translation-full-duo">';
  html += '<div class="translation-full-col">';
  html += '<p class="translation-full-label">Letra (EN)</p>';
  if (editable) {
    html +=
      '<textarea id="translation-whole-en" class="input textarea translation-full-text translation-full-readonly" rows="12" spellcheck="false" readonly>' +
      esc(lyricsEn) +
      "</textarea>";
  } else {
    html += `<div class="translation-full-text">${esc(lyricsEn) || '<span class="muted">—</span>'}</div>`;
  }
  html += "</div>";
  html += '<div class="translation-full-col">';
  html += '<p class="translation-full-label">Tradução (PT)</p>';
  if (editable) {
    html +=
      '<textarea id="translation-whole-pt" class="input textarea translation-full-text translation-full-derived" rows="12" spellcheck="true" readonly title="Gerada automaticamente a partir das linhas abaixo">' +
      esc(wholePt) +
      "</textarea>";
    html += '<p class="translation-derived-hint muted">Tradução completa gerada das linhas PT (não é resumo).</p>';
  } else {
    html += `<div class="translation-full-text">${esc(wholePt) || '<span class="muted">—</span>'}</div>`;
  }
  html += "</div></div>";
  return html;
}

function renderTranslation(lesson) {
  const t = lesson.translation || {};
  const lines = Array.isArray(t.line_by_line) ? t.line_by_line : [];
  let html = '<h2 class="content-heading">Tradução</h2>';
  html += renderTranslationFullDuo(lesson);
  html += '<h3 class="section-heading translation-lines-heading">Linha a linha</h3>';
  if (lines.length) {
    html +=
      '<div class="table-wrap prose-table-wrap"><table class="prose-table"><thead><tr><th>EN</th><th>PT</th></tr></thead><tbody>';
    html += renderTranslationLineRows(lesson, lines);
    html += "</tbody></table></div>";
  } else {
    html += '<p class="muted">Nenhuma linha em translation.line_by_line.</p>';
  }
  return html;
}

function renderStructures(lesson) {
  const s = lesson.structures || {};
  const sections = Array.isArray(s.sections) ? s.sections : [];
  let html = '<h2 class="content-heading">Estruturas e gramática</h2>';
  if (!sections.length) {
    return html + '<p class="muted">Nenhuma seção em structures.sections.</p>';
  }
  for (const sec of sections) {
    if (!sec || typeof sec !== "object") continue;
    const h = esc(sec.heading || "");
    const body = esc(sec.body_pt || "").replace(/\n/g, "<br/>");
    html += `<div class="section-block"><h3 class="section-heading">${h || "(sem título)"}</h3><p>${body}</p>`;
    const exs = Array.isArray(sec.examples_en) ? sec.examples_en : [];
    if (exs.length) {
      html += "<p><strong>Exemplos (EN)</strong></p><ul class='clean'>";
      for (const e of exs) html += "<li>" + esc(e) + "</li>";
      html += "</ul>";
    }
    html += "</div>";
  }
  return html;
}

function renderVocabulary(lesson) {
  const items = Array.isArray(lesson.vocabulary) ? lesson.vocabulary : [];
  let html = '<h2 class="content-heading">Vocabulário</h2>';
  if (!items.length) {
    return html + '<p class="muted">Lista vazia.</p>';
  }
  html +=
    '<div class="table-wrap prose-table-wrap"><table class="prose-table"><thead><tr><th>Termo</th><th>Significado (PT)</th><th>Notas</th><th>Colocações (EN)</th></tr></thead><tbody>';
  for (const it of items) {
    if (!it || typeof it !== "object") continue;
    const cols = Array.isArray(it.common_collocations_en) ? it.common_collocations_en.join(", ") : "";
    html += `<tr><td>${esc(it.term || "")}</td><td>${esc(it.meaning_pt || "")}</td><td>${esc(
      it.notes_pt || ""
    )}</td><td>${esc(cols)}</td></tr>`;
  }
  html += "</tbody></table></div>";
  return html;
}

function renderExamples(lesson) {
  const d = lesson.examples_and_drills || {};
  let html = '<h2 class="content-heading">Exemplos e fixação</h2>';
  const patterns = Array.isArray(d.pattern_drills) ? d.pattern_drills : [];
  if (patterns.length) {
    for (const p of patterns) {
      if (!p || typeof p !== "object") continue;
      html +=
        '<div class="section-block"><h3 class="section-heading">' + esc(p.pattern_name_pt || "Padrão") + "</h3>";
      if (p.pattern_explanation_pt) html += "<p>" + esc(p.pattern_explanation_pt) + "</p>";
      const ex = Array.isArray(p.examples_en) ? p.examples_en : [];
      if (ex.length) {
        html += "<ul class='clean'>";
        for (const e of ex) html += "<li>" + esc(e) + "</li>";
        html += "</ul>";
      }
      const fp = Array.isArray(p.fixation_prompts_pt) ? p.fixation_prompts_pt : [];
      if (fp.length) {
        html += "<p><strong>Fixação (PT)</strong></p><ul class='clean'>";
        for (const x of fp) html += "<li><em>" + esc(x) + "</em></li>";
        html += "</ul>";
      }
      html += "</div>";
    }
  }
  const mistakes = Array.isArray(d.mistakes_pt_speakers) ? d.mistakes_pt_speakers : [];
  if (mistakes.length) {
    html +=
      '<div class="section-block"><h3 class="section-heading">Erros comuns (falantes de PT)</h3><ul class="clean">';
    for (const m of mistakes) {
      if (!m || typeof m !== "object") continue;
      html +=
        "<li><strong>Evite:</strong> " +
        esc(m.wrong || "") +
        " → <strong>Melhor:</strong> " +
        esc(m.better || "") +
        " — " +
        esc(m.why_pt || "") +
        "</li>";
    }
    html += "</ul></div>";
  }
  if (!patterns.length && !mistakes.length) {
    html += '<p class="muted">Nenhum conteúdo em examples_and_drills.</p>';
  }
  return html;
}

function renderCuriosities(lesson) {
  const list = Array.isArray(lesson.curiosities) ? lesson.curiosities : [];
  let html = '<h2 class="content-heading">Curiosidades</h2>';
  if (!list.length) {
    return html + '<p class="muted">Lista vazia.</p>';
  }
  for (const c of list) {
    if (!c || typeof c !== "object") continue;
    const flag = c.needs_verification ? " <span class='muted'>(verificar fonte)</span>" : "";
    html +=
      '<div class="section-block"><h3 class="section-heading">' + esc(c.title || "Curiosidade") + flag + "</h3>";
    html += "<p>" + esc(c.body_pt || "") + "</p></div>";
  }
  return html;
}

function splitLinesNonEmpty(s) {
  return String(s || "")
    .split("\n")
    .map((t) => t.trim())
    .filter(Boolean);
}

function renderTranslationForm(lesson) {
  const lines = Array.isArray(lesson.translation?.line_by_line) ? lesson.translation.line_by_line : [];
  let html = '<h2 class="content-heading">Tradução</h2><div class="lesson-edit-fields">';
  html += renderTranslationFullDuo(lesson, { editable: true });
  html += '<h3 class="section-heading translation-lines-heading">Linha a linha</h3>';
  if (lines.length) {
    html +=
      '<div class="table-wrap prose-table-wrap"><table class="prose-table lesson-edit-table"><thead><tr><th>EN</th><th>PT</th></tr></thead><tbody>';
    html += renderTranslationLineRows(lesson, lines, { editable: true });
    html += "</tbody></table></div>";
  } else {
    html += '<p class="muted">Nenhuma linha em translation.line_by_line.</p>';
  }
  html += "</div>";
  return html;
}

function collectTranslation() {
  const rows = [];
  document.querySelectorAll("#panel-translation tr.lesson-edit-row").forEach((tr) => {
    rows.push({
      en: tr.querySelector(".line-en")?.value ?? "",
      pt: tr.querySelector(".line-pt")?.value ?? "",
    });
  });
  const lyricsEn = lyricsEnForLesson(lastLoadedLessonSnapshot || { translation: { line_by_line: rows } });
  const whole = buildWholePtFromLines(lyricsEn, rows).trim();
  return { line_by_line: rows, whole_song_pt: whole || null };
}

function renderStructuresForm(lesson) {
  const s = lesson.structures || {};
  const sections = Array.isArray(s.sections) ? s.sections : [];
  let html = '<h2 class="content-heading">Estruturas e gramática</h2><div class="lesson-edit-fields">';
  if (!sections.length) {
    return html + '<p class="muted">Nenhuma seção em structures.sections.</p></div>';
  }
  for (const sec of sections) {
    if (!sec || typeof sec !== "object") continue;
    const exs = Array.isArray(sec.examples_en) ? sec.examples_en.join("\n") : "";
    html += `<div class="section-block structure-edit-block">
      <label class="field-label">Título da secção</label>
      <input type="text" class="input structure-heading" value="${esc(sec.heading || "")}" autocomplete="off" />
      <label class="field-label">Texto (PT)</label>
      <textarea class="input textarea structure-body" rows="5" spellcheck="true">${esc(sec.body_pt || "")}</textarea>
      <label class="field-label">Exemplos (EN), um por linha</label>
      <textarea class="input textarea structure-examples" rows="4" spellcheck="false">${esc(exs)}</textarea>
    </div>`;
  }
  return html + "</div>";
}

function collectStructures() {
  const sections = [];
  document.querySelectorAll("#panel-structures .structure-edit-block").forEach((block) => {
    sections.push({
      heading: block.querySelector(".structure-heading")?.value.trim() || "",
      body_pt: block.querySelector(".structure-body")?.value ?? "",
      examples_en: splitLinesNonEmpty(block.querySelector(".structure-examples")?.value),
    });
  });
  return { sections };
}

function renderVocabularyForm(lesson) {
  const items = Array.isArray(lesson.vocabulary) ? lesson.vocabulary : [];
  let html = '<h2 class="content-heading">Vocabulário</h2><div class="lesson-edit-fields">';
  if (!items.length) {
    return html + '<p class="muted">Lista vazia.</p></div>';
  }
  html +=
    '<div class="table-wrap prose-table-wrap"><table class="prose-table lesson-edit-table"><thead><tr><th>Termo</th><th>Significado (PT)</th><th>Notas</th><th>Colocações (EN)</th></tr></thead><tbody>';
  for (const it of items) {
    if (!it || typeof it !== "object") continue;
    const cols = Array.isArray(it.common_collocations_en) ? it.common_collocations_en.join(", ") : "";
    html += `<tr class="vocab-edit-row">
      <td><input type="text" class="input vocab-term" value="${esc(it.term || "")}" autocomplete="off" /></td>
      <td><input type="text" class="input vocab-meaning" value="${esc(it.meaning_pt || "")}" autocomplete="off" /></td>
      <td><input type="text" class="input vocab-notes" value="${esc(it.notes_pt || "")}" autocomplete="off" /></td>
      <td><input type="text" class="input vocab-cols" value="${esc(cols)}" autocomplete="off" placeholder="separadas por vírgula" /></td>
    </tr>`;
  }
  return html + "</tbody></table></div></div>";
}

function collectVocabulary() {
  const items = [];
  document.querySelectorAll("#panel-vocabulary tr.vocab-edit-row").forEach((tr) => {
    const raw = tr.querySelector(".vocab-cols")?.value ?? "";
    const common_collocations_en = raw
      .split(/[,;]/)
      .map((x) => x.trim())
      .filter(Boolean);
    items.push({
      term: tr.querySelector(".vocab-term")?.value.trim() || "",
      meaning_pt: tr.querySelector(".vocab-meaning")?.value.trim() || "",
      notes_pt: tr.querySelector(".vocab-notes")?.value.trim() || "",
      common_collocations_en,
    });
  });
  return items;
}

function renderExamplesForm(lesson) {
  const d = lesson.examples_and_drills || {};
  const patterns = Array.isArray(d.pattern_drills) ? d.pattern_drills : [];
  let html = '<h2 class="content-heading">Exemplos e fixação</h2><div class="lesson-edit-fields">';
  for (const p of patterns) {
    if (!p || typeof p !== "object") continue;
    const ex = Array.isArray(p.examples_en) ? p.examples_en.join("\n") : "";
    const fp = Array.isArray(p.fixation_prompts_pt) ? p.fixation_prompts_pt.join("\n") : "";
    html += `<div class="section-block pattern-edit-block">
      <label class="field-label">Nome do padrão (PT)</label>
      <input type="text" class="input pattern-name" value="${esc(p.pattern_name_pt || "")}" autocomplete="off" />
      <label class="field-label">Explicação (PT)</label>
      <textarea class="input textarea pattern-expl" rows="3" spellcheck="true">${esc(p.pattern_explanation_pt || "")}</textarea>
      <label class="field-label">Exemplos (EN), um por linha</label>
      <textarea class="input textarea pattern-examples-en" rows="4" spellcheck="false">${esc(ex)}</textarea>
      <label class="field-label">Fixação / prompts (PT), um por linha</label>
      <textarea class="input textarea pattern-fp-pt" rows="4" spellcheck="true">${esc(fp)}</textarea>
    </div>`;
  }
  const mistakes = Array.isArray(d.mistakes_pt_speakers) ? d.mistakes_pt_speakers : [];
  if (mistakes.length) {
    html +=
      '<div class="section-block"><h3 class="section-heading">Erros comuns (falantes de PT)</h3><div class="table-wrap prose-table-wrap"><table class="prose-table lesson-edit-table"><thead><tr><th>Evite</th><th>Melhor</th><th>Por quê (PT)</th></tr></thead><tbody>';
    for (const m of mistakes) {
      if (!m || typeof m !== "object") continue;
      html += `<tr class="mistake-edit-row">
        <td><input type="text" class="input mistake-wrong" value="${esc(m.wrong || "")}" autocomplete="off" /></td>
        <td><input type="text" class="input mistake-better" value="${esc(m.better || "")}" autocomplete="off" /></td>
        <td><input type="text" class="input mistake-why" value="${esc(m.why_pt || "")}" autocomplete="off" /></td>
      </tr>`;
    }
    html += "</tbody></table></div></div>";
  }
  if (!patterns.length && !mistakes.length) {
    html += '<p class="muted">Nenhum conteúdo em examples_and_drills.</p>';
  }
  return html + "</div>";
}

function collectExamples() {
  const pattern_drills = [];
  document.querySelectorAll("#panel-examples .pattern-edit-block").forEach((block) => {
    pattern_drills.push({
      pattern_name_pt: block.querySelector(".pattern-name")?.value.trim() || "",
      pattern_explanation_pt: block.querySelector(".pattern-expl")?.value.trim() || "",
      examples_en: splitLinesNonEmpty(block.querySelector(".pattern-examples-en")?.value),
      fixation_prompts_pt: splitLinesNonEmpty(block.querySelector(".pattern-fp-pt")?.value),
    });
  });
  const mistakes_pt_speakers = [];
  document.querySelectorAll("#panel-examples tr.mistake-edit-row").forEach((tr) => {
    mistakes_pt_speakers.push({
      wrong: tr.querySelector(".mistake-wrong")?.value.trim() || "",
      better: tr.querySelector(".mistake-better")?.value.trim() || "",
      why_pt: tr.querySelector(".mistake-why")?.value.trim() || "",
    });
  });
  return { pattern_drills, mistakes_pt_speakers };
}

function renderCuriositiesForm(lesson) {
  const list = Array.isArray(lesson.curiosities) ? lesson.curiosities : [];
  let html = '<h2 class="content-heading">Curiosidades</h2><div class="lesson-edit-fields">';
  if (!list.length) {
    return html + '<p class="muted">Lista vazia.</p></div>';
  }
  for (const c of list) {
    if (!c || typeof c !== "object") continue;
    const chk = c.needs_verification ? " checked" : "";
    html += `<div class="section-block curio-edit-block">
      <label class="field-label">Título</label>
      <input type="text" class="input curio-title" value="${esc(c.title || "")}" autocomplete="off" />
      <label class="field-label">Texto (PT)</label>
      <textarea class="input textarea curio-body" rows="4" spellcheck="true">${esc(c.body_pt || "")}</textarea>
      <label class="field-label lesson-edit-check"><input type="checkbox" class="curio-verify"${chk} /> Indicar «verificar fonte»</label>
    </div>`;
  }
  return html + "</div>";
}

function collectCuriosities() {
  const list = [];
  document.querySelectorAll("#panel-curiosities .curio-edit-block").forEach((block) => {
    list.push({
      title: block.querySelector(".curio-title")?.value.trim() || "",
      body_pt: block.querySelector(".curio-body")?.value ?? "",
      needs_verification: Boolean(block.querySelector(".curio-verify")?.checked),
    });
  });
  return list;
}

function buildLessonFromPanels() {
  const base =
    lastLoadedLessonSnapshot && typeof lastLoadedLessonSnapshot === "object"
      ? JSON.parse(JSON.stringify(lastLoadedLessonSnapshot))
      : {};
  base.translation = collectTranslation();
  base.structures = collectStructures();
  base.vocabulary = collectVocabulary();
  base.examples_and_drills = collectExamples();
  base.curiosities = collectCuriosities();
  return base;
}

function setTab(name) {
  document.querySelectorAll(".tab-chip").forEach((btn) => {
    const on = btn.dataset.tab === name;
    btn.classList.toggle("active", on);
    btn.setAttribute("aria-selected", on ? "true" : "false");
  });
  const panels = ["translation", "structures", "vocabulary", "examples", "curiosities"];
  for (const p of panels) {
    const el = document.getElementById("panel-" + p);
    if (!el) continue;
    const on = p === name;
    el.classList.toggle("active", on);
    el.hidden = !on;
  }
}

document.querySelectorAll(".tab-chip").forEach((btn) => {
  btn.addEventListener("click", () => setTab(btn.dataset.tab));
});

function displayLesson(lesson) {
  try {
    lastLoadedLessonSnapshot =
      lesson && typeof lesson === "object" ? JSON.parse(JSON.stringify(lesson)) : null;
  } catch (_e) {
    lastLoadedLessonSnapshot = lesson && typeof lesson === "object" ? { ...lesson } : null;
  }
  const editable = editingLessonId != null && isLoggedIn;
  if (editable) {
    $("panel-translation").innerHTML = renderTranslationForm(lesson);
    $("panel-structures").innerHTML = renderStructuresForm(lesson);
    $("panel-vocabulary").innerHTML = renderVocabularyForm(lesson);
    $("panel-examples").innerHTML = renderExamplesForm(lesson);
    $("panel-curiosities").innerHTML = renderCuriositiesForm(lesson);
  } else {
    $("panel-translation").innerHTML = renderTranslation(lesson);
    $("panel-structures").innerHTML = renderStructures(lesson);
    $("panel-vocabulary").innerHTML = renderVocabulary(lesson);
    $("panel-examples").innerHTML = renderExamples(lesson);
    $("panel-curiosities").innerHTML = renderCuriosities(lesson);
  }
  $("result").classList.remove("hidden");
  setTab("translation");
}

function setResultMeta(text) {
  const el = $("result-meta");
  if (!text) {
    el.classList.add("hidden");
    el.textContent = "";
    return;
  }
  el.textContent = text;
  el.classList.remove("hidden");
}

function formatLibraryDate(iso) {
  if (!iso) return "—";
  const normalized = String(iso).trim().replace(" ", "T");
  const d = new Date(normalized);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString("pt-BR", { day: "2-digit", month: "short", year: "numeric" });
}

let librarySearchTimer = null;

function scheduleLoadLibrary() {
  if (librarySearchTimer) clearTimeout(librarySearchTimer);
  librarySearchTimer = setTimeout(() => {
    librarySearchTimer = null;
    loadLibrary();
  }, 300);
}

async function loadLibrary() {
  const st = $("library-status");
  const tbody = $("library-tbody");
  st.textContent = "Carregando…";
  tbody.innerHTML = "";
  try {
    const q = ($("library-search")?.value || "").trim();
    const params = new URLSearchParams({ limit: "200" });
    if (q) params.set("q", q);
    const res = await apiFetch("/api/lessons?" + params.toString());
    const data = await res.json();
    if (!res.ok || !data.ok) {
      st.textContent = data.error || "Falha ao listar.";
      return;
    }
    const groups = data.groups || [];
    const total = typeof data.total === "number" ? data.total : groups.reduce((n, g) => n + (g.lessons || []).length, 0);
    const query = typeof data.query === "string" ? data.query.trim() : "";
    if (!groups.length || !total) {
      tbody.innerHTML =
        "<tr><td colspan=\"3\" class=\"muted\">" +
        (query
          ? `Nenhum resultado para «${esc(query)}».`
          : "Nada na biblioteca ainda. Gere uma lição na aba «Nova lição».") +
        "</td></tr>";
      st.textContent = query ? "" : "";
      return;
    }
    function appendLessonRow(r) {
      const tr = document.createElement("tr");
      tr.className = "library-lesson-row";
      tr.dataset.id = String(r.id);
      const title = r.title_hint && String(r.title_hint).trim() ? r.title_hint : "Sem título";
      const meta = formatLibraryDate(r.created_at);
      const adminActions = isLoggedIn
        ? `<button type="button" class="btn btn-sm btn-secondary btn-edit" data-id="${esc(String(r.id))}">Editar</button>
          <button type="button" class="btn btn-sm btn-ghost-danger btn-del" data-id="${esc(String(r.id))}">Excluir</button>`
        : "";
      tr.innerHTML = `
        <td class="library-music-cell">
          <div class="library-music-title">${esc(title)}</div>
          <div class="library-music-meta">${esc(meta)}</div>
        </td>
        <td class="library-preview-cell">${esc(r.lyrics_preview || "")}</td>
        <td class="cell-actions library-cell-actions">${adminActions}</td>`;
      tr.addEventListener("click", (ev) => {
        if (ev.target.closest(".btn-del") || ev.target.closest(".btn-edit")) return;
        openLesson(r.id);
      });
      const delBtn = tr.querySelector(".btn-del");
      const editBtn = tr.querySelector(".btn-edit");
      if (delBtn) {
        delBtn.addEventListener("click", (ev) => {
          ev.stopPropagation();
          deleteLesson(r.id);
        });
      }
      if (editBtn) {
        editBtn.addEventListener("click", (ev) => {
          ev.stopPropagation();
          editLesson(r.id);
        });
      }
      tbody.appendChild(tr);
    }
    for (const g of groups) {
      const artist = g.artist || "(sem artista)";
      const lessons = g.lessons || [];
      if (!lessons.length) continue;
      const head = document.createElement("tr");
      head.className = "library-group-row";
      head.innerHTML = `<td colspan="3" class="library-artist-cell">${esc(artist)}</td>`;
      tbody.appendChild(head);
      for (const r of lessons) appendLessonRow(r);
    }
    let stMsg = `${total} registro(s) · ${groups.filter((g) => (g.lessons || []).length).length} artista(s).`;
    if (query) stMsg += ` · filtro: «${query}»`;
    st.textContent = stMsg;
    if (!query) {
      const flat = [];
      for (const g of groups) {
        for (const r of g.lessons || []) flat.push(lessonToTrainEntry(r));
      }
      libraryTrainingPool = flat;
      syncTrainPoolHint();
    }
  } catch (e) {
    st.textContent = String(e);
  }
}

async function openLesson(id, opts = {}) {
  const training = Boolean(opts.training);
  clearEditingMode();
  if (training) {
    trainingModeActive = true;
    setTrainingBanner(true, opts.trainLabel || "");
  } else {
    clearTrainingMode();
  }
  const st = $("status");
  st.textContent = training ? "A preparar treino…" : "Abrindo lição…";
  try {
    const res = await apiFetch("/api/lessons/" + encodeURIComponent(String(id)));
    const data = await res.json();
    if (!res.ok || !data.ok) {
      st.textContent = data.error || "Não encontrado.";
      return;
    }
    $("lyrics").value = data.lyrics_en || "";
    $("title").value = data.title_hint || "";
    $("artist").value = data.artist_hint || "";
    displayLesson(data.lesson);
    $("error-panel").classList.add("hidden");
    const title = data.title_hint && String(data.title_hint).trim() ? data.title_hint : "Sem título";
    const artist = data.artist_hint && String(data.artist_hint).trim() ? data.artist_hint : "";
    const trainLabel = opts.trainLabel || formatTrainLabel({ title, artist: artist || "Artista desconhecido" });
    if (training) {
      setTrainingBanner(true, trainLabel);
      setResultMeta(`Treino · ${trainLabel}`);
      setTab("translation");
    } else {
      setResultMeta(`Lição #${data.id} · ${formatLibraryDate(data.created_at)}`);
      setTab("translation");
    }
    setView("create");
    $("result").scrollIntoView({ behavior: "smooth", block: "start" });
    st.textContent = training ? "Boa prática!" : "";
  } catch (e) {
    st.textContent = String(e);
  }
}

async function editLesson(id) {
  if (!isLoggedIn) {
    $("status").textContent = "Entre na sua conta para editar.";
    return;
  }
  const st = $("status");
  st.textContent = "A carregar para edição…";
  try {
    const res = await apiFetch("/api/lessons/" + encodeURIComponent(String(id)));
    const data = await res.json();
    if (!res.ok || !data.ok) {
      st.textContent = data.error || "Não encontrado.";
      return;
    }
    $("lyrics").value = data.lyrics_en || "";
    $("title").value = data.title_hint || "";
    $("artist").value = data.artist_hint || "";
    editingLessonId = id;
    setEditBanner(true, id);
    displayLesson(data.lesson);
    $("error-panel").classList.add("hidden");
    setResultMeta(`A editar · #${data.id} · ${formatLibraryDate(data.created_at)}`);
    setView("create");
    $("lyrics").scrollIntoView({ behavior: "smooth", block: "center" });
    st.textContent = "";
    syncEditToolbar();
  } catch (e) {
    st.textContent = String(e);
  }
}

async function deleteLesson(id) {
  if (!isLoggedIn) {
    $("library-status").textContent = "Entre na sua conta para excluir.";
    return;
  }
  if (!confirm("Excluir esta lição do banco local?")) return;
  if (editingLessonId === id) {
    clearEditingMode();
    $("result").classList.add("hidden");
    setResultMeta("");
  }
  $("library-status").textContent = "Excluindo…";
  try {
    const res = await apiFetch("/api/lessons/" + encodeURIComponent(String(id)), { method: "DELETE" });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      $("library-status").textContent = data.error || "Falha ao excluir.";
      return;
    }
    await loadLibrary();
  } catch (e) {
    $("library-status").textContent = String(e);
  }
}

$("btn-refresh-library").addEventListener("click", () => {
  if (librarySearchTimer) clearTimeout(librarySearchTimer);
  librarySearchTimer = null;
  loadLibrary();
});

async function downloadBackup() {
  if (!isAdmin) {
    $("library-status").textContent = "Apenas o administrador pode exportar o backup.";
    return;
  }
  const st = $("library-status");
  st.textContent = "A preparar backup…";
  try {
    const res = await fetch("/api/backup", { credentials: "same-origin" });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      st.textContent = data.error || `Falha ao exportar (HTTP ${res.status}).`;
      return;
    }
    const blob = await res.blob();
    let name = "trusicas-backup.sqlite";
    const dispo = res.headers.get("Content-Disposition") || "";
    const m = /filename\*?=(?:UTF-8''|")?([^";]+)"?/i.exec(dispo);
    if (m) name = decodeURIComponent(m[1].trim());
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = name;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
    st.textContent = "Backup descarregado.";
  } catch (e) {
    st.textContent = String(e);
  }
}

async function restoreBackupFromFile(file) {
  if (!isAdmin) {
    $("library-status").textContent = "Apenas o administrador pode restaurar o backup.";
    return;
  }
  if (!file) return;
  if (
    !confirm(
      "Restaurar este backup? Todas as lições actuais serão substituídas. O servidor guarda uma cópia automática do ficheiro anterior."
    )
  ) {
    return;
  }
  const st = $("library-status");
  st.textContent = "A restaurar backup…";
  try {
    const fd = new FormData();
    fd.append("file", file);
    const res = await fetch("/api/backup", {
      method: "POST",
      body: fd,
      credentials: "same-origin",
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || !data.ok) {
      st.textContent = data.error || "Falha ao restaurar.";
      return;
    }
    clearNewLessonForm({ hideResult: true, focusLyrics: false });
    libraryTrainingPool = [];
    await loadLibrary();
    await refreshTrainingPool();
    syncTrainPoolHint();
    st.textContent = `Backup restaurado · ${data.lessons ?? 0} lição(ões).`;
  } catch (e) {
    st.textContent = String(e);
  }
}

$("btn-download-backup")?.addEventListener("click", () => downloadBackup());
$("backup-file-input")?.addEventListener("change", (ev) => {
  const input = ev.target;
  const file = input.files && input.files[0];
  if (file) restoreBackupFromFile(file);
  input.value = "";
});

$("btn-random-train")?.addEventListener("click", () => spinRandomTraining());
$("btn-another-random")?.addEventListener("click", () => spinRandomTraining());
$("btn-exit-training")?.addEventListener("click", () => {
  clearTrainingMode();
  $("status").textContent = "";
});

const libSearch = $("library-search");
if (libSearch) {
  libSearch.addEventListener("input", scheduleLoadLibrary);
  libSearch.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter") {
      ev.preventDefault();
      if (librarySearchTimer) clearTimeout(librarySearchTimer);
      librarySearchTimer = null;
      loadLibrary();
    }
  });
}

$("btn-cancel-edit").addEventListener("click", () => {
  const snap = lastLoadedLessonSnapshot;
  clearEditingMode();
  if (snap && $("result") && !$("result").classList.contains("hidden")) {
    displayLesson(snap);
  }
  $("status").textContent = "";
});

async function saveLessonTextOnly() {
  if (!isLoggedIn || editingLessonId == null) return;
  const lyrics = $("lyrics").value.trim();
  const status = $("status");
  if (!lyrics) {
    status.textContent = "Cole a letra antes de guardar.";
    return;
  }
  let lessonObj;
  try {
    lessonObj = buildLessonFromPanels();
  } catch (e) {
    status.textContent = "Erro ao ler os campos da lição: " + String(e);
    return;
  }
  if (typeof lessonObj !== "object" || lessonObj === null || Array.isArray(lessonObj)) {
    status.textContent = "Estrutura da lição inválida.";
    return;
  }
  const btn = $("btn-save-lesson");
  btn.disabled = true;
  status.textContent = "A guardar…";
  try {
    const res = await apiFetch("/api/lessons/" + encodeURIComponent(String(editingLessonId)), {
      method: "PATCH",
      body: JSON.stringify({
        lyrics,
        title: $("title").value.trim() || null,
        artist: $("artist").value.trim() || null,
        lesson: lessonObj,
      }),
    });
    const data = await res.json();
    if (!res.ok || !data.ok) {
      status.textContent = data.error || "Falha ao guardar.";
      return;
    }
    const saved = data.saved;
    if (saved && saved.id != null) {
      setResultMeta(`Lição guardada · #${saved.id} · ${formatLibraryDate(saved.created_at)}`);
    }
    displayLesson(lessonObj);
    status.textContent = "Alterações guardadas na base local.";
  } catch (e) {
    status.textContent = String(e);
  } finally {
    btn.disabled = false;
  }
}

$("btn-save-lesson").addEventListener("click", () => saveLessonTextOnly());

$("btn-new-lesson")?.addEventListener("click", () => {
  if (!isLoggedIn) {
    $("status").textContent = "Entre na sua conta para criar lições.";
    return;
  }
  clearNewLessonForm({ hideResult: true, focusLyrics: true });
  $("status").textContent = "Formulário limpo — pronto para a próxima música.";
});

$("btn-generate").addEventListener("click", async () => {
  if (!isLoggedIn) {
    $("status").textContent = "Entre na sua conta para gerar ou alterar lições.";
    return;
  }
  const lyrics = $("lyrics").value.trim();
  const status = $("status");
  const errPanel = $("error-panel");
  const errText = $("error-text");
  const errRaw = $("error-raw");
  const result = $("result");
  const btn = $("btn-generate");

  errPanel.classList.add("hidden");
  result.classList.add("hidden");
  setResultMeta("");
  status.textContent = "";
  if (editingLessonId == null) setEditBanner(false);

  if (!lyrics) {
    status.textContent = "Cole a letra antes de gerar.";
    return;
  }

  btn.disabled = true;
  const updating = editingLessonId != null;
  setGenerateLoading(true, { updating });
  status.textContent = "";

  try {
    const payload = {
      lyrics,
      title: $("title").value.trim() || null,
      artist: $("artist").value.trim() || null,
    };
    if (editingLessonId != null) payload.replace_lesson_id = editingLessonId;

    const res = await apiFetch("/api/generate", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    const elapsedMs = Date.now() - generateStartedAt;

    if (!res.ok || !data.ok) {
      errPanel.classList.remove("hidden");
      errText.textContent = data.error || "Falha desconhecida.";
      const raw = data.raw;
      errRaw.textContent =
        raw != null && String(raw).trim() !== ""
          ? String(raw)
          : "(sem texto bruto — o modelo não devolveu conteúdo em message.content ou houve falha antes da resposta.)";
      status.textContent = "";
      return;
    }

    recordGenDuration(elapsedMs);
    const lesson = data.lesson;
    displayLesson(lesson);
    const saved = data.saved;
    const replaced = Boolean(data.replaced);
    if (saved && saved.id != null) {
      if (replaced) {
        editingLessonId = saved.id;
        setEditBanner(true, saved.id);
        syncEditToolbar();
        setResultMeta(`Lição atualizada #${saved.id} · ${formatLibraryDate(saved.created_at)}`);
        status.textContent = `Lição atualizada em ${formatDurationShort(elapsedMs)}.`;
      } else {
        setResultMeta(`Salvo · #${saved.id} · ${formatLibraryDate(saved.created_at)}`);
        startNextLessonAfterGenerate(saved, {
          title: payload.title || "",
          artist: payload.artist || "",
          elapsedMs,
        });
      }
    } else {
      clearNewLessonForm({ hideResult: false });
      status.textContent = `Pronto em ${formatDurationShort(elapsedMs)}. Cole a próxima letra acima.`;
      $("create-form-surface")?.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  } catch (e) {
    errPanel.classList.remove("hidden");
    errText.textContent = String(e);
    errRaw.textContent = "";
    status.textContent = "";
  } finally {
    btn.disabled = false;
    setGenerateLoading(false);
  }
});

const THEME_KEY = "trusicas-theme";

function syncThemeUi() {
  const theme = document.documentElement.getAttribute("data-theme") || "dark";
  document.querySelectorAll(".theme-choice").forEach((btn) => {
    const on = btn.dataset.theme === theme;
    btn.classList.toggle("active", on);
    btn.setAttribute("aria-pressed", on ? "true" : "false");
  });
}

document.querySelectorAll(".theme-choice").forEach((btn) => {
  btn.addEventListener("click", () => {
    const t = btn.dataset.theme;
    if (t !== "light" && t !== "dark") return;
    document.documentElement.setAttribute("data-theme", t);
    try {
      localStorage.setItem(THEME_KEY, t);
    } catch (_e) {
      /* ignore */
    }
    syncThemeUi();
  });
});

syncThemeUi();

$("login-form")?.addEventListener("submit", (ev) => doLogin(ev));
$("btn-admin-logout")?.addEventListener("click", () => doLogout());
$("btn-open-settings")?.addEventListener("click", () => openSettingsModal());
$("settings-form")?.addEventListener("submit", (ev) => saveSettings(ev));
document.querySelectorAll("[data-close-settings-modal]").forEach((el) => {
  el.addEventListener("click", () => closeSettingsModal());
});
$("btn-open-users")?.addEventListener("click", () => openUsersModal());
$("users-create-form")?.addEventListener("submit", (ev) => createUserFromForm(ev));
document.querySelectorAll("[data-close-users-modal]").forEach((el) => {
  el.addEventListener("click", () => closeUsersModal());
});
document.querySelectorAll(".lyrics-mode-btn").forEach((btn) => {
  btn.addEventListener("click", () => setLyricsInputMode(btn.dataset.lyricsMode || "paste"));
});
$("btn-fetch-lyrics")?.addEventListener("click", () => fetchLyricsFromWeb());
document.addEventListener("keydown", (ev) => {
  if (ev.key !== "Escape") return;
  if ($("users-modal") && !$("users-modal").classList.contains("hidden")) closeUsersModal();
  else if ($("settings-modal") && !$("settings-modal").classList.contains("hidden")) closeSettingsModal();
});

setLyricsInputMode("paste");
refreshAuth();
