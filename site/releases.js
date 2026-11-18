/* Official release dates and discovery dates are intentionally separate. */
window.ReleaseMonitor = (() => {
  let generation = 0;
  let snapshot, platform;
  const el = id => document.getElementById(id);
  const escape = value => String(value ?? "").replace(/[&<>"']/g, c => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
  const time = value => value ? new Date(value).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai" }) : "未提供";
  const sources = {
    "apple:newapplications": "Apple 新应用 RSS", "apple:newfreeapplications": "Apple 新免费应用 RSS",
    "apple:newpaidapplications": "Apple 新付费应用 RSS", "google:new_free": "Google Play 免费新游",
    "google:new_paid": "Google Play 付费新游",
    "apple:free": "Apple 美区免费榜", "google:free": "Google Play 美区免费榜",
  };
  const statusNames = { verified: "已核实", pending: "待核验", scheduled: "待发布", undated: "发布日期待核实", unavailable: "美区暂不可查" };

  function render() {
    const all = snapshot.apps.filter(row => row.platform === platform && row.status !== "not_game");
    const status = el("release-status").value;
    const date = el("release-day").value;
    const price = el("release-price").value;
    const rows = all.filter(row => {
      if (status !== "all" && (status === "pending" ? !["pending", "undated", "unavailable"].includes(row.status) : row.status !== status)) return false;
      if (date && row.release_date !== date) return false;
      return price === "all" || (price === "free" ? row.free === true : row.free === false);
    }).sort((a, b) => (b.release_date || "").localeCompare(a.release_date || "") || a.name.localeCompare(b.name));
    el("release-day").disabled = false;
    const verified = all.filter(row => row.status === "verified").length;
    const releasedToday = all.filter(row => row.status === "verified" && row.release_date === snapshot.date_beijing).length;
    el("release-count").textContent = `显示 ${rows.length} 款 · 已核实 ${verified} 款 · 监测日发布 ${releasedToday} 款 · 待核验 ${all.filter(row => ["pending", "undated", "unavailable"].includes(row.status)).length} 款`;
    const tbody = el("tbl-releases").querySelector("tbody");
    tbody.innerHTML = rows.map(row => {
      const expected = platform === "ios" ? "https://apps.apple.com/us/" : "https://play.google.com/store/apps/";
      const identity = { ...row, app_id: platform === "ios" ? row.id : undefined, package: platform === "android" ? row.id : undefined };
      const game = gameCell(row.name, identity, { plat: platform, label: "美区每日新上架" });
      const storeLink = row.url?.startsWith(expected)
        ? ` · <a href="${escape(row.url)}" target="_blank" rel="noopener noreferrer">查看商店</a>` : "";
      const amount = row.free === true ? "免费" : row.free === false ? `付费 ${escape(row.currency || "")} ${escape(row.price ?? "")}` : "待核实";
      return `<tr><td>${escape(row.release_date || "未知")}</td><td>${game}<div class="muted">${escape(row.id)}${storeLink}</div></td>
        <td>${amount}</td><td>${escape(row.publisher || "—")}</td><td>${escape(time(row.first_discovered_at))}</td>
        <td>${escape((row.sources || []).map(source => sources[source] || source).join("、"))}<div class="muted">${escape(statusNames[row.status] || row.status)}${row.error ? ` · ${escape(row.error)}` : ""}</div></td></tr>`;
    }).join("") || '<tr><td colspan="6" class="muted">此筛选下暂无已记录游戏。可切换发布日期查看历史记录，日期未知的记录请选择“全部已记录日期”；没有发现不代表商店当天没有上架。</td></tr>';
  }

  async function show(date, plat) {
    const ticket = ++generation;
    platform = plat;
    el("release-title").textContent = `${plat === "ios" ? "App Store" : "Google Play"} · 美区每日新上架 · ${date} 监测快照`;
    el("tbl-releases").querySelector("tbody").innerHTML = '<tr><td colspan="6">加载中…</td></tr>';
    el("release-sources").textContent = "";
    el("release-coverage").textContent = "官方来源优先，覆盖有限；采集日期与官方发布日期分别记录。";
    el("release-count").textContent = "";
    try {
      const response = await fetch(`./data/releases/${date}.json`, { cache: "no-cache" });
      if (!response.ok) throw new Error(response.status === 404 ? "所选日期尚无上架监控快照" : `HTTP ${response.status}`);
      const data = await response.json();
      if (ticket !== generation) return;
      snapshot = data;
      el("release-coverage").textContent = `${data.coverage} 本次采集：${time(data.scraped_at_utc)}（北京时间）。`;
      const dates = [...new Set([date, ...data.apps.filter(row => row.platform === plat && row.status !== "not_game" && row.release_date).map(row => row.release_date)])].sort().reverse();
      el("release-day").innerHTML = '<option value="">全部已记录日期</option>' + dates.map(day => `<option value="${escape(day)}">${escape(day)}</option>`).join("");
      el("release-day").value = date;
      el("release-status").value = "all";
      el("release-price").value = "all";
      const prefix = plat === "ios" ? "apple:" : "google:";
      el("release-sources").innerHTML = Object.entries(data.sources).filter(([source]) => source.startsWith(prefix) || source === "discovery").map(([source, info]) => {
        const updated = info.source_updated_at;
        const stale = updated && Date.now() - Date.parse(updated) > 48 * 3600000;
        return `<p><strong>${escape(sources[source] || source)}</strong> · 上次成功采集：${escape(time(info.last_success_at))}<br>
          来源更新时间：${escape(time(updated))}${stale ? "（已超过 48 小时，请留意来源延迟）" : ""} · 本次尝试：${escape(time(info.attempted_at))}<br>
          ${info.latest_candidate_release_date ? `候选中最近的已到发布日期：${escape(info.latest_candidate_release_date)}<br>` : ""}
          ${info.error ? `采集失败，保留已收录记录：${escape(info.error)}` : `候选 ${escape(info.candidates ?? 0)} 款`}</p>`;
      }).join("");
      for (const id of ["release-day", "release-price", "release-status"]) el(id).onchange = render;
      render();
    } catch (error) {
      if (ticket !== generation) return;
      el("tbl-releases").querySelector("tbody").innerHTML = `<tr><td colspan="6" class="muted">${escape(error.message)}</td></tr>`;
    }
  }
  return { show, cancel() { generation++; } };
})();
