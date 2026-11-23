(() => {
  let hkjcState = null;
  let selectedRaceId = null;
  let betsFilterMode = "all"; // "all" | "race" | "settled"
  let settingsFormDirty = false;
  let ws = null;

  const $ = (id) => document.getElementById(id);

  function escapeHtml(str) {
    return String(str ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function fmtHkd(val, showSign = false) {
    const n = Number(val || 0);
    const abs = Math.abs(n).toLocaleString("en-US", {
      minimumFractionDigits: 0,
      maximumFractionDigits: 0,
    });
    if (showSign) {
      return n >= 0 ? `+HK$${abs}` : `-HK$${abs}`;
    }
    return n < 0 ? `-HK$${abs}` : `HK$${abs}`;
  }

  function fmtTimeHkt(isoStr) {
    if (!isoStr) return "--:--:--";
    try {
      return new Date(isoStr).toLocaleTimeString("zh-HK", {
        timeZone: "Asia/Hong_Kong",
        hour12: false,
      });
    } catch {
      return isoStr;
    }
  }

  function updateHktClock() {
    const el = $("header-hkt-clock");
    if (!el) return;
    el.textContent = new Date().toLocaleTimeString("zh-HK", {
      timeZone: "Asia/Hong_Kong",
      hour12: false,
    });
  }

  function showToast(msg) {
    const container = $("toast-container");
    if (!container) return;
    const el = document.createElement("div");
    el.className = "toast";
    el.textContent = msg;
    container.appendChild(el);
    setTimeout(() => el.remove(), 4200);
  }

  async function apiPost(url, body = {}) {
    const resp = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await resp.json();
    if (!resp.ok || data.ok === false) {
      throw new Error(data.error || `Request failed (${resp.status})`);
    }
    return data;
  }

  // =====================================================================
  // RENDER HKJC REAL-TIME QUANT & SELF-EVOLVING STATE
  // =====================================================================
  function renderHkjc(hkjc) {
    if (!hkjc) return;
    hkjcState = hkjc;

    const meeting = hkjc.meeting_info || {};
    const sync = hkjc.sync_status || {};
    const cfg = hkjc.config || {};
    const mState = hkjc.model_state || {};
    const port = hkjc.portfolio || {};
    const races = hkjc.races || [];
    const bets = hkjc.paper_bets || [];
    const evolutions = hkjc.evolutions || [];

    // Ensure valid selectedRaceId
    if (!selectedRaceId || !races.some((r) => r.race_id === selectedRaceId)) {
      selectedRaceId = hkjc.next_open_race_id || (races[0] && races[0].race_id) || null;
    }

    // 1. Topbar status & meeting pill
    if ($("hkjc-meeting-pill")) {
      const invStr = meeting.total_investment_hkd
        ? ` · 總投注額 HK$${(Number(meeting.total_investment_hkd) / 1e8).toFixed(2)}億`
        : "";
      $("hkjc-meeting-pill").textContent = `${meeting.date || ""} ${meeting.venue_name || "沙田"} (${races.length}場${invStr})`;
    }
    if ($("hkjc-version-pill")) {
      $("hkjc-version-pill").textContent = `模型 v${mState.version || "2.0.0"}`;
    }
    if ($("hkjc-sync-status-text")) {
      const srcLabel =
        sync.source === "HKJC_LIVE_GRAPHQL_AND_RACING_INFO"
          ? "馬會官方 GraphQL 實時連線"
          : "馬會官方數據快取";
      $("hkjc-sync-status-text").textContent = `${srcLabel} (${fmtTimeHkt(sync.last_sync_at)})`;
    }

    const nextOpenRace = races.find((r) => r.status !== "SETTLED");
    const settleNextBtn = $("hkjc-settle-next-btn");
    if (settleNextBtn) {
      if (nextOpenRace) {
        settleNextBtn.disabled = false;
        settleNextBtn.textContent = `🏁 結算第 ${nextOpenRace.race_no} 場賽果 & 自動升級`;
      } else {
        settleNextBtn.disabled = true;
        settleNextBtn.textContent = `✅ 全日 ${races.length} 場均已結算並完成進化`;
      }
    }

    // 2. 4-Card Executive KPI Strip
    if ($("hkjc-kpi-equity")) $("hkjc-kpi-equity").textContent = fmtHkd(port.equity_hkd || 100000);
    if ($("hkjc-kpi-cash")) $("hkjc-kpi-cash").textContent = fmtHkd(port.cash_hkd || 100000);
    if ($("hkjc-kpi-open-stake")) $("hkjc-kpi-open-stake").textContent = fmtHkd(port.open_stake_hkd || 0);

    const pnl = Number(port.realized_pnl_hkd || 0);
    const pnlEl = $("hkjc-kpi-pnl");
    if (pnlEl) {
      pnlEl.textContent = fmtHkd(pnl, true);
      pnlEl.className = `kpi-value mono-num ${pnl >= 0 ? "pnl-pos" : "pnl-neg"}`;
    }

    const roiPct = Number(port.roi_pct || 0);
    const roiBadge = $("hkjc-kpi-roi-badge");
    if (roiBadge) {
      roiBadge.textContent = `${roiPct >= 0 ? "+" : ""}${roiPct.toFixed(2)}% ROI`;
      roiBadge.className = `badge ${roiPct >= 0 ? "badge-success" : "badge-danger"}`;
    }

    const settledBetsCount = (port.won_bets || 0) + (port.lost_bets || 0);
    if ($("hkjc-kpi-winrate-tag")) {
      $("hkjc-kpi-winrate-tag").textContent =
        settledBetsCount > 0
          ? `命中率 ${Number(port.win_rate_pct || 0).toFixed(1)}%`
          : `進行中 ${port.open_bets || 0} 筆`;
    }
    if ($("hkjc-kpi-settled-stake")) $("hkjc-kpi-settled-stake").textContent = fmtHkd(port.settled_stake_hkd || 0);
    if ($("hkjc-kpi-bets-summary")) {
      $("hkjc-kpi-bets-summary").textContent = `${port.total_bets || 0} 筆 (${port.won_bets || 0} 勝 / ${port.lost_bets || 0} 負)`;
    }

    const settledRacesCnt = Number(port.settled_races_count || 0);
    if ($("hkjc-kpi-progress-badge")) {
      $("hkjc-kpi-progress-badge").textContent = `已進化 ${settledRacesCnt} / ${port.total_races_count || races.length} 場`;
    }

    if ($("hkjc-kpi-hit-summary")) {
      if (settledRacesCnt > 0) {
        const t1 = port.top1_hits_benter2 || 0;
        const t3 = port.top3_hits_benter2 || 0;
        $("hkjc-kpi-hit-summary").innerHTML = `頭馬首選命中 <span class="pnl-pos">${t1}/${settledRacesCnt}</span> · 前三命中 <span class="pnl-pos">${t3}/${settledRacesCnt}</span>`;
      } else {
        $("hkjc-kpi-hit-summary").textContent = "等待首場完賽對比";
      }
    }

    if ($("hkjc-kpi-logloss-sub")) {
      if (settledRacesCnt > 0) {
        const ll2 = Number(port.avg_log_loss_benter2 || 0).toFixed(3);
        const ll1 = Number(port.avg_log_loss_benter1 || 0).toFixed(3);
        const imp = ll1 > 0 ? (((ll1 - ll2) / ll1) * 100).toFixed(1) : "0.0";
        $("hkjc-kpi-logloss-sub").innerHTML = `對數損失：<strong class="mono-num" style="color:#34d399;">2.0版 ${ll2}</strong> vs 1.0版 ${ll1} (優化 +${imp}%)`;
      } else {
        $("hkjc-kpi-logloss-sub").textContent = "每場完賽後自動對比 Benter 2.0 與 1.0 對數損失";
      }
    }

    if ($("hkjc-kpi-theta-badge")) {
      $("hkjc-kpi-theta-badge").textContent = `θ₂=${Number(mState.theta_2 || 0.81).toFixed(3)} · θ₃=${Number(mState.theta_3 || 0.71).toFixed(3)}`;
    }

    const drawBias = Number(mState.day_draw_bias || 0);
    const paceBias = Number(mState.day_pace_bias || 0);
    if ($("hkjc-kpi-bias-summary")) {
      const dTxt =
        drawBias < -0.04
          ? `利內檔 (${drawBias.toFixed(2)})`
          : drawBias > 0.04
          ? `利外檔 (+${drawBias.toFixed(2)})`
          : "內外檔均勢";
      const pTxt =
        paceBias > 0.05
          ? `利後上衝刺 (+${paceBias.toFixed(2)})`
          : paceBias < -0.05
          ? `利前領守好位 (${paceBias.toFixed(2)})`
          : "步速均勢";
      $("hkjc-kpi-bias-summary").textContent = `${dTxt} · ${pTxt}`;
    }

    if ($("hkjc-kpi-bias-sub")) {
      const preRaces = Number(mState.offline_pretrained_races || 3850);
      const todayPairs = Number(mState.cumulative_metrics?.total_pairwise_samples || 0);
      const jCount = Object.keys(mState.intraday_jockey_form || {}).length;
      if (todayPairs > 0) {
        $("hkjc-kpi-bias-sub").innerHTML = `離線先驗 <strong>${preRaces.toLocaleString()}場</strong> · 今日全場配對 <strong>+${todayPairs}組 C(N,2)</strong> · 騎練手風 <strong>${jCount}人</strong>`;
      } else {
        $("hkjc-kpi-bias-sub").innerHTML = `離線多季先驗基底 <strong>${preRaces.toLocaleString()}場 (31.2萬組配對)</strong> · 雙時間尺度動態校準`;
      }
    }

    // 3. Horizontal Race Selector Tabs (R1..R11)
    const tablist = $("hkjc-race-tablist");
    if (tablist) {
      tablist.innerHTML = races
        .map((r) => {
          const isSel = r.race_id === selectedRaceId;
          let badgeHtml = `<span class="badge badge-outline" style="font-size:10px;">⏳ 待結算</span>`;
          if (r.status === "SETTLED") {
            const rPnl = Number(r.settlement?.comparison?.race_pnl_hkd || 0);
            badgeHtml = `<span class="badge ${rPnl >= 0 ? "badge-success" : "badge-danger"}" style="font-size:10px;">${rPnl >= 0 ? "✅ " : "🏁 "}${fmtHkd(rPnl, true)}</span>`;
          } else if (r.status === "BETTING_OPEN") {
            badgeHtml = `<span class="badge badge-accent" style="font-size:10px;">🎯 當前分析</span>`;
          }
          return `
            <button
              type="button"
              class="hkjc-race-tab"
              role="tab"
              data-race-id="${escapeHtml(r.race_id)}"
              aria-selected="${isSel ? "true" : "false"}"
              ${isSel ? "focusgroupstart" : ""}
            >
              <div class="hkjc-race-tab-top">
                <span>第 ${r.race_no} 場 (${escapeHtml(r.scheduled_time)})</span>
                ${badgeHtml}
              </div>
              <div class="hkjc-race-tab-sub">${escapeHtml(r.distance_m + "m · " + r.race_class)}</div>
            </button>
          `;
        })
        .join("");
    }

    // 4. Selected Race Details
    const selectedRace = races.find((r) => r.race_id === selectedRaceId) || races[0];
    if (!selectedRace) return;

    const settleCurrBtn = $("hkjc-settle-current-btn");
    if (settleCurrBtn) {
      if (selectedRace.status === "SETTLED") {
        settleCurrBtn.disabled = true;
        settleCurrBtn.textContent = `✅ 第 ${selectedRace.race_no} 場已完賽並升級`;
      } else {
        settleCurrBtn.disabled = false;
        settleCurrBtn.textContent = `🏁 結算第 ${selectedRace.race_no} 場賽果並升級`;
      }
    }

    if ($("hkjc-field-rule-badge")) {
      const rCount = (selectedRace.runners || []).length;
      $("hkjc-field-rule-badge").textContent =
        rCount <= 6
          ? `出馬 ${rCount} 匹 (位置僅派前二名 · 不設位置Q)`
          : `出馬 ${rCount} 匹 (位置派前三名 · 設連贏及位置Q)`;
    }

    renderRaceHeader(selectedRace);
    renderPostRaceReport(selectedRace);
    renderRunnersTable(selectedRace);
    renderExoticGrid(selectedRace);
    renderBetsSidebar(bets, selectedRace.race_id);
    renderModelSidebar(mState, evolutions);
    renderTelegramSidebar(hkjc.telegram || {}, hkjc.telegram_logs || [], selectedRace, bets);
  }

  function renderRaceHeader(race) {
    const el = $("hkjc-race-banner");
    if (!el) return;
    const pa = race.pace_analysis || {};
    const runners = race.runners || [];
    const leaders = runners.filter((r) => r.style_code === "LEADER").map((r) => `#${r.horse_no} ${r.horse_name_ch}(${r.draw}檔)`);
    const onPace = runners.filter((r) => r.style_code === "ON_PACE").map((r) => `#${r.horse_no} ${r.horse_name_ch}(${r.draw}檔)`);
    const mids = runners.filter((r) => r.style_code === "MIDFIELD" || r.style_code === "GRINDER").map((r) => `#${r.horse_no} ${r.horse_name_ch}`);
    const closers = runners.filter((r) => r.style_code === "CLOSER").map((r) => `#${r.horse_no} ${r.horse_name_ch}`);

    const poolInvStr = race.pool_investment_hkd
      ? `單場彩池投注額：HK$${(Number(race.pool_investment_hkd) / 1e6).toFixed(1)}M`
      : `總獎金：${fmtHkd(race.prize_hkd)}`;

    const stBadge =
      race.status === "SETTLED"
        ? `<span class="badge badge-success">🏁 已對比官方賽果並完成自進化</span>`
        : `<span class="badge badge-accent">🎯 賽前無未來函數量化定價中</span>`;

    el.innerHTML = `
      <div class="race-header-top">
        <div>
          <div class="panel-title-row">
            <span class="badge badge-outline">第 ${race.race_no} 場 · ${escapeHtml(race.scheduled_time)} HKT</span>
            <span class="badge badge-outline">${escapeHtml(race.race_class)} · ${race.distance_m}米 · ${escapeHtml(race.going)}</span>
            <span class="badge badge-outline">${escapeHtml(race.surface)}</span>
            ${stBadge}
          </div>
          <h3 class="race-title-main">${escapeHtml(race.race_name)} <small style="font-size:12px;color:#94a3b8;font-weight:400;">${escapeHtml(race.race_name_en)}</small></h3>
          <div class="race-meta-line">
            ${escapeHtml(poolInvStr)} · 步速博弈判定：<strong style="color:#6ee7b7;">${escapeHtml(pa.pace_regime_label || "")}</strong>
          </div>
        </div>
        <div>
          <span class="badge ${pa.ppi_score >= 74 ? "badge-danger" : pa.ppi_score <= 46 ? "badge-success" : "badge-accent"}" style="font-size:12px;padding:5px 10px;">
            🧭 步速壓力指數 PPI: ${Number(pa.ppi_score || 50).toFixed(1)}
          </span>
        </div>
      </div>

      <details class="speedmap-details">
        <summary class="speedmap-summary">展開全場預計走位分佈圖 (Speed Map：領放 ${leaders.length} 匹 / 跟前 ${onPace.length} 匹 / 居中 ${mids.length} 匹 / 後追 ${closers.length} 匹)</summary>
        <div class="hkjc-speedmap-lanes">
          <div class="hkjc-lane-col">
            <span class="hkjc-lane-title">🔥 領放快閘 (${leaders.length})</span>
            <div>${leaders.length ? escapeHtml(leaders.join("、")) : "無純放頭馬"}</div>
          </div>
          <div class="hkjc-lane-col">
            <span class="hkjc-lane-title">⚡ 跟前好位 (${onPace.length})</span>
            <div>${onPace.length ? escapeHtml(onPace.join("、")) : "—"}</div>
          </div>
          <div class="hkjc-lane-col">
            <span class="hkjc-lane-title">⚖️ 居中/均速 (${mids.length})</span>
            <div>${mids.length ? escapeHtml(mids.join("、")) : "—"}</div>
          </div>
          <div class="hkjc-lane-col">
            <span class="hkjc-lane-title">🚀 留後衝刺 (${closers.length})</span>
            <div>${closers.length ? escapeHtml(closers.join("、")) : "—"}</div>
          </div>
        </div>
      </details>
    `;
  }

  function renderPostRaceReport(race) {
    const panel = $("hkjc-postrace-panel");
    if (!panel) return;
    if (race.status !== "SETTLED" || !race.settlement) {
      panel.classList.add("hidden");
      panel.innerHTML = "";
      return;
    }

    panel.classList.remove("hidden");
    const st = race.settlement;
    const comp = st.comparison || {};
    const divs = st.dividends || {};
    const top4 = (st.full_order || []).slice(0, 4);
    const flaws = comp.flaws_and_fixes || [];
    const deltas = comp.weight_deltas || {};
    const rPnl = Number(comp.race_pnl_hkd || 0);

    const deltaBadges = Object.entries(deltas)
      .filter(([, v]) => Math.abs(Number(v)) >= 0.0001)
      .map(([k, v]) => {
        const num = Number(v);
        return `<span class="badge ${num > 0 ? "badge-success" : "badge-outline"} mono-num">${escapeHtml(k.replace("w_", ""))}: ${num > 0 ? "+" : ""}${(num * 100).toFixed(2)}%</span>`;
      })
      .join(" ");

    const pairSamples = comp.pairwise_samples || ((st.full_order?.length || 12) * ((st.full_order?.length || 12) - 1)) / 2;

    panel.innerHTML = `
      <div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px;padding-bottom:10px;border-bottom:1px solid rgba(16,185,129,0.3);">
        <div class="panel-title-row">
          <span class="badge badge-success">🏁 第 ${race.race_no} 場雙時間尺度賽後學習報告</span>
          <span class="badge badge-accent">版本升級：v${escapeHtml(comp.old_version)} → v${escapeHtml(comp.new_version)}</span>
          <span class="badge badge-outline mono-num">全場配對樣本：${pairSamples} 組 C(N,2)</span>
          <span class="badge ${rPnl >= 0 ? "badge-success" : "badge-danger"}">本場實戰損益：${fmtHkd(rPnl, true)} (本金 ${fmtHkd(comp.race_staked_hkd)})</span>
        </div>
        <div style="font-size:12px;color:#a7f3d0;">
          頭馬模型排名：<strong>第 ${comp.winner_rank_benter2} 位</strong> (傳統1.0: 第 ${comp.winner_rank_benter1} 位) · 對數損失：<strong>${comp.log_loss_benter2}</strong>
        </div>
      </div>

      <div class="hkjc-postrace-grid">
        <div>
          <div style="font-size:12px;color:#cbd5e1;margin-bottom:8px;">
            <strong>📊 官方實際步速與賽果：</strong>${escapeHtml(st.actual_pace_scenario)}
          </div>
          <div class="hkjc-podium-list">
            ${top4
              .map((fin, idx) => {
                const medal = idx === 0 ? "🥇 冠軍" : idx === 1 ? "🥈 亞軍" : idx === 2 ? "🥉 季軍" : "4️⃣ 殿軍";
                const marginTxt = idx === 0 ? "頭馬" : `負 ${escapeHtml(fin.beaten_lengths || "-")} 馬位`;
                return `
                  <div class="hkjc-podium-row">
                    <div>
                      <strong>${medal} #${fin.horse_no} ${escapeHtml(fin.horse_name_ch || fin.horse_name)}</strong>
                      <span style="font-size:11px;color:#94a3b8;margin-left:6px;">(${escapeHtml(fin.jockey)} · ${fin.draw}檔 · 走位[${escapeHtml(fin.in_running)}] · ${marginTxt} · ${escapeHtml(fin.finish_time_sec)}s)</span>
                      <div style="font-size:11px;color:#93c5fd;margin-top:2px;">📋 競賽事件：${escapeHtml(fin.steward_incident)}</div>
                    </div>
                    <div class="mono-num" style="text-align:right;font-size:11.5px;white-space:nowrap;">
                      <div>2.0勝率: <strong style="color:#34d399;">${(fin.benter2_prob * 100).toFixed(1)}%</strong></div>
                      <div style="color:#94a3b8;">賠率: ${fin.market_win_odds}x</div>
                    </div>
                  </div>
                `;
              })
              .join("")}
          </div>
          <div style="display:flex;flex-wrap:wrap;gap:6px;margin-top:8px;">
            <span class="badge badge-outline mono-num">獨贏 #${divs.WIN?.horse_no}: HK$${divs.WIN?.dividend_per_10}</span>
            <span class="badge badge-outline mono-num">連贏 (${escapeHtml(divs.QUINELLA?.combination)}): HK$${divs.QUINELLA?.dividend_per_10}</span>
            ${Object.entries(divs.PLACE_QUINELLA || {})
              .map(([k, v]) => `<span class="badge badge-outline mono-num">位置Q (${escapeHtml(k)}): HK$${v}</span>`)
              .join("")}
          </div>
        </div>

        <div>
          <div style="font-size:12px;font-weight:700;color:#fbbf24;margin-bottom:8px;">
            🛠️ 賽後自動找出的偏差根源與即時模型升級動作
          </div>
          ${flaws
            .map(
              (f) => `
                <div class="hkjc-flaw-card">
                  <div><strong>【${escapeHtml(f.category)}】</strong> ${escapeHtml(f.finding)}</div>
                  <div class="hkjc-flaw-action">⚡ 升級動作：${escapeHtml(f.action)}</div>
                </div>
              `
            )
            .join("")}
          <div style="margin-top:6px;font-size:11.5px;color:#cbd5e1;display:flex;align-items:center;gap:6px;flex-wrap:wrap;">
            <strong>權重更新：</strong> ${deltaBadges}
          </div>
        </div>
      </div>
    `;
  }

  function renderRunnersTable(race) {
    const tbody = $("hkjc-runners-tbody");
    if (!tbody) return;
    const isSettled = race.status === "SETTLED";
    const orderMap = {};
    if (isSettled && race.settlement?.full_order) {
      race.settlement.full_order.forEach((o) => {
        orderMap[o.horse_no] = o;
      });
    }

    // Sort runners by Benter 2.0 win probability descending for immediate clarity
    const sortedRunners = [...(race.runners || [])].sort((a, b) => b.benter2_prob - a.benter2_prob);

    tbody.innerHTML = sortedRunners
      .map((r, rankIdx) => {
        const isRecommended = ( rankIdx < 3 && r.win_ev_pct >= 5.0 ) || ( rankIdx < 4 && r.place_ev_pct >= 5.0 );
        const fin = orderMap[r.horse_no];
        let finBadge = "";
        if (fin) {
          const pos = fin.position;
          const cls = pos === 1 ? "badge-success" : pos <= 3 ? "badge-accent" : "badge-outline";
          const lbl = pos === 1 ? "🥇 冠軍" : pos === 2 ? "🥈 亞軍" : pos === 3 ? "🥉 季軍" : `第 ${pos} 名`;
          finBadge = `<span class="badge ${cls}" style="margin-left:5px;">${lbl}</span>`;
        }

        const signalsHtml = (r.key_signals || [])
          .map((s) => `<span class="signal-tag">${escapeHtml(s)}</span>`)
          .join("");

        const edgeVal = Number(r.prob_edge_vs_b1_pct || 0);
        const edgeStr = edgeVal >= 0 ? `+${edgeVal.toFixed(1)}%` : `${edgeVal.toFixed(1)}%`;

        return `
          <tr class="${isRecommended ? "row-qualified" : ""}">
            <th scope="row">
              <div style="display:flex;align-items:center;flex-wrap:wrap;gap:4px;">
                <span class="hkjc-horse-num-pill">${r.horse_no}</span>
                <strong>${escapeHtml(r.horse_name_ch)}</strong>
                <span class="badge badge-outline" style="font-size:10px;">2.0排#${rankIdx + 1}</span>
                ${finBadge}
              </div>
              <div style="font-size:11.5px;color:#94a3b8;margin-top:3px;">
                騎師：<strong>${escapeHtml(r.jockey)}</strong> · 練馬師：${escapeHtml(r.trainer)}
              </div>
            </th>

            <td class="mono-num">
              <div>
                <span class="badge badge-outline">${r.draw} 檔</span>
                <span class="badge badge-outline">${r.weight_lbs} 磅</span>
                <span class="badge badge-outline">評分 ${r.rating} (${r.rating_delta >= 0 ? "+" + r.rating_delta : r.rating_delta})</span>
              </div>
              <div style="font-size:11px;color:#94a3b8;margin-top:4px;">
                近6仗: <strong>${escapeHtml(r.last6run)}</strong> · 最佳: ${escapeHtml(r.best_time_str)}
              </div>
            </td>

            <td>
              <div>${signalsHtml}</div>
              <div style="font-size:11px;color:#94a3b8;margin-top:3px;">
                ${escapeHtml(r.style_label)} · 波動 σ=${r.sigma_hetero} · 跨池聰明錢 ${r.cross_pool_ratio}x
              </div>
            </td>

            <td class="mono-num">
              <div style="font-size:13px;">
                獨贏: <strong style="color:#34d399;">${(r.benter2_prob * 100).toFixed(1)}%</strong>
                <small style="color:#94a3b8;">(1.0版 ${(r.benter1_prob * 100).toFixed(1)}%, ${edgeStr})</small>
              </div>
              <div class="prob-bar-track">
                <div class="prob-bar-fill" style="width:${Math.min(100, r.benter2_prob * 220)}%"></div>
              </div>
              <div style="font-size:11.5px;color:#93c5fd;margin-top:3px;">
                入位率: <strong>${(r.benter2_place_prob * 100).toFixed(1)}%</strong> · 公眾: ${(r.public_prob * 100).toFixed(1)}%
              </div>
            </td>

            <td class="mono-num">
              <div style="font-size:13px;">
                獨贏: <strong style="color:#fbbf24;">${r.market_win_odds} 倍</strong>
                ${r.win_odds_drop_pct >= 5 ? `<span class="badge badge-success" style="font-size:10px;">落飛 -${r.win_odds_drop_pct}%</span>` : ""}
              </div>
              <div style="font-size:11.5px;color:#cbd5e1;margin-top:3px;">
                位置: <strong>${r.market_place_odds} 倍</strong>
              </div>
            </td>

            <td class="text-right mono-num">
              <div style="font-size:11.5px;margin-bottom:5px;">
                獨贏EV <strong class="${r.win_ev_pct >= 5 ? "pnl-pos" : "pnl-neg"}">${r.win_ev_pct >= 0 ? "+" : ""}${r.win_ev_pct}%</strong> ·
                位置EV <strong class="${r.place_ev_pct >= 5 ? "pnl-pos" : "pnl-neg"}">${r.place_ev_pct >= 0 ? "+" : ""}${r.place_ev_pct}%</strong>
              </div>
              <div style="display:flex;gap:5px;justify-content:flex-end;">
                <button
                  type="button"
                  class="btn btn-sm ${r.win_ev_pct >= 5 && rankIdx < 3 ? "btn-primary" : "btn-outline"} hkjc-bet-btn"
                  data-race-id="${escapeHtml(race.race_id)}"
                  data-pool-type="WIN"
                  data-selection-key="${r.horse_no}"
                  data-stake="${r.rec_win_stake_hkd >= 100 ? r.rec_win_stake_hkd : 300}"
                  ${isSettled ? "disabled" : ""}
                >
                  獨贏 ${fmtHkd(r.rec_win_stake_hkd >= 100 ? r.rec_win_stake_hkd : 300)}
                </button>
                <button
                  type="button"
                  class="btn btn-sm ${r.place_ev_pct >= 5 && rankIdx < 4 ? "btn-secondary" : "btn-ghost"} hkjc-bet-btn"
                  data-race-id="${escapeHtml(race.race_id)}"
                  data-pool-type="PLACE"
                  data-selection-key="${r.horse_no}"
                  data-stake="${r.rec_place_stake_hkd >= 100 ? r.rec_place_stake_hkd : 300}"
                  ${isSettled ? "disabled" : ""}
                >
                  位置 ${fmtHkd(r.rec_place_stake_hkd >= 100 ? r.rec_place_stake_hkd : 300)}
                </button>
              </div>
            </td>
          </tr>
        `;
      })
      .join("");
  }

  function renderExoticGrid(race) {
    const grid = $("hkjc-exotic-grid");
    if (!grid) return;
    const recs = race.exotic_recommendations || [];
    const isSettled = race.status === "SETTLED";
    if (recs.length === 0) {
      grid.innerHTML = `<div class="empty-cell" style="grid-column:1/-1;">本場無超過正期望值門檻之連贏/位置Q組合（或出馬少於 7 匹不設位置Q）</div>`;
      return;
    }

    grid.innerHTML = recs
      .map(
        (ex) => `
          <div class="hkjc-exotic-card">
            <div>
              <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:6px;">
                <span class="badge badge-accent">${escapeHtml(ex.pool_label)}</span>
                <span class="badge badge-success mono-num">期望值 +${ex.ev_pct}%</span>
              </div>
              <div style="font-size:14px;font-weight:700;margin-bottom:4px;">${escapeHtml(ex.selection_label)}</div>
              <div class="mono-num" style="font-size:12px;color:#cbd5e1;">
                2.0 命中率: <strong style="color:#34d399;">${(ex.benter2_prob * 100).toFixed(1)}%</strong> ·
                馬會實時賠率: <strong style="color:#fbbf24;">${ex.market_odds} 倍</strong>
              </div>
              <div style="font-size:11px;color:#94a3b8;margin-top:4px;">${escapeHtml(ex.rationale)}</div>
            </div>
            <button
              type="button"
              class="btn btn-primary btn-sm hkjc-bet-btn"
              style="width:100%;"
              data-race-id="${escapeHtml(race.race_id)}"
              data-pool-type="${escapeHtml(ex.pool_type)}"
              data-selection-key="${escapeHtml(ex.selection_key)}"
              data-stake="${ex.rec_stake_hkd}"
              ${isSettled ? "disabled" : ""}
            >
              ➕ 模擬投注 ${escapeHtml(ex.pool_label)} (${fmtHkd(ex.rec_stake_hkd)})
            </button>
          </div>
        `
      )
      .join("");
  }

  function renderBetsSidebar(bets, currentRaceId) {
    if ($("hkjc-total-bets-count")) $("hkjc-total-bets-count").textContent = String(bets.length);
    const listEl = $("hkjc-bets-list");
    if (!listEl) return;

    let filtered = bets;
    if (betsFilterMode === "race") {
      filtered = bets.filter((b) => b.race_id === currentRaceId);
    } else if (betsFilterMode === "settled") {
      filtered = bets.filter((b) => b.status !== "OPEN");
    }

    if (filtered.length === 0) {
      listEl.innerHTML = `<div class="empty-cell">目前篩選條件下尚無注單</div>`;
      return;
    }

    const poolNames = {
      WIN: "獨贏 WIN",
      PLACE: "位置 PLA",
      QUINELLA: "連贏 QIN",
      PLACE_QUINELLA: "位置Q QPL",
    };

    listEl.innerHTML = filtered
      .map((b) => {
        let stBadge = `<span class="badge badge-accent">⏳ 待結算</span>`;
        if (b.status === "WON") {
          stBadge = `<span class="badge badge-success mono-num">✅ 命中 +${fmtHkd(b.net_pnl_hkd)} (收回 ${fmtHkd(b.payout_hkd)})</span>`;
        } else if (b.status === "LOST") {
          stBadge = `<span class="badge badge-danger mono-num">❌ 未中 (${fmtHkd(b.net_pnl_hkd)})</span>`;
        } else if (b.status === "REFUNDED") {
          stBadge = `<span class="badge badge-outline">🔄 退出馬退還本金</span>`;
        }

        return `
          <div class="pos-card">
            <div class="pos-header">
              <div>
                <span class="badge badge-outline">第 ${b.race_no} 場 · ${escapeHtml(poolNames[b.pool_type] || b.pool_type)}</span>
                <span class="badge badge-outline mono-num">v${escapeHtml(b.model_version)}</span>
                <div class="pos-title" style="margin-top:4px;">${escapeHtml(b.selection_label)}</div>
              </div>
              ${stBadge}
            </div>
            <div class="pos-metrics mono-num">
              <div>本金: <strong>${fmtHkd(b.stake_hkd)}</strong></div>
              <div>機率: <strong>${(Number(b.benter2_prob) * 100).toFixed(1)}%</strong></div>
              <div>賠率: <strong>${b.market_odds}x</strong> (EV +${b.expected_value_pct}%)</div>
            </div>
            <div style="font-size:11px;color:#94a3b8;margin-top:4px;">${escapeHtml(b.rationale || "")}</div>
          </div>
        `;
      })
      .join("");
  }

  function renderModelSidebar(mState, evolutions) {
    if ($("hkjc-evo-count-badge")) $("hkjc-evo-count-badge").textContent = String(evolutions.length);
    const wGrid = $("hkjc-weights-grid");
    if (wGrid && mState.weights) {
      const latestDeltas = (evolutions[0] && evolutions[0].weight_deltas) || {};
      const items = [
        { key: "w_fundamental", label: "1. 近績與最佳時間速指 (GBDT)" },
        { key: "w_pace_iia", label: "2. 反 IIA 步速與當日檔位偏差" },
        { key: "w_nlp_censored", label: "3. 競賽事件受阻免責與反彈" },
        { key: "w_intent", label: "4. 王牌馬 / 減分 / 首戴配備戰意" },
        { key: "w_hetero_mc", label: "5. 異方差波動度 σᵢ 與位置穩定度" },
        { key: "w_microstructure", label: "6. 跨彩池聰明錢與馬會落飛指標" },
      ];
      const preRaces = Number(mState.offline_pretrained_races || 3850);
      const prePairs = Number(mState.offline_pretrained_pairs || 312400);
      const todayPairs = Number(mState.cumulative_metrics?.total_pairwise_samples || 0);
      const topJockeys = Object.entries(mState.intraday_jockey_form || {})
        .sort((a, b) => Number(b[1]) - Number(a[1]))
        .slice(0, 3)
        .filter(([, v]) => Number(v) > 0.02)
        .map(([k, v]) => `${k}(+${Number(v).toFixed(2)})`)
        .join("、");

      const twoTierHeaderHtml = `
        <div class="weight-row-item" style="border-left:3px solid #6366f1;margin-bottom:4px;">
          <div style="font-size:11.5px;font-weight:700;color:#c7d2fe;">🏛️ 雙時間尺度學習架構 (Two-Timescale Adaptation)</div>
          <div style="font-size:11px;color:#94a3b8;margin-top:3px;">
            • <strong>離線預訓練先驗</strong>：${preRaces.toLocaleString()} 場 (${(prePairs / 10000).toFixed(1)}萬組 Plackett-Luce 配對)<br/>
            • <strong>慢速層 (結構權重)</strong>：L2 先驗錨定 + 全場累計 <span class="mono-num" style="color:#34d399;">+${todayPairs}</span> 組 C(N,2) 馬位加權梯度<br/>
            • <strong>快速層 (當日手風)</strong>：${topJockeys ? `今日超預期騎師 ${escapeHtml(topJockeys)}` : "隨每場完賽全場殘差即時更新"}
          </div>
        </div>
      `;

      wGrid.innerHTML =
        twoTierHeaderHtml +
        items
          .map((it) => {
          const w = Number(mState.weights[it.key] || 0);
          const d = Number(latestDeltas[it.key] || 0);
          const dStr =
            Math.abs(d) >= 0.0001
              ? `<span class="${d > 0 ? "pnl-pos" : "pnl-neg"} mono-num" style="font-size:11px;">(${d > 0 ? "+" : ""}${(d * 100).toFixed(2)}%)</span>`
              : "";
          return `
            <div class="weight-row-item">
              <div class="weight-row-top">
                <span>${escapeHtml(it.label)}</span>
                <span class="mono-num" style="color:#34d399;">${(w * 100).toFixed(1)}% ${dStr}</span>
              </div>
              <div class="prob-bar-track" style="max-width:100%;margin-top:5px;">
                <div class="prob-bar-fill" style="width:${Math.min(100, w * 260)}%"></div>
              </div>
            </div>
          `;
        })
        .join("");
    }

    const evoEl = $("hkjc-evolutions-list");
    if (!evoEl) return;
    if (evolutions.length === 0) {
      evoEl.innerHTML = `<div class="empty-cell">結算任一場次後將自動記錄模型升級歷程</div>`;
      return;
    }
    evoEl.innerHTML = evolutions
      .map((ev) => {
        const pnl = Number(ev.race_pnl_hkd || 0);
        const flaws = ev.flaws_detected || [];
        return `
          <div class="pos-card" style="border-left:3px solid #10b981;">
            <div class="pos-header">
              <div>
                <span class="badge badge-accent mono-num">v${escapeHtml(ev.old_version)} → v${escapeHtml(ev.new_version)}</span>
                <strong style="font-size:12px;margin-left:5px;">R${ev.race_no} ${escapeHtml(ev.race_name)}</strong>
              </div>
              <span class="badge ${pnl >= 0 ? "badge-success" : "badge-danger"} mono-num">${fmtHkd(pnl, true)}</span>
            </div>
            <div class="mono-num" style="font-size:11px;color:#cbd5e1;margin-top:4px;">
              2.0損失 <strong style="color:#34d399;">${Number(ev.benter2_log_loss).toFixed(3)}</strong> vs 1.0損失 ${Number(ev.benter1_log_loss).toFixed(3)}
            </div>
            ${flaws
              .slice(0, 2)
              .map(
                (f) => `
                  <div style="font-size:11px;color:#fde68a;margin-top:4px;">
                    ⚡ <strong>${escapeHtml(f.category)}：</strong>${escapeHtml(f.action)}
                  </div>
                `
              )
              .join("")}
          </div>
        `;
      })
      .join("");
  }

  function buildRaceTelegramPreview(race, bets, mState, meeting) {
    if (!race) return "";
    const poolMap = {
      WIN: "獨贏",
      PLACE: "位置",
      QUINELLA: "連贏",
      PLACE_QUINELLA: "位置Q",
    };
    const raceBets = (bets || []).filter((b) => b.race_id === race.race_id);
    const lines = [
      `📅 ${meeting.date || ""}｜第 ${race.race_no} 場｜${race.race_name}`,
      `🎯 建議投注策略及注碼：`,
    ];
    if (raceBets.length > 0) {
      raceBets.forEach((b) => {
        const pName = poolMap[b.pool_type] || b.pool_type;
        const cleanSel = String(b.selection_label || "").split(" (")[0].trim();
        lines.push(`• ${pName}：${cleanSel} — 注碼 ${fmtHkd(b.stake_hkd)}`);
      });
    } else if ((race.exotic_recommendations || []).length > 0) {
      const ex = race.exotic_recommendations[0];
      const pName = poolMap[ex.pool_type] || ex.pool_label;
      lines.push(`• ${pName}：${ex.selection_label} — 注碼 ${fmtHkd(ex.rec_stake_hkd)}`);
    } else {
      lines.push(`• 本場無達標正期望值注項（建議觀望）`);
    }
    return lines.join("\n");
  }

  function renderTelegramSidebar(tg, tgLogs, selectedRace, bets) {
    if ($("hkjc-tg-count-badge")) $("hkjc-tg-count-badge").textContent = String(tgLogs.length);
    const pill = $("hkjc-tg-status-pill");
    if (pill) {
      if (tg.ready) {
        pill.className = "badge badge-success";
        pill.textContent = `✅ Telegram 已連線 (${tg.chat_id})`;
      } else {
        pill.className = "badge badge-warning";
        pill.textContent = "📲 待綁定 Bot Token & Chat ID (目前生成即時預覽)";
      }
    }

    if (!settingsFormDirty) {
      if ($("hkjc-tg-token-hint")) {
        $("hkjc-tg-token-hint").textContent = tg.has_token
          ? `✅ 已設定 Token (${tg.bot_token_masked})`
          : "尚未設定 Bot Token（可向 @BotFather 申請並填入）";
      }
      if ($("hkjc-tg-chatid") && !$("hkjc-tg-chatid").value && tg.chat_id) {
        $("hkjc-tg-chatid").value = tg.chat_id;
      }
      if ($("hkjc-tg-enabled")) {
        $("hkjc-tg-enabled").checked = tg.enabled !== false;
      }
    }

    if ($("hkjc-tg-preview-box") && hkjcState) {
      $("hkjc-tg-preview-box").textContent = buildRaceTelegramPreview(
        selectedRace,
        bets,
        hkjcState.model_state || {},
        hkjcState.meeting_info || {}
      );
    }

    const listEl = $("hkjc-tg-logs-list");
    if (!listEl) return;
    if (tgLogs.length === 0) {
      listEl.innerHTML = `<div class="empty-cell">點擊上方「📲 發送本場策略至 Telegram」或結算場次後，系統將自動推送最新投注策略並記錄於此</div>`;
      return;
    }

    listEl.innerHTML = tgLogs
      .map((l) => {
        const stBadge =
          l.status === "SENT"
            ? `<span class="badge badge-success">✅ 已推播至 Telegram</span>`
            : l.status === "QUEUED_PREVIEW"
            ? `<span class="badge badge-accent">📋 策略推播報文預覽</span>`
            : `<span class="badge badge-danger">⚠️ 發送失敗</span>`;
        const errBox =
          l.status === "ERROR" && l.error_detail
            ? `<div style="margin-top:6px;padding:6px 8px;border-radius:6px;background:rgba(244,63,94,0.14);border:1px solid rgba(244,63,94,0.4);color:#fda4af;font-size:11.5px;line-height:1.4;">${escapeHtml(l.error_detail)}</div>`
            : "";
        return `
          <div class="pos-card">
            <div class="pos-header">
              <span class="badge badge-outline">第 ${l.race_no} 場 · ${fmtTimeHkt(l.created_at)}</span>
              ${stBadge}
            </div>
            ${errBox}
            <pre class="mono-num" style="margin-top:6px;font-size:11px;color:#e2e8f0;white-space:pre-wrap;line-height:1.4;">${escapeHtml(l.message_text)}</pre>
          </div>
        `;
      })
      .join("");
  }

  // =====================================================================
  // EVENT BINDINGS & MODERN WEB GUIDANCE FALLBACKS
  // =====================================================================
  function bindEvents() {
    // 1. Scroll-state container query fallback (modern-web-guidance: content-first-noise-reduction)
    if (typeof CSS !== "undefined" && !CSS.supports("container-type", "scroll-state")) {
      const bar = document.querySelector(".top-bar");
      let lastY = window.scrollY;
      window.addEventListener(
        "scroll",
        () => {
          const y = Math.max(
            0,
            Math.min(window.scrollY, document.documentElement.scrollHeight - window.innerHeight)
          );
          if (y === lastY) return;
          bar?.classList.toggle("is-hidden", y > lastY && y > (bar.offsetHeight || 56));
          lastY = y;
        },
        { passive: true }
      );
    }

    // 2. Light-dismiss dialog fallback (modern-web-guidance: light-dismiss-a-dialog)
    const tgModal = $("hkjc-telegram-modal");
    if (typeof HTMLDialogElement !== "undefined" && !("closedBy" in HTMLDialogElement.prototype)) {
      tgModal?.addEventListener("click", (event) => {
        if (event.target !== tgModal) return;
        const rect = tgModal.getBoundingClientRect();
        const isDialogContent =
          rect.top <= event.clientY &&
          event.clientY <= rect.top + rect.height &&
          rect.left <= event.clientX &&
          event.clientX <= rect.left + rect.width;
        if (isDialogContent) return;
        tgModal.close();
      });
    }

    $("open-tg-modal-btn")?.addEventListener("click", () => tgModal?.showModal());
    $("close-tg-modal-btn")?.addEventListener("click", () => tgModal?.close());

    // 3. Right Sidebar 3-Tab Switcher (modern-web-guidance: tabs with hidden="until-found" & beforematch)
    const sideTabs = [$("side-tab-bets"), $("side-tab-model"), $("side-tab-tg")];
    const sidePanels = [$("side-panel-bets"), $("side-panel-model"), $("side-panel-tg")];

    function selectSideTab(targetTab) {
      sideTabs.forEach((tab, idx) => {
        if (!tab) return;
        const isSel = tab === targetTab;
        tab.setAttribute("aria-selected", isSel ? "true" : "false");
        tab.toggleAttribute("focusgroupstart", isSel);
        const panel = sidePanels[idx];
        if (panel) {
          if (isSel) {
            panel.removeAttribute("hidden");
            panel.setAttribute("tabindex", "0");
          } else {
            panel.setAttribute("hidden", "until-found");
            panel.removeAttribute("tabindex");
          }
        }
      });
    }

    sideTabs.forEach((tab) => {
      tab?.addEventListener("click", () => selectSideTab(tab));
    });
    sidePanels.forEach((panel, idx) => {
      panel?.addEventListener("beforematch", () => selectSideTab(sideTabs[idx]));
    });

    // 4. Ledger Filter Chips
    const setFilterMode = (mode) => {
      betsFilterMode = mode;
      $("filter-bets-all")?.classList.toggle("active", mode === "all");
      $("filter-bets-race")?.classList.toggle("active", mode === "race");
      $("filter-bets-settled")?.classList.toggle("active", mode === "settled");
      if (hkjcState) renderBetsSidebar(hkjcState.paper_bets || [], selectedRaceId);
    };
    $("filter-bets-all")?.addEventListener("click", () => setFilterMode("all"));
    $("filter-bets-race")?.addEventListener("click", () => setFilterMode("race"));
    $("filter-bets-settled")?.addEventListener("click", () => setFilterMode("settled"));

    // 5. Race Selector Tabs (R1..R11)
    $("hkjc-race-tablist")?.addEventListener("click", (e) => {
      const btn = e.target.closest(".hkjc-race-tab");
      if (!btn) return;
      selectedRaceId = btn.dataset.raceId;
      if (hkjcState) {
        renderHkjc(hkjcState);
        const activeTab = $("hkjc-race-tablist")?.querySelector(`[data-race-id="${selectedRaceId}"]`);
        activeTab?.scrollIntoView({ block: "nearest", inline: "nearest", container: "nearest" });
      }
    });

    // 6. Settle Next Race & Evolve
    $("hkjc-settle-next-btn")?.addEventListener("click", async () => {
      const btn = $("hkjc-settle-next-btn");
      if (btn) btn.disabled = true;
      try {
        const res = await apiPost("/api/hkjc/settle", {});
        const settledId = res.result?.settled_race?.race_id;
        if (settledId) selectedRaceId = settledId;
        renderHkjc(res.hkjc);
        const evo = res.result?.evolution;
        showToast(
          `🏁 第 ${evo?.race_no} 場完賽結算 (損益 ${fmtHkd(evo?.race_pnl_hkd, true)})！模型已自動檢討並升級至 v${evo?.new_version}`
        );
      } catch (err) {
        showToast(`❌ ${err.message}`);
      } finally {
        if (btn) btn.disabled = false;
      }
    });

    // 7. Settle Current Race & Evolve
    $("hkjc-settle-current-btn")?.addEventListener("click", async () => {
      const btn = $("hkjc-settle-current-btn");
      if (btn) btn.disabled = true;
      try {
        const res = await apiPost("/api/hkjc/settle", { race_id: selectedRaceId });
        const settledId = res.result?.settled_race?.race_id;
        if (settledId) selectedRaceId = settledId;
        renderHkjc(res.hkjc);
        const evo = res.result?.evolution;
        showToast(`🏁 第 ${evo?.race_no} 場已對比官方賽果結算並將模型升級至 v${evo?.new_version}`);
      } catch (err) {
        showToast(`❌ ${err.message}`);
      } finally {
        if (btn) btn.disabled = false;
      }
    });

    // 8. Settle All Remaining Races & Evolve Sequentially
    $("hkjc-settle-all-btn")?.addEventListener("click", async () => {
      const btn = $("hkjc-settle-all-btn");
      if (btn) btn.disabled = true;
      try {
        const res = await apiPost("/api/hkjc/settle-all", {});
        renderHkjc(res.hkjc);
        const p = res.hkjc?.portfolio || {};
        showToast(
          `⚡ 全日 ${res.settled_count} 場逐場自進化完成！累計實戰損益 ${fmtHkd(p.realized_pnl_hkd, true)} (ROI ${p.roi_pct >= 0 ? "+" : ""}${p.roi_pct}%)`
        );
      } catch (err) {
        showToast(`❌ ${err.message}`);
      } finally {
        if (btn) btn.disabled = false;
      }
    });

    // 9. Manual Live Sync with HKJC GraphQL
    $("hkjc-sync-btn")?.addEventListener("click", async () => {
      const btn = $("hkjc-sync-btn");
      if (btn) btn.disabled = true;
      try {
        const res = await apiPost("/api/hkjc/sync", {});
        renderHkjc(res.hkjc);
        showToast("🔄 已與香港賽馬會官方 GraphQL 同步最新實時賠率與賽事狀態！");
      } catch (err) {
        showToast(`❌ 同步失敗：${err.message}`);
      } finally {
        if (btn) btn.disabled = false;
      }
    });

    // 10. Auto Deploy Bets for Current Open Race
    $("hkjc-auto-bet-btn")?.addEventListener("click", async () => {
      try {
        const res = await apiPost("/api/hkjc/auto-bet", {});
        renderHkjc(res.hkjc);
        showToast(`🎯 已根據最新進化權重下達 ${res.placed_count} 筆 +EV 凱利策略注單！`);
      } catch (err) {
        showToast(`❌ ${err.message}`);
      }
    });

    // 11. Reset Race Day (Keep Evolved Model vs Full Reset)
    $("hkjc-reset-day-btn")?.addEventListener("click", async () => {
      try {
        const res = await apiPost("/api/hkjc/reset", { reset_model_weights: false });
        selectedRaceId = res.hkjc?.next_open_race_id || null;
        renderHkjc(res.hkjc);
        showToast(`🔄 已重置賽日進度（保留已進化模型 v${res.hkjc?.model_state?.version} 權重），可從第 1 場重新驗證！`);
      } catch (err) {
        showToast(`❌ ${err.message}`);
      }
    });

    $("hkjc-reset-all-btn")?.addEventListener("click", async () => {
      try {
        const res = await apiPost("/api/hkjc/reset", { reset_model_weights: true });
        selectedRaceId = res.hkjc?.next_open_race_id || null;
        renderHkjc(res.hkjc);
        showToast("🔄 已完全重置注單與模型權重回初始 v2.0.0！");
      } catch (err) {
        showToast(`❌ ${err.message}`);
      }
    });

    // 12. Manual Bet Placement Delegation
    const onBetClick = async (e) => {
      const btn = e.target.closest(".hkjc-bet-btn");
      if (!btn) return;
      btn.disabled = true;
      try {
        const res = await apiPost("/api/hkjc/bet", {
          race_id: btn.dataset.raceId,
          pool_type: btn.dataset.poolType,
          selection_key: btn.dataset.selectionKey,
          stake_hkd: Number(btn.dataset.stake || 300),
        });
        renderHkjc(res.hkjc);
        showToast(`🏇 已新增策略注單：${res.bet?.selection_label} (${fmtHkd(res.bet?.stake_hkd)})`);
      } catch (err) {
        showToast(`❌ 下單失敗：${err.message}`);
      } finally {
        btn.disabled = false;
      }
    };
    $("hkjc-runners-tbody")?.addEventListener("click", onBetClick);
    $("hkjc-exotic-grid")?.addEventListener("click", onBetClick);

    // 13. Telegram Binding & Immediate Strategy Push Handlers
    const pushCurrentStrategyToTg = async () => {
      try {
        const res = await apiPost("/api/hkjc/telegram/push", { race_id: selectedRaceId });
        renderHkjc(res.hkjc);
        selectSideTab($("side-tab-tg"));
        showToast(res.push?.message || "📲 已觸發 Telegram 策略推播！");
      } catch (err) {
        showToast(`❌ 推播失敗：${err.message}`);
      }
    };

    $("hkjc-push-tg-btn")?.addEventListener("click", pushCurrentStrategyToTg);
    $("side-push-tg-btn")?.addEventListener("click", pushCurrentStrategyToTg);

    $("hkjc-tg-form")?.addEventListener("input", () => {
      settingsFormDirty = true;
    });

    const saveTgConfig = async (andPush = false) => {
      try {
        const payload = {
          bot_token: $("hkjc-tg-token")?.value || "",
          chat_id: $("hkjc-tg-chatid")?.value || "",
          enabled: Boolean($("hkjc-tg-enabled")?.checked),
        };
        const res = await apiPost("/api/hkjc/telegram/config", payload);
        if ($("hkjc-tg-token")) $("hkjc-tg-token").value = "";
        settingsFormDirty = false;
        renderHkjc(res.hkjc);
        if (andPush) {
          await pushCurrentStrategyToTg();
          tgModal?.close();
        } else {
          showToast("✅ Telegram 推播綁定已儲存！未來每場完賽升級與賽前策略將自動發送。");
        }
      } catch (err) {
        showToast(`❌ 儲存失敗：${err.message}`);
      }
    };

    $("hkjc-tg-form")?.addEventListener("submit", async (e) => {
      e.preventDefault();
      await saveTgConfig(false);
    });

    $("test-tg-push-btn")?.addEventListener("click", async () => {
      await saveTgConfig(true);
    });
  }

  function connectWebSocket() {
    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    ws = new WebSocket(`${proto}//${window.location.host}/ws`);
    ws.onopen = () => {
      $("ws-dot")?.classList.remove("offline");
    };
    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        if (msg.type === "STATE_UPDATE" && msg.data?.hkjc) {
          renderHkjc(msg.data.hkjc);
        }
      } catch (err) {
        console.error("WS parse error", err);
      }
    };
    ws.onclose = () => {
      $("ws-dot")?.classList.add("offline");
      setTimeout(connectWebSocket, 3000);
    };
  }

  async function init() {
    updateHktClock();
    setInterval(updateHktClock, 1000);
    bindEvents();
    try {
      const resp = await fetch("/api/hkjc/state");
      if (resp.ok) {
        const data = await resp.json();
        if (data.hkjc) renderHkjc(data.hkjc);
      }
    } catch (err) {
      console.error("Initial HKJC state fetch error", err);
    }
    connectWebSocket();
  }

  document.addEventListener("DOMContentLoaded", init);
})();
