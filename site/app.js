/* Dashboard for daily snapshots and diffs published alongside this site. */

const PLATFORM_ORDER = [
  ["wx", "微信小游戏"],
  ["douyin", "抖音小游戏"],
  ["ios", "iOS App Store"],
  ["android", "Android"],
  ["taptap", "TapTap"],
];
const BOARD_LABELS = {
  wx: ["畅销榜", "畅玩榜", "人气榜"],
  douyin: ["畅销榜", "热门榜", "新游榜"],
  taptap: ["预约榜"],
  ios: ["美区免费榜", "国区免费榜", "日区免费榜"],
  android: ["美区免费榜"],
};

// Icon per board name (works across platforms).
const BOARD_ICON = {
  "畅销榜": "💰",
  "畅玩榜": "🎮",
  "人气榜": "🔥",
  "热门榜": "⚡",
  "新游榜": "✨",
  "预约榜": "📅",
  "免费榜": "🍎",
};

// Icon per platform (top-level tab).
const PLATFORM_ICON = {
  wx: "💬",
  douyin: "🎵",
  taptap: "📅",
  ios: "🍎",
  android: "🤖",
};

// Rows rendered per "page" in the full-board table.
const PAGE_SIZE = 50;

const state = {
  currentDate: null,          // YYYY-MM-DD (Beijing)
  availableDates: [],         // sorted asc
  diffDates: new Set(),
  dailyData: new Map(),        // date -> shared snapshot and diff request
  // cache[date]["plat/label"] = { rows, diff }
  cache: {},
  activePlatform: null,       // current top-level platform key
  activeBoard: null,          // {plat, label}
  lastBoard: {},              // plat -> last chosen label (remembered)
  fullBoardPage: 0,          // pages already rendered for the active board
};

function $(id) { return document.getElementById(id); }

function platLabelOf(key) {
  return ({ wx: "微信小游戏", douyin: "抖音小游戏", taptap: "TapTap",
            ios: "iOS App Store", android: "Android" })[key] || key;
}

