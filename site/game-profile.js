/* Game profile page: product-dimension list -> click into edit view.
 * Read via anon key; write via save-profile Edge Function (x-client-info admin key).
 */
(function () {
  const FN_URL = "https://pjwwwxanhtvzkscumedm.supabase.co/functions/v1/save-profile";
  const ADMIN_KEY = (window.APP_CONFIG && window.APP_CONFIG.PROFILE_ADMIN_KEY) || "";
  const SB_URL = (window.APP_CONFIG && window.APP_CONFIG.SUPABASE_URL) || "";
  const ASSET_VERSION = (window.APP_CONFIG && window.APP_CONFIG.ASSET_VERSION) || "1";
  const assetUrl = (path) => path + "?v=" + encodeURIComponent(ASSET_VERSION);
  let baseGamesRequest;
  let storeAppsRequest;
  let descriptionObserver;
  let descriptionId = 0;
  let hlsRequest;
  const miniGameRequests = new Map();
  let miniIconRequest = 0;

  function miniGameData(path) {
    if (!miniGameRequests.has(path)) {
      miniGameRequests.set(path, fetch(assetUrl(path), { cache: "no-cache" })
        .then(response => response.ok ? response.json() : {})
        .catch(() => ({})));
    }
    return miniGameRequests.get(path);
  }

  async function renderMiniGameIcon(name, { platform, board, snapshot } = {}) {
    const request = ++miniIconRequest;
    if (!["wx", "douyin"].includes(platform)) return;
    const current = () => state.current === name && request === miniIconRequest;
    const path = /^\d{4}-\d{2}-\d{2}$/.test(snapshot || "")
      ? `data/daily/${snapshot}.json` : "data/latest.json";
    const find = data => (data.platforms?.[platform]?.boards || [])
      .filter(item => !board || item.label === board)
      .flatMap(item => item.rows || []).find(row => row.name === name);
    let row = find(await miniGameData(path));
    let iconRow = row;
    if (!row?.icon) {
      const base = await loadBaseGames();
      const dates = Object.entries(base[name]?.board_history || {})
        .filter(([key]) => key.startsWith(platform + "/") && (!board || key === `${platform}/${board}`))
        .map(([, entry]) => entry.last_seen).filter(date => /^\d{4}-\d{2}-\d{2}$/.test(date || "")).sort();
      if (dates.length) {
        const fallback = find(await miniGameData(`data/daily/${dates.at(-1)}.json`));
        row ||= fallback;
        iconRow = fallback || row;
      }
    }
    let icon = iconRow?.icon;
    if (!icon && iconRow?.app_id) icon = (await miniGameData("data/base/game-icons.json"))[platform]?.[iconRow.app_id];
    if (!icon) {
      try {
        await loadDetailIndex();
        const key = window.GameDetail?.normName ? window.GameDetail.normName(name) : name;
        const detail = state.detailIndex[key];
        // Name-only archives may belong to the other platform; require a matching source.
        if (detail?.boards?.some(key => key.startsWith(platform + "/"))) icon = detail.icon;
      } catch { /* Keep the existing placeholder when optional metadata is unavailable. */ }
    }
    if (current()) {
      if (row) {
        state.profileRank = { name, publisher: row.publisher || "", category: row.category || "" };
        renderView(state.profiles[name] || { game_name: name }, false);
      }
      if (icon) renderProfileIcon(name, icon);
    }
  }

  function loadBaseGames() {
    return baseGamesRequest ||= fetch(assetUrl("data/base/games.json"), { cache: "no-cache" })
      .then((r) => r.ok ? r.json() : null)
      .then((base) => base?.games || {})
      .catch(() => ({}));
  }

  function baseGame(name, entry) {
    return {
      name,
      first_seen_at: entry?.first_seen_anywhere || "",
      category: entry?.categories?.[0] || "",
      publisher_name: entry?.publishers?.[0] || "",
    };
  }

  function preferRankFields(game, fallback) {
    return {
      ...game,
      first_seen_at: game.first_seen_at || fallback?.first_seen_at,
      publisher_name: fallback ? fallback.publisher_name : game.publisher_name,
      category: fallback ? fallback.category : game.category,
    };
  }

  async function readOptional(table, options) {
    try {
      return await window.sb.select(table, { ...options, signal: AbortSignal.timeout(8000) });
    } catch (error) {
      console.warn("档案补充数据加载失败", table, error.name);
      return table === "game_profiles" ? null : [];
    }
  }

  const state = {
    games: [],        // [{name}]
    profiles: {},     // name -> profile row
    profilesLoaded: false,
    shots: {},        // name -> [shot rows]
    list: [],         // computed merged list
    current: null,
    isNew: false,   // new-game mode
    dirty: false,
    abandoned: false,
    boardMap: {},        // name -> board_history
    latestRankMap: {},   // name -> {"wx/人气榜": 11} 当前榜单排名
    page: 0,             // 列表当前页
    detailIndex: {},      // 游戏名 -> 详情页索引（成本档位/复刻结论/引擎）
    filter: "",           // 价值/放弃筛选
    platformFilter: "wx", // wx | douyin | ios | android | taptap
    boardFilter: "畅销榜", // specific board under platformFilter
    favOnly: false,       // 我的收藏模式
    fullListLoaded: false,
  };

  const $ = (id) => document.getElementById(id);

  function esc(s) {
    return String(s ?? "").replace(/[&<>"']/g, (m) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[m]));
  }

  const BOARD_PLATFORM = { wx: "微信", douyin: "抖音", taptap: "TapTap", ios: "iOS", android: "Google Play" };
  // Fixed platform ordering for board tags: 微信 → 抖音 → iOS → 安卓 → TapTap.
  const PLATFORM_SEQ = { wx: 0, douyin: 1, ios: 2, android: 3, taptap: 4 };
  // Boards available under each platform (mirrors site/app.js BOARD_LABELS).
  const PLATFORM_BOARDS = {
    wx: ["畅销榜", "畅玩榜", "人气榜"],
    douyin: ["畅销榜", "热门榜", "新游榜"],
    ios: ["美区免费榜", "国区免费榜", "日区免费榜"],
    android: ["美区免费榜", "美区免费新游榜"],
    taptap: ["预约榜"],
  };
  function boardSeq(key) {
    const plat = String(key).split("/")[0];
    return PLATFORM_SEQ[plat] ?? 99;
  }
  function sortBoardKeys(keys) {
    return [...keys].sort((a, b) => boardSeq(a) - boardSeq(b)
      || String(a).localeCompare(String(b), "zh"));
  }

  function fmtBoard(key, name) {
    // "wx/人气榜" -> "微信·人气榜"; name 存在时附加名次:
    //   首次上榜名次优先（first_rank，base 库）-> "微信·畅销榜·首上 11"
    //   缺 first_rank 时回退当前排名 / 历史最佳
    const [plat, board] = String(key).split("/");
    const base = (BOARD_PLATFORM[plat] || plat) + "\u00b7" + (board || "");
    if (!name) return base;
    const bh = (state.boardMap[name] || {})[key];
    if (bh && bh.first_rank) return base + "\u00b7 首上 " + bh.first_rank;
    const rank = (state.latestRankMap[name] || {})[key];
    if (rank) return base + "\u00b7" + rank;
    if (bh && bh.best_rank) return base + "\u00b7 最佳 " + bh.best_rank;
    return base;
  }

  async function loadScreenshots() {
    const shots = await readOptional("game_screenshots", {
      select: "id,game_name,url,sort_order", order: "sort_order.asc,id.asc", limit: 5000,
    });
    state.shots = {};
    for (const s of shots || []) {
      if (!state.shots[s.game_name]) state.shots[s.game_name] = [];
      state.shots[s.game_name].push(s);
    }
  }

  async function loadDetailIndex() {
    const detail = await window.GameDetail.loadIndex();
    state.detailIndex = detail.games || {};
  }

  async function loadAll() {
    const [games, profiles, base, latest] = await Promise.all([
      // 排序键必须**唯一**：sb.select 用 Range 分页（每页 1000），而
      // first_seen_at 精确到日、大量记录同值，仅按它排会得到不确定的顺序，
      // 跨页可能重复返回同一行（列表里出现两条同名记录）。补 name 兜底成全序。
      readOptional("games", { select: "name,first_seen_at,category,publisher_name", order: "first_seen_at.desc,name.asc", limit: 5000 }),
      readOptional("game_profiles", { limit: 5000 }),
      loadBaseGames(),
      fetch(assetUrl("data/latest.json"), { cache: "no-cache" }).then((r) => r.ok ? r.json() : null).catch(() => null),
    ]);
    const byName = new Map(Object.entries(base).map(([name, entry]) => [name, baseGame(name, entry)]));
    for (const game of games) {
      const fallback = byName.get(game.name);
      byName.set(game.name, preferRankFields(game, fallback));
    }
    state.boardMap = Object.fromEntries(Object.entries(base).map(([name, entry]) => [name, entry.board_history || {}]));
    // 从最新榜单快照构建: 名称 -> {榜单key: 当前排名}
    if (latest && latest.platforms) {
      const rm = {};
      for (const [platKey, plat] of Object.entries(latest.platforms)) {
        for (const b of (plat && plat.boards) || []) {
          const bKey = platKey + "/" + (b.label || "");
          for (const row of (b.rows) || []) {
            if (!row || !row.name) continue;
            if (typeof row.rank === "number" || typeof row.rank === "string") {
              rm[row.name] = rm[row.name] || {};
              rm[row.name][bKey] = row.rank;
            }
          }
        }
      }
      state.latestRankMap = rm;
    }
    state.games = [...byName.values()];
    state.profiles = {};
    state.profilesLoaded = profiles !== null;
    for (const p of profiles || []) state.profiles[p.game_name] = p;
    state.shots = {};
    buildList();
    state.fullListLoaded = true;
    // Thumbnails and detail badges are enhancements; don't hold first paint
    // on either of these secondary requests.
    loadScreenshots().then(buildList);
    loadDetailIndex().then(buildList).catch(() => {});
  }

  async function loadFastGame(name, storeContext) {
    showView(name, storeContext);
    $("gp-edit-btn").disabled = true;
    const shotsPromise = readOptional("game_screenshots", {
      raw: { game_name: `eq.${name}` },
      select: "id,game_name,url,sort_order",
      order: "sort_order.asc,id.asc",
      limit: 200,
    });
    const gamePromise = readOptional("games", {
      raw: { name: `eq.${name}` },
      select: "name,first_seen_at,category,publisher_name",
      limit: 1,
    });
    const profilesPromise = readOptional("game_profiles", {
      raw: { game_name: `eq.${name}` },
      select: "game_name,developer,gameplay_desc,tags,notes,value,favorite,abandon_reason,updated_at",
      limit: 1,
    });
    const basePromise = loadBaseGames().then((base) => {
      const entry = base[name];
      if (state.current !== name || state.fullListLoaded || !entry) return;
      state.games = [baseGame(name, entry)];
      state.boardMap[name] = entry.board_history || {};
      renderView(state.profiles[name] || { game_name: name }, false);
    });
    shotsPromise.then((shots) => {
      if (state.current !== name || state.fullListLoaded) return;
      state.shots[name] = shots;
      renderViewShots(name);
    });
    const [[game], profiles] = await Promise.all([gamePromise, profilesPromise, basePromise]);
    if (state.current !== name || state.fullListLoaded) return;
    if (game) {
      state.games = [preferRankFields(game, state.games.find(row => row.name === name))];
    }
    const profile = profiles?.[0];
    state.profilesLoaded = profiles !== null;
    if (profile) state.profiles[name] = profile;
    renderView(profile || { game_name: name }, false);
    $("gp-edit-btn").disabled = false;
  }

  function buildList() {
    const rows = [];
    for (const g of state.games) {
      const p = state.profiles[g.name];
      const sh = state.shots[g.name] || [];
      const bh = state.boardMap[g.name] || {};
      // 详情索引的键做了名称归一化（全角冒号/不换行空格），查表要走同一套规则
      const d = state.detailIndex[
        (window.GameDetail && window.GameDetail.normName)
          ? window.GameDetail.normName(g.name) : g.name] || null;
      const boardKeys = sortBoardKeys(Object.keys(bh));
      const sourceBoards = boardKeys.map((k) => ({ key: k, text: fmtBoard(k, g.name) }));
      rows.push({
        name: g.name,
        bhRaw: bh,  // board_history for first_rank sorting under the active board
        has: !!p,
        developer: (p && p.developer) || (g.publisher_name || ""),
        desc: (p && p.gameplay_desc) || "",
        tags: (p && p.tags) || [],
        updated: (p && p.updated_at) || "",
        value: (p && p.value) || "",
        favorite: !!(p && p.favorite),
        abandonReason: (p && p.abandon_reason) || "",
        abandoned: !p || !["high", "mid", "low"].includes(p.value),
        category: g.category || "",
        publisher: (g.publisher_name || (p && p.developer) || ""),
        joined: g.first_seen_at || "",
        sourceBoards: sourceBoards,
        platformKeys: boardKeys,
        firstShot: sh.length ? sh[0].url : "",
        shotCount: sh.length,
        // 详情页（第 1/2/3/4 分区）相关
        hasDetail: !!(d && d.collected),
        detailCost: (d && d.cost_level) || "",
        detailVerdict: (d && d.verdict) || "",
        detailEngine: (d && d.engine) || "",
        detailIcon: (d && d.icon) || "",
      });
    }
    // 按新加入顺序(first_seen_at 降序),无时间戳的排最后
    rows.sort((a, b) => {
      const ta = a.joined || "", tb = b.joined || "";
      if (ta && tb) return tb.localeCompare(ta);
      if (ta) return -1;
      if (tb) return 1;
      return a.name.localeCompare(b.name, "zh");
    });
    state.list = rows;
    renderList();
  }

  function renderPager(total, totalPages) {
    const pager = document.getElementById("gp-pager");
    const info = document.getElementById("gp-page-info");
    const prev = document.getElementById("gp-prev");
    const next = document.getElementById("gp-next");
    if (!pager) return;
    if (totalPages <= 1) { pager.style.display = "none"; return; }
    pager.style.display = "flex";
    const from = state.page * 20 + 1;
    const to = Math.min(total, (state.page + 1) * 20);
    info.textContent = `${from}-${to} / 共 ${total} 款 · 第 ${state.page + 1}/${totalPages} 页`;
    prev.disabled = state.page === 0;
    next.disabled = state.page >= totalPages - 1;
    const input = document.getElementById("gp-page-input");
    const go = document.getElementById("gp-page-go");
    if (input) { input.max = totalPages; input.value = state.page + 1; }
    if (go) go.disabled = totalPages <= 1;
  }

  function jumpToPage() {
    const input = document.getElementById("gp-page-input");
    if (!input) return;
    const totalPages = Math.max(1, Math.ceil(state.list.length / 20));
    let n = parseInt(input.value, 10);
    if (isNaN(n)) n = 1;
    n = Math.max(1, Math.min(totalPages, n));
    state.page = n - 1;
    renderList();
  }

  function setBoardTag(id, gameName) {
    const el = document.getElementById(id);
    if (!el) return;
    const bh = state.boardMap[gameName] || {};
    const keys = sortBoardKeys(Object.keys(bh));
    const text = keys.map((k) => fmtBoard(k, gameName)).join(" / ");
    el.textContent = text;
    el.style.display = text ? "" : "none";
  }

  function openLightbox(srcUrl) {
    const lb = document.getElementById("gp-lightbox");
    if (!lb || !srcUrl) return;
    const img = lb.querySelector("img");
    if (img) img.src = srcUrl;
    lb.style.display = "flex";
  }

  function renderBoardFilter() {
    const box = document.getElementById("gp-board-filter");
    if (!box) return;
    box.innerHTML = "";
    const boards = PLATFORM_BOARDS[state.platformFilter] || [];
    for (const b of boards) {
      const btn = document.createElement("button");
      btn.dataset.board = b;
      btn.textContent = b;
      if (state.boardFilter === b) btn.classList.add("active");
      btn.onclick = () => {
        state.boardFilter = b;
        state.page = 0;
        renderBoardFilter();
        renderList();
      };
      box.appendChild(btn);
    }
  }

  function renderList() {
    // 搜索前先做与详情索引同一套归一化（NFKC + 去不可见字符 + 合并空白）：
    // 榜单名与底库名常只差一个全角冒号「：」/「:」或不换行空格，
    // 用户按键盘敲的是半角，不归一就搜不到（例如「ゴシップハーバー」）。
    const normFn = (window.GameDetail && window.GameDetail.normName) || ((s) => s);
    const q = normFn($("gp-search").value.trim()).toLowerCase();
    let filtered = q
      ? state.list.filter((r) =>
          normFn(r.name).toLowerCase().includes(q) ||
          normFn(r.developer).toLowerCase().includes(q))
      : state.list;
    // Platform + board filter: product must have appeared on this exact board.
    const boardKey = state.platformFilter + "/" + state.boardFilter;
    filtered = filtered.filter((r) =>
      (r.platformKeys || []).includes(boardKey));
    if (state.favOnly) {
      filtered = filtered.filter((r) => r.favorite);
    }
    const f = state.filter;
    if (f === "high" || f === "mid" || f === "low") {
      filtered = filtered.filter((r) => r.value === f);
    } else if (f === "abandoned") {
      filtered = filtered.filter((r) => r.abandoned);
    }
    // Sort: newest first-seen date first; within each date group, order by
    // the 首上 (first_rank) of the ACTIVE board, ascending from 1.
    filtered.sort((a, b) => {
      const da = (a.joined || "").slice(0, 10), db = (b.joined || "").slice(0, 10);
      if (da !== db) return da < db ? 1 : -1;
      const bhA = (a.bhRaw && a.bhRaw[boardKey]) || {};
      const bhB = (b.bhRaw && b.bhRaw[boardKey]) || {};
      const ra = bhA.first_rank, rb = bhB.first_rank;
      if (ra !== rb) return (ra ?? Infinity) - (rb ?? Infinity);
      return a.name.localeCompare(b.name, "zh");
    });
    const tbody = document.querySelector("#tbl-game tbody");
    tbody.innerHTML = "";
    $("gp-count").textContent = `共 ${filtered.length} 份档案`;
    if (!filtered.length) {
      tbody.innerHTML = '<tr><td colspan="5" class="muted" style="text-align:center;padding:24px">没有匹配的产品。</td></tr>';
      renderMore(0);
      return;
    }
    // Date-group totals across the whole filtered set (publishers-style
    // grouping: newest → oldest with a date divider row per day).
    const groupTotals = new Map();
    for (const r of filtered) {
      const d = (r.joined || "").slice(0, 10) || "未知日期";
      groupTotals.set(d, (groupTotals.get(d) || 0) + 1);
    }
    const PAGE_SIZE = 50;
    const shown = filtered.slice(0, (state.page + 1) * PAGE_SIZE);
    let currentDate = null;
    for (const r of shown) {
      const seenDate = (r.joined || "").slice(0, 10) || "未知日期";
      if (seenDate !== currentDate) {
        currentDate = seenDate;
        const tr = document.createElement("tr");
        tr.className = "date-divider";
        tr.innerHTML = `<td colspan="5">
          <span class="dv-date">${esc(seenDate)}</span>
          <span class="dv-count">首次上榜 ${groupTotals.get(seenDate) || 0} 款</span>
        </td>`;
        tbody.appendChild(tr);
      }
      const effVal = r.value || "abandoned";
      const tr = document.createElement("tr");
      tr.className = "gp-tr v-" + esc(effVal);
      const thumbSrc = r.firstShot || r.detailIcon;
      const thumb = thumbSrc
        ? `<img class="gp-thumb" src="${esc(thumbSrc)}" loading="lazy" decoding="async" width="72" height="72" alt="" referrerpolicy="no-referrer" />`
        : `<span class="gp-thumb empty">无图</span>`;
      // 榜单来源只显示当前筛选榜单（如微信·畅销榜），不带其他榜单
      const srcBoards = (r.sourceBoards || [])
        .filter((b) => b.key === boardKey)
        .map((b) => `<span class="badge-src">${esc(b.text)}</span>`).join("");
      const actBtns = `
            <button class="gp-card-fav${r.favorite ? " on" : ""}" data-name="${esc(r.name)}" title="收藏">❤</button>
            <button class="gp-card-val ${esc(effVal)}" data-name="${esc(r.name)}" title="设置玩法状态">${effVal === "high" ? "高价值" : effVal === "mid" ? "中价值" : effVal === "low" ? "低价值" : "放弃"}</button>`;
      // 详情徽章：有档案的显示「成本档位 + 复刻结论」，一眼可筛
      const detailBadge = r.hasDetail
        ? `<span class="badge-detail" title="已有详情页">详情${r.detailCost ? "·成本" + esc(r.detailCost) : ""}${r.detailVerdict ? "·" + esc(r.detailVerdict) : ""}</span>`
        : "";
      tr.innerHTML = `
        <td class="gp-td-name">${thumb}<span class="gp-name">${esc(r.name)}</span>${r.has || r.hasDetail ? "" : '<span class="badge-has">基础档案</span>'}${detailBadge}
          ${r.abandonReason ? `<div class="gp-card-abandon-reason">放弃理由：${esc(r.abandonReason)}</div>` : ""}</td>
        <td class="gp-td-dev">${esc(r.developer)}</td>
        <td class="gp-td-src">${srcBoards}</td>
        <td class="gp-td-val">${actBtns}</td>
        <td class="gp-td-view"><button type="button" class="gp-view-btn" data-name="${esc(r.name)}" title="查看/编辑档案">档案</button></td>`;
      tr.addEventListener("click", (e) => {
        if (e.target.closest(".gp-card-fav") || e.target.closest(".gp-card-val")) return;
        showView(r.name, { platform: state.platformFilter, board: state.boardFilter });
      });
      const viewBtn = tr.querySelector(".gp-view-btn");
      if (viewBtn) viewBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        showView(r.name, { platform: state.platformFilter, board: state.boardFilter });
      });
      tbody.appendChild(tr);
    }
    renderMore(filtered.length);
    // 表格行上的收藏按钮: 局部切换心形状态
    tbody.querySelectorAll(".gp-card-fav").forEach((btn) => {
      btn.addEventListener("click", async (e) => {
        e.stopPropagation();
        if (!canEditProfile()) return;
        const name = btn.dataset.name;
        const cur = state.profiles[name] || { game_name: name, developer: "", gameplay_desc: "", tags: [], notes: "", value: "", favorite: false };
        const next = !cur.favorite;
        btn.disabled = true;
        try {
          const resp = await fetch(FN_URL, {
            method: "POST",
            headers: {
              "Content-Type": "application/json",

              ...authHeaders(),
            },
            body: JSON.stringify({ ...cur, favorite: next }),
          });
          const data = await resp.json().catch(() => ({}));
          if (!resp.ok || !data.ok) throw new Error(data.error || ("HTTP " + resp.status));
          cur.favorite = next;
          state.profiles[name] = cur;
          const row = state.list.find((x) => x.name === name);
          if (row) row.favorite = next;
          btn.disabled = false;
          btn.classList.toggle("on", next);
          if (state.favOnly) renderList();
        } catch (err) {
          alert("操作失败：" + err.message);
          btn.disabled = false;
        }
      });
    });
    // 表格行上的价值按钮: 点击弹出高/中/低选项,局部更新
    tbody.querySelectorAll(".gp-card-val").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        if (!canEditProfile()) return;
        openValueMenu(btn);
      });
    });
    function openValueMenu(btn) {
      const old = document.querySelector(".gp-val-menu");
      if (old) old.remove();
      const name = btn.dataset.name;
      const cur = state.profiles[name] || { game_name: name, developer: "", gameplay_desc: "", tags: [], notes: "", value: "" };
      const r = btn.getBoundingClientRect();
      const menu = document.createElement("div");
      menu.className = "gp-val-menu";
      menu.style.top = (r.bottom + 4) + "px";
      menu.style.left = Math.max(4, r.left) + "px";
      const opts = [
        ["high", "高价值"],
        ["mid", "中价值"],
        ["low", "低价值"],
        ["abandoned", "放弃"],
      ];
      for (const [v, label] of opts) {
        const item = document.createElement("button");
        item.type = "button";
        item.className = "gp-val-menu-item" + ((cur.value || "abandoned") === v ? " active" : "");
        item.textContent = label;
        item.addEventListener("click", (ev) => {
          ev.stopPropagation();
          menu.remove();
          setCardValue(btn, name, v);
        });
        menu.appendChild(item);
      }
      document.body.appendChild(menu);
      const close = (ev) => {
        if (!menu.contains(ev.target)) menu.remove();
        document.removeEventListener("click", close);
      };
      setTimeout(() => document.addEventListener("click", close), 0);
    }

    async function setCardValue(btn, name, value) {
      let reason = (state.profiles[name] || {}).abandon_reason || "";
      if (value === "abandoned") {
        const inp = window.prompt("放弃理由（可选）：", reason || "");
        if (inp !== null) reason = inp.trim();
      }
      const cur = state.profiles[name] || { game_name: name, developer: "", gameplay_desc: "", tags: [], notes: "", value: "" };
      btn.disabled = true;
      try {
        const resp = await fetch(FN_URL, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            ...authHeaders(),
          },
          body: JSON.stringify({ ...cur, value, abandon_reason: reason }),
        });
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok || !data.ok) throw new Error(data.error || ("HTTP " + resp.status));
        cur.value = value;
        state.profiles[name] = cur;
        const row = state.list.find((x) => x.name === name);
        if (row) { row.value = value; row.abandonReason = reason; }
        btn.disabled = false;
        btn.className = "gp-card-val " + value;
        btn.textContent = value === "high" ? "高价值" : value === "mid" ? "中价值" : value === "low" ? "低价值" : "放弃";
        // 放弃理由同步到所在行的文案区
        const rowEl = btn.closest("tr");
        if (rowEl) {
          rowEl.className = "gp-tr v-" + (value || "abandoned");
          const reasonEl = rowEl.querySelector(".gp-card-abandon-reason");
          if (reasonEl) reasonEl.remove();
          if (reason) {
            const el = document.createElement("div");
            el.className = "gp-card-abandon-reason";
            el.textContent = reason;
            const nameCell = rowEl.querySelector(".gp-td-name");
            if (nameCell) nameCell.appendChild(el);
          }
        }
        if (state.filter) renderList();
      } catch (err) {
        alert("操作失败：" + err.message);
        btn.disabled = false;
      }
    }
  }
  // Segmented loading: show a "加载更多" button until everything is on screen.
  function renderMore(total) {
    const row = document.getElementById("gp-more-row");
    const btn = document.getElementById("gp-more");
    if (!row || !btn) return;
    const shownCount = (state.page + 1) * 50;
    if (shownCount < total) {
      row.style.display = "";
      btn.textContent = `加载更多（还有 ${total - shownCount} 条）`;
      btn.onclick = () => { state.page++; renderList(); };
    } else {
      row.style.display = "none";
    }
  }
  document.getElementById("gp-page-go").addEventListener("click", jumpToPage);
  document.getElementById("gp-new").addEventListener("click", startNewGame);
  const filterSel = document.getElementById("gp-filter");
  if (filterSel) filterSel.addEventListener("change", () => {
    state.filter = filterSel.value;
    state.favOnly = false;
    const favBtn = document.getElementById("gp-fav");
    if (favBtn) favBtn.classList.remove("active");
    state.page = 0;
    renderList();
  });
  const favBtn = document.getElementById("gp-fav");
  if (favBtn) favBtn.addEventListener("click", () => {
    state.favOnly = !state.favOnly;
    favBtn.classList.toggle("active", state.favOnly);
    state.page = 0;
    renderList();
  });
  const platFilter = document.getElementById("gp-platform-filter");
  if (platFilter) platFilter.addEventListener("click", (e) => {
    const b = e.target.closest("button[data-plat]");
    if (!b) return;
    platFilter.querySelectorAll("button").forEach(x =>
      x.classList.toggle("active", x === b));
    state.platformFilter = b.dataset.plat;
    state.boardFilter = (PLATFORM_BOARDS[state.platformFilter] || [])[0] || "";
    state.page = 0;
    renderBoardFilter();
    renderList();
  });
  document.getElementById("gp-page-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter") jumpToPage();
  });

  function showView(name, storeContext) {
    state.current = name;
    state.profileRank = null;
    state.isNew = false;
    $("gp-toolbar").style.display = "none";
    $("gp-table-wrap").style.display = "none";
    $("gp-form").style.display = "block";
    $("gp-edit-btn").disabled = false;
    renderView(state.profiles[name] || { game_name: name });
    const detailKey = window.GameDetail?.normName ? window.GameDetail.normName(name) : name;
    renderProfileIcon(name, state.detailIndex[detailKey]?.icon);
    renderMiniGameIcon(name, storeContext || { platform: state.platformFilter, board: state.boardFilter });
    renderStoreApps(name, storeContext);
    $("gp-status").textContent = "";
  }

  function startNewGame() {
    if (!canEditProfile()) return;
    const name = prompt("输入新游戏名称:");
    if (!name || !name.trim()) return;
    state.current = name.trim();
    state.isNew = true;
    if (!state.profiles[state.current]) {
      state.profiles[state.current] = { game_name: state.current, developer: "", gameplay_desc: "", tags: [], notes: "", abandoned: false };
    }
    $("gp-toolbar").style.display = "none";
    $("gp-table-wrap").style.display = "none";
    $("gp-form").style.display = "block";
    document.getElementById("gp-view").style.display = "none";
    document.getElementById("gp-edit-mode").style.display = "none";
    enterEditMode();
    $("gp-status").textContent = "新建游戏，填写完成后保存，上榜日期为今天。";
  }

  function renderView(p, loadDetail = true) {
    const game = state.games.find((row) => row.name === p.game_name);
    const rank = state.profileRank?.name === p.game_name ? state.profileRank : null;
    const history = state.boardMap[p.game_name] || {};
    const lastSeen = Object.values(history).map((entry) => entry.last_seen).filter(Boolean).sort().pop();
    const boardHistory = sortBoardKeys(Object.keys(history)).map((key) => {
      const entry = history[key];
      return [
        fmtBoard(key),
        entry.best_rank == null ? "" : `最高第 ${entry.best_rank} 名`,
        entry.appearances ? `上榜 ${entry.appearances} 天` : "",
      ].filter(Boolean).join(" · ");
    }).join("\n");
    const fields = [
      ["开发商", p.developer],
      ["发行商", (rank ? rank.publisher : game?.publisher_name) || "未知"],
      ["分类", rank ? rank.category : game?.category],
      ["首次上榜", game?.first_seen_at?.slice(0, 10)],
      ["最近上榜", lastSeen],
      ["上榜记录", boardHistory],
      ["玩法", p.gameplay_desc, true],
      ["标签", (p.tags || []).join(", ")],
      ["备注", p.notes],
      ["放弃理由", p.abandon_reason],
      ["更新时间", (p.updated_at || "").slice(0, 16).replace("T", " ")],
    ];
    const view = document.getElementById("gp-view");
    view.style.display = "block";
    view.querySelector(".gp-view-name").textContent = p.game_name;
    setBoardTag("gp-view-board", p.game_name);
    const dl = view.querySelector(".gp-view-fields");
    dl.innerHTML = "";
    for (const [k, v, isMd] of fields) {
      if (!v) continue;
      const dt = document.createElement("dt");
      dt.textContent = k;
      const dd = document.createElement("dd");
      if (isMd) dd.innerHTML = renderMarkdown(v);
      else {
        dd.textContent = v;
        dd.style.whiteSpace = "pre-wrap";
      }
      dl.appendChild(dt);
      dl.appendChild(dd);
    }
    renderViewShots(p.game_name);
    if (loadDetail) renderDetail(p.game_name);
    document.getElementById("gp-edit-mode").style.display = "none";
    const editBtn = document.getElementById("gp-edit-btn");
    if (editBtn) editBtn.style.display = "";
  }

  function renderProfileIcon(name, url) {
    const header = $("gp-view").querySelector(".gp-view-name-wrap");
    if (!header) return;
    let box = header.querySelector(".gp-profile-icon");
    if (!box) {
      box = document.createElement("div");
      box.className = "gp-profile-icon";
      header.append(box);
    }
    const placeholder = (name || "?").slice(0, 1);
    box.textContent = placeholder;
    if (!/^https:\/\//.test(url || "")) return;
    const img = document.createElement("img");
    img.alt = `${name} 游戏图标`;
    img.referrerPolicy = "no-referrer";
    img.addEventListener("error", () => { box.textContent = placeholder; }, { once: true });
    img.src = url;
    box.replaceChildren(img);
  }

  async function renderStoreApps(name, { platform, board, storeKey, release } = {}) {
    descriptionObserver?.disconnect();
    $("gp-video-player")?.close();
    const detail = $("gp-detail");
    $("gp-store-apps")?.remove();
    if (platform && !["ios", "android", "taptap"].includes(platform)) return;

    const section = document.createElement("section");
    section.id = "gp-store-apps";
    section.hidden = true;
    detail.before(section);
    storeAppsRequest ||= fetch(assetUrl("data/base/store-apps.json"), { cache: "no-cache" })
      .then((response) => response.ok ? response.json() : {})
      .catch(() => ({}));
    const releaseRequest = /^\d{4}-\d{2}-\d{2}$/.test(release || "")
      ? fetch(assetUrl(`data/releases/${release}.json`), { cache: "no-cache" })
        .then(response => response.ok ? response.json() : null).catch(() => null) : null;
    const [records, base, releaseSnapshot] = await Promise.all([storeAppsRequest, platform ? null : loadBaseGames(), releaseRequest]);
    if (!section.isConnected) return;
    const regions = { "美区免费榜": "us", "美区每日新上架": "us", "国区免费榜": "cn", "日区免费榜": "jp" };
    const country = regions[board];
    const history = base?.[name]?.board_history || {};
    const candidate = releaseSnapshot?.apps?.find(app => app.platform === platform
      && storeKey === (platform === "ios" ? `ios:us:${app.id}` : `android:${app.id}`));
    const releaseApp = candidate ? { ...candidate, country: "us", developer: candidate.publisher } : null;
    const knownApp = storeKey && Object.hasOwn(records, storeKey) ? records[storeKey] : null;
    const selectedApp = releaseApp ? { ...knownApp, ...Object.fromEntries(Object.entries(releaseApp).filter(([key, value]) =>
      value !== null && value !== undefined && value !== "" && (!Array.isArray(value) || value.length || key === "media_missing"))) } : knownApp;
    // A historical release snapshot must not replace more recent store metrics.
    if (releaseApp && Date.parse(knownApp?.metrics_checked_at) > (Date.parse(releaseApp.metrics_checked_at) || 0)) {
      for (const key of ["rating", "rating_count", "review_count", "installs", "metrics_checked_at"]) {
        if (knownApp[key] !== undefined) selectedApp[key] = knownApp[key];
      }
    }
    const apps = storeKey
      ? [selectedApp].filter(Boolean)
      : Object.values(records).filter((app) => {
        if (app.name !== name && !app.names?.includes(name)) return false;
        if (platform) return app.platform === platform && (platform !== "ios" || app.country === country);
        return Object.keys(history).some((key) => {
          const [source, label] = key.split("/");
          return source === app.platform && (source !== "ios" || regions[label] === app.country);
        });
      });
    if (platform) {
      // A platform-specific profile must not inherit another store's historical publisher.
      const app = apps.length === 1 && apps[0].platform === platform ? apps[0] : null;
      state.profileRank = { name, publisher: app ? app.publisher || app.developer || "" : "", category: app?.category || "" };
      renderView(state.profiles[name] || { game_name: name }, false);
      if (!app) renderProfileIcon(name);
    }
    for (const app of apps) {
      if (platform && app.platform !== platform) continue;
      const ios = app.platform === "ios";
      const tap = app.platform === "taptap";
      const storeName = ios ? "App Store" : tap ? "TapTap" : "Google Play";
      if (app.icon && apps.length === 1) renderProfileIcon(name, app.icon);
      const count = value => Number.isSafeInteger(value) && value >= 0 ? value.toLocaleString("en-US") : null;
      const heading = document.createElement("h3");
      heading.textContent = ios ? `${storeName} · ${app.country.toUpperCase()}` : storeName;
      const fields = [
        ["应用名称", app.name],
        ["开发者", app.developer || app.publisher],
        ["商店分类", app.category],
        [ios || tap ? "App ID" : "包名", app.id],
        ["版本", app.version],
        ["评分", Number.isFinite(app.rating) && app.rating >= 0 && app.rating <= 5 ? `${Number(app.rating.toFixed(2))} / 5` : null],
        ["评分人数", count(app.rating_count)],
        ["评论数", count(app.review_count)],
        ["总下载量", app.installs ? `${app.installs}（商店累计安装档位，全球累计，不代表美区下载量）` : null],
        ["指标获取日期", app.metrics_checked_at?.slice(0, 10)],
        ["适用设备", (app.supported_platforms || []).join("、")],
        ["应用简介", app.description],
      ];
      const list = document.createElement("dl");
      list.className = "gp-view-fields";
      for (const [label, value] of fields) {
        if (!value) continue;
        const term = document.createElement("dt");
        term.textContent = label;
        const definition = document.createElement("dd");
        definition.textContent = value;
        definition.style.whiteSpace = "pre-wrap";
        if (label === "应用简介") renderDescription(definition, value);
        list.append(term, definition);
      }
      section.append(heading, list);
      if (release && (!app.description || !app.screenshots?.length || !app.version)) {
        const missing = [!app.description && "游戏介绍", !app.version && "版本", !app.screenshots?.length && "官方截图"].filter(Boolean);
        const note = document.createElement("p");
        note.className = "muted";
        note.textContent = app.media_checked_at
          ? `本次官方查询未返回完整资料：${missing.join("、")}。已有资料保留，后续采集会重试。`
          : `基础资料待获取：${missing.join("、")}。已进入采集队列，与 AI 深度补档分开处理。`;
        section.append(note);
      }
      const storeOrigin = ios ? "https://apps.apple.com/" : tap ? "https://www.taptap.cn/" : "https://play.google.com/";
      if (app.url?.startsWith(storeOrigin)) {
        const link = document.createElement("a");
        link.href = app.url;
        link.textContent = "查看商店详情";
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        section.append(link);
      }
      renderStoreScreenshots(section, app.screenshots || [], storeName, app.videos || [], app.url);
      if (app.video_error) {
        const note = document.createElement("p");
        note.className = "muted";
        note.textContent = "官方视频本次获取失败，已有视频保留，后续采集重试。";
        section.append(note);
      }
      section.hidden = false;
    }
  }

  function renderDescription(parent, text) {
    parent.textContent = "";
    const content = document.createElement("div");
    content.className = "gp-app-description";
    content.id = `gp-app-description-${++descriptionId}`;
    content.textContent = text;
    const toggle = document.createElement("button");
    toggle.type = "button";
    toggle.className = "gp-description-toggle";
    toggle.textContent = "展开简介";
    toggle.hidden = true;
    toggle.setAttribute("aria-controls", content.id);
    toggle.setAttribute("aria-expanded", "false");
    toggle.onclick = () => {
      const expanded = content.classList.toggle("is-expanded");
      toggle.textContent = expanded ? "收起简介" : "展开简介";
      toggle.setAttribute("aria-expanded", String(expanded));
    };
    parent.append(content, toggle);
    descriptionObserver ||= new ResizeObserver(entries => {
      for (const { target } of entries) {
        target.nextElementSibling.hidden = !target.classList.contains("is-expanded") && target.scrollHeight <= target.clientHeight + 1;
      }
    });
    descriptionObserver.observe(content);
  }

  function validPreview(video) {
    try {
      const url = new URL(video.url);
      if (url.protocol !== "https:" || url.username || url.password || (url.port && url.port !== "443")) return false;
      if (video.type === "youtube") return ["www.youtube.com", "www.youtube-nocookie.com"].includes(url.hostname) && /^\/embed\/[\w-]{11}$/.test(url.pathname);
      return ["mp4", "hls"].includes(video.type) && ["play-games.googleusercontent.com", "apptrailers.itunes.apple.com", "video.itunes.apple.com"].includes(url.hostname)
        && url.pathname.endsWith(video.type === "hls" ? ".m3u8" : ".mp4");
    } catch { return false; }
  }

  function loadHls() {
    return hlsRequest ||= new Promise((resolve, reject) => {
      const script = document.createElement("script");
      script.src = "vendor/hls.light.min.js?v=1.7.4";
      script.onload = () => resolve(window.Hls);
      script.onerror = () => { hlsRequest = null; script.remove(); reject(new Error("视频播放器加载失败")); };
      document.head.append(script);
    });
  }

  function openVideoPreview(preview, storeUrl) {
    if (!validPreview(preview)) return;
    $("gp-video-player")?.close();
    const dialog = document.createElement("dialog");
    dialog.id = "gp-video-player";
    dialog.setAttribute("aria-label", "官方视频预览");
    const close = document.createElement("button");
    close.type = "button";
    close.textContent = "关闭视频";
    close.onclick = () => dialog.close();
    const status = document.createElement("p");
    status.setAttribute("role", "status");
    dialog.append(close);
    const media = document.createElement(preview.type === "youtube" ? "iframe" : "video");
    let hls;
    const failed = () => { status.textContent = "视频暂时无法播放，可尝试备用视频或查看官方商店。"; };
    if (preview.type === "youtube") {
      media.title = "官方视频预览";
      media.allow = "autoplay; encrypted-media; fullscreen; picture-in-picture";
      media.allowFullscreen = true;
      media.referrerPolicy = "strict-origin-when-cross-origin";
      media.setAttribute("sandbox", "allow-scripts allow-same-origin allow-presentation");
      media.src = preview.url;
    } else {
      media.controls = true;
      media.playsInline = true;
      media.preload = "none";
      media.addEventListener("error", failed);
    }
    dialog.append(media, status);
    if (preview.fallback_url && validPreview({ url: preview.fallback_url, type: "youtube" })) {
      const fallback = document.createElement("button");
      fallback.type = "button";
      fallback.textContent = "尝试备用视频";
      fallback.onclick = () => { dialog.close(); openVideoPreview({ url: preview.fallback_url, type: "youtube" }, storeUrl); };
      dialog.append(fallback);
    }
    if (/^https:\/\/(?:apps\.apple\.com|play\.google\.com)\//.test(storeUrl || "")) {
      const link = document.createElement("a");
      link.href = storeUrl;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.textContent = "查看官方商店";
      dialog.append(link);
    }
    dialog.addEventListener("click", event => { if (event.target === dialog) dialog.close(); });
    dialog.addEventListener("close", () => {
      hls?.destroy();
      if (media.tagName === "VIDEO") { media.pause(); media.removeAttribute("src"); media.load(); }
      else media.src = "about:blank";
      dialog.remove();
    }, { once: true });
    document.body.append(dialog);
    dialog.showModal();
    if (preview.type === "youtube") return;
    const play = () => media.play().catch(() => {});
    if (preview.type === "mp4" || media.canPlayType("application/vnd.apple.mpegurl")) {
      media.src = preview.url;
      play();
    } else {
      loadHls().then(Hls => {
        if (!dialog.isConnected) return;
        if (!Hls?.isSupported()) { failed(); return; }
        hls = new Hls();
        hls.on(Hls.Events.ERROR, (_, data) => { if (data.fatal) { hls.destroy(); failed(); } });
        hls.on(Hls.Events.MANIFEST_PARSED, play);
        hls.loadSource(preview.url);
        hls.attachMedia(media);
      }).catch(failed);
    }
  }

  function renderStoreScreenshots(section, urls, storeName, videos = [], storeUrl) {
    const gallery = document.createElement("div");
    gallery.className = "gp-store-shots";
    const heading = document.createElement("h4");
    heading.textContent = `${storeName} 官方截图`;
    for (const preview of videos.filter(validPreview)) {
      heading.textContent = `${storeName} 官方截图与视频`;
      const button = document.createElement("button");
      button.type = "button";
      button.className = "gp-video-preview";
      button.setAttribute("aria-label", "播放官方视频预览");
      if (/^https:\/\/(?:[\w.-]+\.mzstatic\.com|play-lh\.googleusercontent\.com)\//.test(preview.poster || "")) {
        const poster = document.createElement("img");
        poster.src = preview.poster;
        poster.alt = "";
        poster.loading = "lazy";
        poster.addEventListener("error", () => poster.remove(), { once: true });
        button.append(poster);
      }
      const label = document.createElement("span");
      label.textContent = "▶ 官方视频预览";
      button.append(label);
      button.onclick = () => openVideoPreview(preview, storeUrl);
      gallery.append(button);
    }
    for (const url of urls) {
      if (!/^https:\/\/(?:[\w.-]+\.(?:mzstatic\.com|tapimg\.com)|play-lh\.googleusercontent\.com)\//.test(url)) continue;
      const link = document.createElement("a");
      link.href = url;
      const img = document.createElement("img");
      img.alt = `${storeName} 截图 ${gallery.childElementCount + 1}`;
      img.loading = "lazy";
      img.decoding = "async";
      img.referrerPolicy = "no-referrer";
      img.addEventListener("error", () => {
        link.remove();
        if (!gallery.childElementCount) {
          gallery.remove();
          heading.remove();
          const note = document.createElement("p");
          note.className = "muted";
          note.textContent = `${storeName} 官方截图暂时加载失败，可通过商店详情核对。`;
          section.append(note);
        }
      });
      link.addEventListener("click", (event) => {
        if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
        event.preventDefault();
        openLightbox(url);
      });
      img.src = url;
      link.append(img);
      gallery.append(link);
    }
    if (gallery.childElementCount) section.append(heading, gallery);
  }

  function renderMarkdown(src) {
    if (!src) return "";
    const lines = String(src).replace(/\r\n?/g, "\n").split("\n");
    const out = [];
    let i = 0, inCode = false, codeBuf = [], inUl = false, inOl = false, inQuote = false;
    const closeUl = () => { if (inUl) { out.push("</ul>"); inUl = false; } };
    const closeOl = () => { if (inOl) { out.push("</ol>"); inOl = false; } };
    const closeQuote = () => { if (inQuote) { out.push("</blockquote>"); inQuote = false; } };
    while (i < lines.length) {
      const raw = lines[i];
      const line = raw.trim();
      if (line.startsWith("```")) {
        if (!inCode) { closeUl(); closeOl(); closeQuote(); inCode = true; codeBuf = []; }
        else { out.push("<pre><code>" + esc(codeBuf.join("\n")) + "</code></pre>"); inCode = false; }
        i++; continue;
      }
      if (inCode) { codeBuf.push(raw); i++; continue; }
      if (!line) { closeUl(); closeOl(); closeQuote(); out.push(""); i++; continue; }
      const hm = line.match(/^(#{1,6})\s+(.*)$/);
      if (hm) { closeUl(); closeOl(); closeQuote(); out.push(`<h${hm[1].length}>${inlineMd(hm[2])}</h${hm[1].length}>`); i++; continue; }
      if (/^(-{3,}|\*{3,}|_{3,})$/.test(line)) { closeUl(); closeOl(); closeQuote(); out.push("<hr>"); i++; continue; }
      if (line.startsWith(">")) {
        closeUl(); closeOl();
        if (!inQuote) { out.push("<blockquote>"); inQuote = true; }
        out.push("<p>" + inlineMd(line.replace(/^>\s?/, "")) + "</p>");
        i++; continue;
      }
      const ulm = line.match(/^[-*+]\s+(.*)$/);
      if (ulm) { closeOl(); closeQuote(); if (!inUl) { out.push("<ul>"); inUl = true; } out.push("<li>" + inlineMd(ulm[1]) + "</li>"); i++; continue; }
      const olm = line.match(/^\d+[.)]\s+(.*)$/);
      if (olm) { closeUl(); closeQuote(); if (!inOl) { out.push("<ol>"); inOl = true; } out.push("<li>" + inlineMd(olm[1]) + "</li>"); i++; continue; }
      closeUl(); closeOl(); closeQuote();
      out.push("<p>" + inlineMd(line) + "</p>");
      i++;
    }
    if (inCode) out.push("<pre><code>" + esc(codeBuf.join("\n")) + "</code></pre>");
    closeUl(); closeOl(); closeQuote();
    return out.join("\n");
  }

  function inlineMd(s) {
    return esc(s)
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/\*([^*\n]+)\*/g, "<em>$1</em>")
      .replace(/!\[([^\]]*)\]\(([^)]+)\)/g, (m, alt, url) => {
        const u = esc(url).trim();
        return /^(https?:|\/)/i.test(u) ? `<img src="${u}" alt="${alt}" loading="lazy" />` : "";
      })
      .replace(/\[([^\]]+)\]\(([^)]+)\)/g, (m, text, url) => {
        const u = esc(url).trim();
        const safe = /^(https?:|mailto:|#|\/)/i.test(u) ? u : "#";
        return `<a href="${safe}" target="_blank" rel="noopener">${text}</a>`;
      });
  }

  function initMdEditor() {
    const toolbar = document.getElementById("gp-md-toolbar");
    const ta = document.getElementById("gp-desc");
    const pv = document.getElementById("gp-desc-preview");
    if (!toolbar || !ta) return;

    function updatePreview() { pv.innerHTML = renderMarkdown(ta.value); }

    toolbar.addEventListener("click", (e) => {
      const btn = e.target.closest("button");
      if (!btn) return;
      e.preventDefault();
      const start = ta.selectionStart, end = ta.selectionEnd;
      const sel = ta.value.slice(start, end);
      let insert, selStart, selEnd;
      if (btn.dataset.wrap) {
        insert = btn.dataset.wrap + sel + btn.dataset.wrap;
        selStart = start + btn.dataset.wrap.length;
        selEnd = end + btn.dataset.wrap.length;
      } else if (btn.dataset.mdBlock) {
        insert = btn.dataset.mdBlock + "\n" + sel + "\n" + btn.dataset.mdBlock;
        selStart = start + btn.dataset.mdBlock.length + 1;
        selEnd = selStart + sel.length;
      } else {
        insert = btn.dataset.md || "";
        selStart = start + insert.length;
        selEnd = selStart;
      }
      ta.setRangeText(insert, start, end, "end");
      ta.focus();
      ta.setSelectionRange(selStart, selEnd);
      ta.dispatchEvent(new Event("input", { bubbles: true }));
    });

    const tabs = document.querySelectorAll("#gp-md-toolbar .md-tab");
    tabs.forEach((t) => t.addEventListener("click", () => switchMdMode(t.dataset.mode)));
    ta.addEventListener("input", updatePreview);
  }

  function switchMdMode(mode) {
    const ta = document.getElementById("gp-desc");
    const pv = document.getElementById("gp-desc-preview");
    document.querySelectorAll("#gp-md-toolbar .md-tab").forEach((t) => t.classList.toggle("active", t.dataset.mode === mode));
    if (mode === "preview") {
      pv.innerHTML = renderMarkdown(ta.value);
      ta.style.display = "none";
      pv.style.display = "block";
    } else {
      ta.style.display = "";
      pv.style.display = "none";
    }
  }

  // 渲染详情页四大分区（技术实现 / 玩法爽点创新点 / 官方截图 / 复刻建议）。
  // 数据来自 data/detail/，由 scripts/detail/ 生成；无档案时容器自动隐藏。
  function renderDetail(name) {
    const box = document.getElementById("gp-detail");
    if (!box) return;
    if (window.GameDetail) { window.GameDetail.render(name, box); return; }
    box.innerHTML = "";
    box.style.display = "none";
  }

  function renderViewShots(name) {
    const box = document.getElementById("gp-view-shots");
    box.innerHTML = "";
    const sh = state.shots[name] || [];
    if (!sh.length) { box.innerHTML = '<span class="gp-hint">暂无截图</span>'; return; }
    for (const s of sh) {
      const img = document.createElement("img");
      img.className = "gp-view-shot";
      img.src = s.url; img.alt = ""; img.loading = "lazy"; img.decoding = "async";
      img.addEventListener("click", () => openLightbox(s.url));
      box.appendChild(img);
    }
  }

  function enterEditMode() {
    if (!isEditor()) { backToList(); return; }
    const p = state.profiles[state.current] || { game_name: state.current, developer: "", gameplay_desc: "", tags: [], notes: "" };
    document.getElementById("gp-view").style.display = "none";
    const edit = document.getElementById("gp-edit-mode");
    const editTitle = document.getElementById("gp-edit-title");
    if (editTitle) editTitle.textContent = state.current;
    setBoardTag("gp-edit-board", state.current);
    edit.style.display = "block";
    const nameBox = document.getElementById("gp-edit-name");
    if (nameBox) {
      nameBox.value = state.current;
      nameBox.style.display = state.isNew ? "" : "none";
    }
    const g = state.games.find((x) => x.name === state.current);
    $("gp-developer").value = p.developer || (g && g.publisher_name) || "";
    $("gp-desc").value = p.gameplay_desc || "";
    $("gp-tags").value = (p.tags || []).join(", ");
    $("gp-notes").value = p.notes || "";
    const reasonBox = $("gp-abandon-reason");
    const reasonLabel = document.getElementById("gp-abandon-reason-label");
    if (reasonBox) reasonBox.value = p.abandon_reason || "";
    const valueRadios = document.querySelectorAll('input[name="gp-value"]');
    const gv = p.value || "abandoned";
    valueRadios.forEach((r) => { r.checked = r.value === gv; });
    if (reasonLabel && reasonBox) {
      const show = gv === "abandoned";
      reasonLabel.style.display = show ? "" : "none";
      reasonBox.style.display = show ? "" : "none";
    }
    renderShots();
  }

  async function deleteShot(s) {
    if (!confirm("确定删除这张截图吗？此操作不可撤销。")) return;
    $("gp-status").textContent = "正在删除截图…";
    try {
      const resp = await fetch(FN_URL, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",

          ...authHeaders(),
        },
        body: JSON.stringify({ action: "delete_shot", url: s.url }),
      });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok || !data.ok) throw new Error(data.error || ("HTTP " + resp.status));
      const sh = state.shots[state.current] || [];
      state.shots[state.current] = sh.filter((x) => x.id !== s.id);
      renderShots();
      $("gp-status").textContent = "截图已删除。";
    } catch (err) {
      $("gp-status").textContent = "删除失败：" + err.message;
    }
  }

  function renderShots() {
    const box = $("gp-shots");
    box.innerHTML = "";
    const sh = state.shots[state.current] || [];
    if (!sh.length) {
      box.innerHTML = '<span class="gp-hint">暂无截图</span>';
      return;
    }
    for (const s of sh) {
      const d = document.createElement("div");
      d.className = "gp-shot";
      d.innerHTML = `<img src="${esc(s.url)}" alt="" loading="lazy" /><button class="del" data-id="${s.id}" title="删除">×</button>`;
      const im = d.querySelector("img");
      if (im) im.addEventListener("click", () => openLightbox(s.url));
      box.appendChild(d);
    }
    box.querySelectorAll(".del").forEach((btn) => {
      btn.addEventListener("click", () => {
        const id = Number(btn.dataset.id);
        const shot = sh.find((s) => s.id === id);
        if (!shot) return;
        deleteShot(shot);
      });
    });
  }

  async function save() {
    if (!isEditor()) { $("gp-status").textContent = "需要编辑者账号登录后才能保存。"; return; }
    const nameBox = document.getElementById("gp-edit-name");
    let targetName = state.current;
    if (state.isNew && nameBox && nameBox.value.trim()) targetName = nameBox.value.trim();
    const body = {
      game_name: targetName,
      developer: $("gp-developer").value.trim(),
      gameplay_desc: $("gp-desc").value.trim(),
      tags: $("gp-tags").value.split(",").map((t) => t.trim()).filter(Boolean),
      notes: $("gp-notes").value.trim(),
      value: (document.querySelector('input[name="gp-value"]:checked') || {}).value || "",
      abandon_reason: $("gp-abandon-reason").value.trim(),
    };
    const files = Array.from($("gp-file").files || []);
    $("gp-status").textContent = files.length ? "保存并上传中…" : "保存中…";
    try {
      // 0) 新建游戏: 先写入 games 表(上榜日期=今天)
      if (state.isNew) {
        const gResp = await fetch(FN_URL, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            ...authHeaders(),
          },
          body: JSON.stringify({ action: "create_game", name: targetName }),
        });
        const gData = await gResp.json().catch(() => ({}));
        if (!gResp.ok || !gData.ok) throw new Error(gData.error || ("HTTP " + gResp.status));
      }
      // 1) 保存档案文字
      const resp = await fetch(FN_URL, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",

          ...authHeaders(),
        },
        body: JSON.stringify(body),
      });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok || !data.ok) throw new Error(data.error || ("HTTP " + resp.status));
      delete state.profiles[state.current];
      state.profiles[targetName] = body;

      // 2) 如有选中的图片,一并上传
      if (files.length) {
        const fd = new FormData();
        fd.append("game_name", targetName);
        for (const f of files) fd.append("files", f);
        const upResp = await fetch(FN_URL, {
          method: "POST",
          headers: {
            ...authHeaders(),
          },
          body: fd,
        });
        const upData = await upResp.json().catch(() => ({}));
        if (!upResp.ok || !upData.ok) throw new Error(upData.error || ("HTTP " + upResp.status));
        $("gp-file").value = "";
        await refreshShots(targetName);
      }

      $("gp-status").textContent = files.length ? "已保存档案并上传截图。" : "已保存到数据库。";
      if (state.isNew) {
        state.isNew = false;
        state.current = targetName;
        await reloadList();
      }
      backToList();
    } catch (err) {
      $("gp-status").textContent = "保存失败：" + err.message;
    }
  }

  async function refreshShots(name) {
    const shots = await window.sb.select("game_screenshots", {
      match: { game_name: name },
      order: "sort_order.asc,id.asc",
      limit: 200,
    });
    state.shots[name] = shots || [];
    renderShots();
  }

  async function reloadList() {
    const [games, profiles, shots, base] = await Promise.all([
      // 排序键必须**唯一**：sb.select 用 Range 分页（每页 1000），而
      // first_seen_at 精确到日、大量记录同值，仅按它排会得到不确定的顺序，
      // 跨页可能重复返回同一行（列表里出现两条同名记录）。补 name 兜底成全序。
      window.sb.select("games", { select: "name,first_seen_at,category,publisher_name", order: "first_seen_at.desc,name.asc", limit: 5000 }),
      window.sb.select("game_profiles", { limit: 5000 }),
      window.sb.select("game_screenshots", { select: "id,game_name,url,sort_order", order: "sort_order.asc,id.asc", limit: 5000 }),
      loadBaseGames(),
    ]);
    state.games = (games || []).map(game => preferRankFields(game, base[game.name] ? baseGame(game.name, base[game.name]) : null));
    state.profiles = {};
    for (const p of profiles || []) state.profiles[p.game_name] = p;
    state.shots = {};
    for (const s of shots || []) {
      if (!state.shots[s.game_name]) state.shots[s.game_name] = [];
      state.shots[s.game_name].push(s);
    }
    buildList();
  }

  function backToList() {
    state.current = null;
    $("gp-toolbar").style.display = "";
    $("gp-table-wrap").style.display = "";
    $("gp-form").style.display = "none";
    document.getElementById("gp-view").style.display = "none";
    if (state.fullListLoaded) buildList();
    else {
      $("gp-loading").style.display = "";
      loadAll().then(() => {
        $("gp-loading").style.display = "none";
      }).catch(() => {
        $("gp-loading").textContent = "产品列表加载失败";
      });
    }
  }


  // ---------- auth ??? (????? auth.js) ----------
  function isEditor() { return window.Auth ? window.Auth.isEditor() : false; }
  function canEditProfile() {
    if (!isEditor()) {
      if (window.Auth) window.Auth.openModal();
      return false;
    }
    if (!state.profilesLoaded) {
      alert("手工档案尚未载入，请刷新后再编辑。");
      return false;
    }
    return true;
  }
  function authHeaders() {
    if (window.Auth) return window.Auth.authHeaders();
    return { "x-client-info": ADMIN_KEY };
  }
  function renderAuthUI() {
    if (window.Auth) window.Auth.render();
    const nb = document.getElementById("gp-new");
    if (nb) nb.style.display = "";
  }
  function openAuthModal() { if (window.Auth) window.Auth.openModal(); }
  function closeAuthModal() { if (window.Auth) window.Auth.closeModal(); }
  async function restoreAuth() {
    if (window.Auth) await window.Auth.restore();
    renderAuthUI();
  }

  function init() {
    initMdEditor();
    if (window.Auth && window.Auth.bind) window.Auth.bind();
    if (window.Auth && window.Auth.onAuthChange) {
      window.Auth.onAuthChange(() => {
        renderAuthUI();
        if (state.games && state.games.length) renderList();
      });
    }
    $("gp-save").addEventListener("click", save);
    $("gp-view-back").addEventListener("click", backToList);
    $("gp-back").addEventListener("click", backToList);
    $("gp-edit-btn").addEventListener("click", () => { if (canEditProfile()) enterEditMode(); });
    document.querySelectorAll('input[name="gp-value"]').forEach((r) => {
      r.addEventListener("change", () => {
        const show = r.value === "abandoned" && r.checked;
        const lbl = document.getElementById("gp-abandon-reason-label");
        const box = $("gp-abandon-reason");
        if (lbl) lbl.style.display = show ? "" : "none";
        if (box) box.style.display = show ? "" : "none";
      });
    });
    $("gp-clear").addEventListener("click", () => { $("gp-search").value = ""; state.page = 0; renderList(); });
    $("gp-prev").addEventListener("click", () => { if (state.page > 0) { state.page--; renderList(); } });
    $("gp-next").addEventListener("click", () => { state.page++; renderList(); });
    $("gp-search").addEventListener("input", () => { state.page = 0; renderList(); });
  }

  (async function () {
    init();
    try {
      const params = new URLSearchParams(location.search);
      const queryName = params.get("name");
      restoreAuth().catch(() => {});
      if (queryName) {
        $("gp-loading").style.display = "none";
        renderBoardFilter();
        await loadFastGame(queryName, {
          platform: params.get("platform"),
          board: params.get("board"),
          storeKey: params.get("store"),
          release: params.get("release"),
          snapshot: params.get("snapshot"),
        });
      } else {
        await loadAll();
        $("gp-loading").style.display = "none";
        renderBoardFilter();  // initial board sub-filter for the default platform
      }
    } catch (err) {
      $("gp-loading").textContent = "加载失败：" + err.message;
    }
  })();
})();