function escapeHTML(s) {
  return String(s ?? "").replace(/[&<>"']/g, m => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  }[m]));
}

function boardKey(plat, label) { return `${plat}/${label}`; }

function cacheEntry(date, plat, label) {
  return state.cache[date] && state.cache[date][boardKey(plat, label)];
}
function setCacheEntry(date, plat, label, data) {
  ((state.cache[date] ||= {}))[boardKey(plat, label)] = data;
}

// ---------- data loading ----------

async function fetchData(path) {
  const response = await fetch(`./data/${path}`, { cache: "no-cache" });
  if (!response.ok) throw new Error(`${path} (HTTP ${response.status})`);
  return response.json();
}

async function loadAvailableDates() {
  const index = await fetchData("index.json");
  state.availableDates = index.daily.slice().sort();
  state.diffDates = new Set(index.diff);
  return state.availableDates;
}

async function loadBoardData(date, plat, label) {
  if (!state.dailyData.has(date)) {
    const request = Promise.all([
      fetchData(`daily/${date}.json`),
      state.diffDates.has(date) ? fetchData(`diff/${date}.json`) : null,
    ]).catch(error => {
      state.dailyData.delete(date);
      throw error;
    });
    state.dailyData.set(date, request);
  }
  const [snapshot, diff] = await state.dailyData.get(date);
  const board = snapshot.platforms?.[plat]?.boards?.find(b => b.label === label);
  return {
    rows: board?.rows ?? [],
    diff: diff?.boards?.[boardKey(plat, label)] ?? null,
    error: board?.error,
  };
}

// ---------- UI: tabs / date picker ----------

function buildPlatformTabs() {
  const nav = $("platform-tabs");
  nav.innerHTML = "";
  for (const [plat, platLabel] of PLATFORM_ORDER) {
    const btn = document.createElement("button");
    btn.className = `platform-tab platform-tab-${plat}`;
    btn.innerHTML = `<span class="tab-icon">${PLATFORM_ICON[plat] || "•"}</span>`
      + `<span class="tab-label">${escapeHTML(platLabel)}</span>`;
    btn.dataset.plat = plat;
    btn.onclick = () => setActivePlatform(plat);
    nav.appendChild(btn);
  }
}

function setActivePlatform(plat) {
  state.activePlatform = plat;
  state.fullBoardPage = 0;
  updatePlatformTabsActive();
  buildBoardTabs();
  // Remember the last board chosen per platform, else the first board.
  const label = state.lastBoard[plat] || BOARD_LABELS[plat][0];
  setActiveBoard(plat, label);
}

function updatePlatformTabsActive() {
  for (const btn of document.querySelectorAll(".platform-tab")) {
    btn.classList.toggle("active", btn.dataset.plat === state.activePlatform);
  }
}

function buildBoardTabs() {
  const nav = $("board-tabs");
  nav.innerHTML = "";
  if (!state.activePlatform) return;
  for (const label of BOARD_LABELS[state.activePlatform]) {
    const btn = document.createElement("button");
    btn.className = `board-tab board-tab-${state.activePlatform}`;
    const iconKey = Object.keys(BOARD_ICON).find(k => label.includes(k));
    const icon = iconKey ? BOARD_ICON[iconKey] : "•";
    btn.innerHTML = `<span class="tab-icon">${icon}</span>`
      + `<span class="tab-label">${escapeHTML(label.replace("榜",""))}</span>`;
    btn.dataset.plat = state.activePlatform;
    btn.dataset.label = label;
    btn.onclick = () => setActiveBoard(state.activePlatform, label);
    nav.appendChild(btn);
  }
}

function updateTabActive() {
  const ab = state.activeBoard;
  for (const btn of document.querySelectorAll(".board-tab")) {
    btn.classList.toggle(
      "active",
      !!ab && btn.dataset.plat === ab.plat && btn.dataset.label === ab.label,
    );
  }
}

async function setActiveBoard(plat, label) {
  const date = state.currentDate;
  state.activeBoard = { plat, label };
  const selection = state.activeBoard;
  state.lastBoard[plat] = label;
  state.fullBoardPage = 0;
  updateTabActive();

  // Cached for this date → render instantly, no network.
  if (cacheEntry(date, plat, label)) {
    renderBoardViews();
    return;
  }

  showBoardLoading();
  try {
    const data = await loadBoardData(date, plat, label);
    setCacheEntry(date, plat, label, data);
  } catch (e) {
    if (state.currentDate === date && state.activeBoard === selection) showBoardError(e);
    return;
  }
  if (state.currentDate === date && state.activeBoard === selection) renderBoardViews();
}

function renderDatePicker() {
  const sel = $("date-picker");
  sel.innerHTML = "";
  const days = state.availableDates.slice().reverse();
  for (const d of days) {
    const opt = document.createElement("option");
    opt.value = d;
    opt.textContent = d;
    sel.appendChild(opt);
  }
  sel.value = state.currentDate;
  $("date-load").onclick = async () => {
    const d = sel.value;
    if (!d || d === state.currentDate) return;
    const ab = state.activeBoard;
    $("date-load").disabled = true;
    try {
      state.currentDate = d;
      renderKPIs();
      await setActiveBoard(ab.plat, ab.label);
    } finally {
      $("date-load").disabled = false;
    }
  };
}

// ---------- UI: rendering ----------

function getBoardData(plat, label) {
  return cacheEntry(state.currentDate, plat, label) || { rows: [], diff: null };
}
function findRows(plat, label) { return getBoardData(plat, label).rows; }

function renderKPIs() {
  const dateLabel = state.currentDate || "—";
  $("meta-date").textContent = `最新数据：${dateLabel}（北京时间）`;
  $("meta-days").textContent = state.availableDates.length;
}

function renderDiffTables() {
  const ab = state.activeBoard;
  if (!ab) return;
  const { diff, error } = getBoardData(ab.plat, ab.label);
  $("panel-new-board").textContent =
    ` · ${platLabelOf(ab.plat)} · ${ab.label}`;

  const dateLabel = state.currentDate || "—";
  $("panel-new-title").textContent = `新进产品 · ${dateLabel}`;

  const hint = $("panel-new-hint");
  if (state.currentDate) {
    const nextDay = new Date(state.currentDate + "T00:00:00");
    nextDay.setDate(nextDay.getDate() + 1);
    const y = nextDay.getFullYear();
    const m = String(nextDay.getMonth() + 1).padStart(2, "0");
    const d = String(nextDay.getDate()).padStart(2, "0");
    hint.textContent = `${y}-${m}-${d} 数据将于当日 10:30 更新`;
  } else {
    hint.textContent = "";
  }

  const baselineEl = $("panel-new-baseline");
  if (baselineEl) baselineEl.textContent = "对照此榜全部历史快照。";

  const tbodyG = document.querySelector("#tbl-new tbody");
  tbodyG.innerHTML = "";

  if (!diff || error) {
    const cnt = $("panel-new-count");
    if (cnt) cnt.textContent = "";
    const message = error ? "本次抓取失败，无法比较" : "尚无对比数据";
    tbodyG.innerHTML = `<tr><td colspan="7" class="muted">${message}</td></tr>`;
    return;
  }

  const newPubSet = new Set((diff.new_publishers || []).map(r => r.publisher).filter(Boolean));

  const rowsTagged = [
    ...(diff.new_to_board || []).map(r => ({ ...r, _tag: "新进榜", _cls: "first-board" })),
    ...(diff.returning   || []).map(r => ({ ...r, _tag: "回归",   _cls: "returning" })),
  ].sort((a, b) => (a.rank ?? 999) - (b.rank ?? 999));

  const cnt = $("panel-new-count");
  if (cnt) cnt.textContent = `（共 ${rowsTagged.length} 个）`;

  if (!rowsTagged.length) {
    tbodyG.innerHTML = `<tr><td colspan="7" class="muted">今日此榜无新进 / 回归</td></tr>`;
    return;
  }
  // Enrich diff rows with category / subcategory / slogan from today's snapshot.
  const snapMap = new Map(
    findRows(ab.plat, ab.label).map(r => [r.name, r]),
  );
  for (const r of rowsTagged) {
    const s = snapMap.get(r.name) || {};
    const category = s.category || "";
    const subcategory = s.subcategory || "";
    const slogan = s.slogan || "";
    const hasPub = !!(r.publisher && String(r.publisher).trim());
    const isNewPub = hasPub && newPubSet.has(r.publisher);
    let studioCell;
    if (!hasPub) {
      studioCell = `<span class="badge studio-unknown">未知</span>`;
    } else if (isNewPub) {
      studioCell = `<span class="badge new-pub">新厂</span>`;
    } else {
      studioCell = `<span class="badge studio-old">老厂</span>`;
    }
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td><span class="badge ${r._cls}">${r._tag}</span></td>
      <td class="rank-num">${r.rank ?? ""}</td>
      <td><strong>${escapeHTML(r.name || "")}</strong>${profileBtn(r.name || "")}</td>
      <td>${escapeHTML(category)}</td>
      <td class="cell-truncate" title="${escapeHTML(subcategory || slogan)}">${escapeHTML(subcategory) || `<span class="slogan">${escapeHTML(slogan)}</span>`}</td>
      <td>${escapeHTML(r.publisher || "")}</td>
      <td>${studioCell}</td>`;
    tbodyG.appendChild(tr);
  }
}

function profileBtn(name) {
  const url = "./game.html?name=" + encodeURIComponent(name);
  return ` <a href="${url}" class="profile-link" title="产品档案">档案</a>`;
}
function changeCell(r) {
  const dir = r.change_direction;
  const raw = (r.change || "").trim();
  const cleaned = raw.replace(/^-\s*/, "").trim();
  if (dir === "top")  return `<span class="chg chg-top">${escapeHTML(cleaned || raw)}</span>`;
  if (dir === "flat") return `<span class="chg chg-flat">— 稳定</span>`;
  if (dir === "new")  return `<span class="chg chg-new">新</span>`;
  if (dir === "up" || dir === "down") {
    const arrow = dir === "up" ? "▼" : "▲";
    const cls = dir === "up" ? "chg-up" : "chg-down";
    const mag = cleaned.match(/\d+/)?.[0] || cleaned;
    return `<span class="chg ${cls}">${arrow} ${escapeHTML(mag)}</span>`;
  }
  return escapeHTML(raw);
}

function renderFullBoard() {
  const ab = state.activeBoard;
  if (!ab) return;
  const { rows, diff, error } = getBoardData(ab.plat, ab.label);
  $("panel-board-name").textContent =
    `${platLabelOf(ab.plat)} · ${ab.label} · 共 ${rows.length} 条`;
  const tbody = document.querySelector("#tbl-full tbody");
  tbody.innerHTML = "";
  if (!rows.length) {
    const message = error ? `榜单抓取失败：${escapeHTML(error)}` : "所选日期暂无此榜单数据";
    tbody.innerHTML = `<tr><td colspan="7" class="muted">${message}</td></tr>`;
    return;
  }
  const newGameSet = new Set(((diff?.new_to_board) || []).map(r => r.name));

  // Always render in rank order (ascending). No user-sorting anymore.
  const sorted = [...rows].sort(
    (a, b) => (a.rank ?? 9999) - (b.rank ?? 9999),
  );

  const showCount = Math.min((state.fullBoardPage + 1) * PAGE_SIZE, sorted.length);
  for (let i = 0; i < showCount; i++) {
    const r = sorted[i];
    const tr = document.createElement("tr");
    const isNew = newGameSet.has(r.name);
    tr.innerHTML = `
      <td class="rank-num">${r.rank ?? ""}</td>
      <td><strong>${escapeHTML(r.name || "")}</strong>${profileBtn(r.name || "")}
        ${r.slogan ? `<div class="slogan" title="${escapeHTML(r.slogan)}">${escapeHTML(r.slogan)}</div>` : ""}</td>
      <td>${escapeHTML(r.category || "")}${r.category_rank ? ` <span class="muted">#${r.category_rank}</span>` : ""}</td>
      <td>${escapeHTML(r.subcategory || "")}</td>
      <td>${escapeHTML(r.publisher || "")}</td>
      <td>${changeCell(r)}</td>
      <td>${isNew ? `<span class="badge-new">NEW</span>` : ""}</td>`;
    tbody.appendChild(tr);
  }

  // "Load more" row if there are still rows off-page.
  if (showCount < sorted.length) {
    const remaining = sorted.length - showCount;
    const more = document.createElement("tr");
    more.className = "load-more-row";
    more.innerHTML = `<td colspan="7" class="load-more-cell">
      <button type="button" id="tbl-full-more" class="load-more-btn">
        加载更多（还有 ${remaining} 条）
      </button></td>`;
    tbody.appendChild(more);
    $("tbl-full-more").onclick = () => {
      state.fullBoardPage++;
      renderFullBoard();
    };
  }
}

function showBoardLoading() {
  const ab = state.activeBoard;
  if (ab) {
    $("panel-board-name").textContent =
      `${platLabelOf(ab.plat)} · ${ab.label} · 加载中…`;
  }
  const tFull = document.querySelector("#tbl-full tbody");
  tFull.innerHTML = `<tr><td colspan="7" class="muted loading-cell">加载中…</td></tr>`;
  const tNew = document.querySelector("#tbl-new tbody");
  tNew.innerHTML = `<tr><td colspan="7" class="muted loading-cell">加载中…</td></tr>`;
  const cnt = $("panel-new-count");
  if (cnt) cnt.textContent = "";
}

function showBoardError(e) {
  const message = `<tr><td colspan="7" class="muted">加载失败：${escapeHTML(e.message)}</td></tr>`;
  document.querySelector("#tbl-full tbody").innerHTML = message;
  document.querySelector("#tbl-new tbody").innerHTML = message;
}

function renderBoardViews() {
  renderDiffTables();
  renderFullBoard();
}

async function init() {
  buildPlatformTabs();

  try {
    const dates = await loadAvailableDates();
    if (!dates.length) throw new Error("no snapshots yet");
    state.currentDate = dates[dates.length - 1];
    renderDatePicker();
    renderKPIs();
    // setActivePlatform renders the platform's board tabs and activates
    // the first (or last remembered) board.
    setActivePlatform("wx");
  } catch (e) {
    document.body.insertAdjacentHTML(
      "afterbegin",
      `<div style="background:#3d1d1d;color:#ff9696;padding:10px 32px">
        数据加载失败：${escapeHTML(e.message)}
      </div>`,
    );
  }
}

init();
