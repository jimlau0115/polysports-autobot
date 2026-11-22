(() => {
  let appState = null;
  let activeHkTab = "overview"; // "overview" or "records"
  let activeLedgerMode = "PAPER"; // "PAPER" (Trial Run) or "LIVE" (Password-Protected)
  let adminToken = localStorage.getItem("poly_admin_token") || "";
  let selectedSport = "ALL";
  let onlyAbove70 = false;
  let searchQuery = "";
  let formDirty = false;
  let lpFormDirty = false;
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

  function fmtUsd(val, showSign = false) {
    const n = Number(val || 0);
    const abs = Math.abs(n).toLocaleString("en-US", {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    });
    if (showSign) {
      return n >= 0 ? `+$${abs}` : `-$${abs}`;
    }
    return n < 0 ? `-$${abs}` : `$${abs}`;
  }

  function fmtTime(isoStr) {
    if (!isoStr) return "--:--:--";
    try {
      const d = new Date(isoStr);
      return d.toLocaleTimeString("zh-HK", { timeZone: "Asia/Hong_Kong", hour12: false });
    } catch {
      return isoStr;
    }
  }

  function fmtDateTimeHkt(isoStr) {
    if (!isoStr) return "—";
    try {
      const d = new Date(isoStr);
      if (Number.isNaN(d.getTime())) return isoStr;
      return new Intl.DateTimeFormat("zh-HK", {
        timeZone: "Asia/Hong_Kong",
        month: "numeric",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
        hourCycle: "h23",
      }).format(d);
    } catch {
      return isoStr;
    }
  }

  function fmtEndCountdown(isoStr) {
    if (!isoStr) return "結束時間：未定";
    try {
      const d = new Date(isoStr);
      if (Number.isNaN(d.getTime())) return `結束時間：${isoStr}`;
      const hktStr = fmtDateTimeHkt(isoStr);
      const diffMs = d.getTime() - Date.now();
      const diffMin = Math.round(diffMs / 60000);
      if (diffMin <= 0) {
        return `🔴 進行中 / 即將結算 (${hktStr} HKT)`;
      }
      if (diffMin < 60) {
        return `⏱️ ${diffMin} 分鐘後結束 (${hktStr} HKT)`;
      }
      if (diffMin < 1440) {
        const hrs = Math.floor(diffMin / 60);
        const mins = diffMin % 60;
        return `⏱️ ${hrs} 小時 ${mins} 分後結束 (${hktStr} HKT)`;
      }
      const days = Math.floor(diffMin / 1440);
      const remHrs = Math.floor((diffMin % 1440) / 60);
      return `⏱️ ${days} 天 ${remHrs} 小時後結束 (${hktStr} HKT)`;
    } catch {
      return `結束時間：${isoStr}`;
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
    setTimeout(() => {
      el.remove();
    }, 4200);
  }

  function getAuthHeaders() {
    const headers = { "Content-Type": "application/json" };
    if (adminToken) {
      headers["X-Admin-Token"] = adminToken;
    }
    return headers;
  }

  function openAuthModal() {
    const isAdmin = Boolean(appState?.auth?.is_admin);
    if (isAdmin) {
      $("auth-login-form")?.classList.add("hidden");
      $("auth-manage-section")?.classList.remove("hidden");
    } else {
      $("auth-login-form")?.classList.remove("hidden");
      $("auth-manage-section")?.classList.add("hidden");
      setTimeout(() => $("auth-password-input")?.focus(), 50);
    }
    $("auth-modal")?.showModal();
  }

  async function apiPost(url, payload = {}) {
    const resp = await fetch(url, {
      method: "POST",
      headers: getAuthHeaders(),
      body: JSON.stringify(payload),
    });
    const data = await resp.json();
    if (!resp.ok || data.ok === false) {
      if (data.auth_required || resp.status === 401) {
        openAuthModal();
      }
      throw new Error(data.error || data.message || `Request failed (${resp.status})`);
    }
    return data;
  }

  function renderState(state) {
    if (!state) return;
    appState = state;
    const { engine, config, clob_status, hk_temp } = state;
    const isAdmin = Boolean(state.auth?.is_admin);

    // If user is not authenticated as admin, force activeLedgerMode to PAPER
    if (!isAdmin) {
      activeLedgerMode = "PAPER";
    }

    const isLiveView = isAdmin && activeLedgerMode === "LIVE" && Boolean(state.live_data);
    const activeData = isLiveView
      ? state.live_data
      : state.paper_data || {
          portfolio: state.portfolio,
          open_positions: state.open_positions,
          closed_positions: state.closed_positions,
          logs: state.logs,
        };
    const portfolio = activeData.portfolio || state.portfolio || {};
    const hkSum = isLiveView
      ? hk_temp?.summaryByMode?.LIVE || {}
      : hk_temp?.summaryByMode?.PAPER || hk_temp?.summary || {};
    const hkTrades = isLiveView
      ? hk_temp?.tradesByMode?.LIVE || []
      : hk_temp?.tradesByMode?.PAPER || hk_temp?.trades || [];
    const hkEng = hk_temp?.engine || {};

    // 1. Topbar controls & Auth Status
    const wsDot = $("ws-dot");
    if (wsDot) wsDot.classList.remove("offline");
    const statusText = $("scanner-status-text");
    if (statusText) {
      statusText.textContent = `掃描 #${engine.scan_count} (${engine.last_scan_duration_ms}ms) · 每 ${config.scan_interval_sec} 秒`;
    }

    const dsSelect = $("data-source-select");
    if (dsSelect && document.activeElement !== dsSelect) {
      dsSelect.value = config.data_source_mode || "AUTO";
    }

    // Auth button & Lock icon
    const authBtn = $("open-auth-modal-btn");
    const lockIcon = $("mode-live-lock-icon");
    if (isAdmin) {
      if (authBtn) {
        authBtn.textContent = "🔓 實盤已解鎖";
        authBtn.className = "btn btn-secondary";
      }
      if (lockIcon) lockIcon.textContent = "⚡";
    } else {
      if (authBtn) {
        authBtn.textContent = "🔒 實盤登入";
        authBtn.className = "btn btn-outline";
      }
      if (lockIcon) lockIcon.textContent = "🔒";
    }

    // Ledger / Trading Mode Buttons & Banner
    const paperBtn = $("mode-paper-btn");
    const liveBtn = $("mode-live-btn");
    const bannerEl = $("active-ledger-banner");
    const bannerPill = $("ledger-mode-pill");
    const bannerDesc = $("ledger-mode-desc");
    const botExecBadge = $("bot-exec-mode-badge");

    if (isLiveView) {
      paperBtn.className = "seg-btn";
      liveBtn.className = "seg-btn active active-live";
      if (bannerEl) bannerEl.className = "ledger-banner ledger-banner-live";
      if (bannerPill) {
        bannerPill.textContent = "⚡ 實盤專區 (LIVE 密碼保護 · 獨立帳本)";
        bannerPill.className = "badge badge-danger";
      }
      if (bannerDesc) {
        bannerDesc.textContent = clob_status?.ready_for_live
          ? "目前顯示「實盤 (LIVE)」獨立持倉、勝率、損益與日誌（與模擬盤 Trial Run 數據 100% 隔離）。"
          : "目前顯示「實盤 (LIVE)」獨立帳本（勝率、持倉與模擬盤完全隔離）。如需開啟實盤自動下單，請點擊右上角「🔐 CLOB 錢包設定」綁定私鑰。";
      }
    } else {
      paperBtn.className = "seg-btn active";
      liveBtn.className = "seg-btn";
      if (bannerEl) bannerEl.className = "ledger-banner ledger-banner-paper";
      if (bannerPill) {
        bannerPill.textContent = "🧪 模擬盤 (Trial Run 策略制定與測試區)";
        bannerPill.className = "badge badge-accent";
      }
      if (bannerDesc) {
        bannerDesc.textContent = "目前顯示「模擬盤 (PAPER)」獨立數據，供策略制定與 Trial Run 測試；實盤需密碼登入且勝率/注項完全分開。";
      }
    }

    if (botExecBadge) {
      const execMode = config.trading_mode === "LIVE" ? "⚡ 實盤 CLOB (LIVE)" : "🧪 模擬盤 (PAPER)";
      botExecBadge.textContent = `🤖 機器人自動下單目標：${execMode}`;
    }

    // Sports Bot Toggle Button
    const botBtn = $("bot-toggle-btn");
    const botLabel = $("bot-toggle-label");
    if (botBtn && botLabel) {
      const minPct = Math.round((config.min_win_prob || 0.7) * 100);
      if (config.bot_enabled) {
        botBtn.className = "btn btn-bot-active";
        botLabel.textContent = `運動 ${minPct}%+ 自動：運行中`;
      } else {
        botBtn.className = "btn btn-bot-paused";
        botLabel.textContent = `運動 ${minPct}%+ 自動：已暫停`;
      }
    }

    // 2. Unified 6-Card KPI Strip (Isolated by PAPER vs LIVE Ledger!)
    const hkExposure = Number(hkSum.exposureUsdc || 0);
    const hkUnrealized = Number(hkSum.unrealizedPnl || 0);
    const hkRealized = Number(hkSum.realizedPnl || 0);
    const combinedOpenVal = Number(portfolio.open_market_value_usdc || 0) + hkExposure + hkUnrealized;
    const combinedInvested = Number(portfolio.open_invested_usdc || 0) + hkExposure;
    const combinedUnrealized = Number(portfolio.unrealized_pnl_usdc || 0) + hkUnrealized;
    const combinedRealized = Number(portfolio.realized_pnl_usdc || 0) + hkRealized;
    const combinedTotalPnl = combinedUnrealized + combinedRealized;

    if (isLiveView) {
      if ($("kpi-equity-label")) $("kpi-equity-label").textContent = "⚡ 實盤持倉總市值 (Live 獨立帳本)";
      if ($("reset-paper-btn")) $("reset-paper-btn").classList.add("hidden");
      $("kpi-equity").textContent = fmtUsd(combinedOpenVal);
      if ($("kpi-equity-sub")) {
        $("kpi-equity-sub").innerHTML = `實盤進行中本金 <strong id="kpi-cash">${fmtUsd(combinedInvested)}</strong> · 實盤市值 <strong id="kpi-open-value">${fmtUsd(combinedOpenVal)}</strong>`;
      }
      if ($("kpi-pnl-label")) $("kpi-pnl-label").textContent = "📈 實盤累計總損益 (Live PnL)";
      if ($("kpi-winrate-label")) $("kpi-winrate-label").textContent = "📌 實盤持倉 & 實盤勝率 (獨立)";
    } else {
      const combinedEquity = Number(portfolio.paper_cash_balance || 0) + combinedOpenVal;
      if ($("kpi-equity-label")) $("kpi-equity-label").textContent = "🧪 模擬盤總淨值 (Trial Run)";
      if ($("reset-paper-btn")) $("reset-paper-btn").classList.remove("hidden");
      $("kpi-equity").textContent = fmtUsd(combinedEquity);
      if ($("kpi-equity-sub")) {
        $("kpi-equity-sub").innerHTML = `模擬可用現金 <strong id="kpi-cash">${fmtUsd(portfolio.paper_cash_balance)}</strong> · 持倉市值 <strong id="kpi-open-value">${fmtUsd(combinedOpenVal)}</strong>`;
      }
      if ($("kpi-pnl-label")) $("kpi-pnl-label").textContent = "📈 模擬盤累計損益 (Paper PnL)";
      if ($("kpi-winrate-label")) $("kpi-winrate-label").textContent = "📌 模擬盤持倉 & 模擬勝率";
    }

    const totalPnlEl = $("kpi-total-pnl");
    totalPnlEl.textContent = fmtUsd(combinedTotalPnl, true);
    totalPnlEl.className = `kpi-value ${combinedTotalPnl >= 0 ? "pnl-pos" : "pnl-neg"}`;

    const roiBase = isLiveView
      ? Math.max(1, combinedInvested || Number(config.daily_budget_usdc || 300))
      : Number(portfolio.paper_initial_balance || 1000);
    const combinedRoiPct = roiBase > 0 ? (combinedTotalPnl / roiBase) * 100 : 0;

    const roiBadge = $("kpi-roi-badge");
    const roiSign = combinedRoiPct >= 0 ? "+" : "";
    roiBadge.textContent = `${roiSign}${combinedRoiPct.toFixed(2)}%`;
    roiBadge.className = `badge ${combinedRoiPct >= 0 ? "badge-success" : "badge-danger"}`;

    $("kpi-unrealized").textContent = fmtUsd(combinedUnrealized, true);
    $("kpi-realized").textContent = fmtUsd(combinedRealized, true);

    const sportsOpenCount = Number(portfolio.open_positions_count || 0);
    const hkOpenCount = Number(hkSum.openCount || 0);
    const totalOpenCount = sportsOpenCount + hkOpenCount;

    $("kpi-positions-count").textContent = `${totalOpenCount} 筆進行中`;
    if ($("kpi-sports-open-count")) $("kpi-sports-open-count").textContent = String(sportsOpenCount);
    if ($("hk-open-count")) $("hk-open-count").textContent = String(hkOpenCount);
    $("kpi-invested").textContent = fmtUsd(combinedInvested);
    $("kpi-winrate-tag").textContent =
      portfolio.closed_positions_count > 0
        ? `勝率 ${portfolio.win_rate_pct}% (${portfolio.wins}勝${portfolio.losses}敗)`
        : `已結算 ${portfolio.wins || 0}勝${portfolio.losses || 0}敗`;

    const budgetPct =
      portfolio.daily_budget_usdc > 0
        ? Math.min(100, Math.round((portfolio.today_spend_usdc / portfolio.daily_budget_usdc) * 100))
        : 0;
    if ($("kpi-daily-spend")) {
      $("kpi-daily-spend").textContent = `${fmtUsd(portfolio.today_spend_usdc)} / $${Number(portfolio.daily_budget_usdc || 300).toFixed(0)}`;
    }
    if ($("kpi-budget-bar")) {
      $("kpi-budget-bar").style.width = `${budgetPct}%`;
    }

    const srcBadge = $("kpi-source-badge");
    if (srcBadge) {
      if (engine.active_data_source === "LIVE_GAMMA_API") {
        srcBadge.textContent = "LIVE API";
        srcBadge.className = "badge badge-success";
      } else {
        srcBadge.textContent = "模擬流";
        srcBadge.className = "badge badge-accent";
      }
    }

    $("kpi-markets-summary").textContent = `${engine.above_threshold_count} / ${engine.total_sports_markets} 場 >= ${Math.round(config.min_win_prob * 100)}%`;
    $("last-scan-timestamp").textContent = `最後更新：${fmtTime(engine.last_scan_at)}`;

    // Right sidebar ledger indicators
    const posLedgerBadge = $("positions-ledger-badge");
    if (posLedgerBadge) {
      posLedgerBadge.textContent = isLiveView ? "⚡ 實盤獨立帳本" : "🧪 模擬盤帳本";
      posLedgerBadge.className = `badge ${isLiveView ? "badge-danger" : "badge-accent"}`;
    }
    const logsTitle = $("logs-panel-title");
    if (logsTitle) {
      logsTitle.textContent = isLiveView
        ? "⚡ 24/7 雙引擎自動交易日誌 (實盤 LIVE 帳本)"
        : "🧪 24/7 雙引擎自動交易日誌 (模擬盤 PAPER 帳本)";
    }

    // 3. Unified Strategy Form (Sports & HK Temp)
    if (!formDirty) {
      $("cfg-min-prob").value = (config.min_win_prob * 100).toFixed(1).replace(/\.0$/, "");
      $("cfg-max-prob").value = (config.max_win_prob * 100).toFixed(1).replace(/\.0$/, "");
      $("cfg-bet-amount").value = config.bet_amount_usdc;
      $("cfg-daily-budget").value = config.daily_budget_usdc;
      $("cfg-min-liquidity").value = config.min_liquidity_usdc;
      $("cfg-max-spread").value = (config.max_spread * 100).toFixed(1);
      $("cfg-tp-enabled").checked = Boolean(config.auto_take_profit_enabled);
      $("cfg-tp-prob").value = (config.take_profit_prob * 100).toFixed(1).replace(/\.0$/, "");
      $("cfg-sl-enabled").checked = Boolean(config.auto_stop_loss_enabled);
      if ($("cfg-sl-minutes")) {
        $("cfg-sl-minutes").value = config.stop_loss_minutes_before_end ?? 30;
      }
      if ($("cfg-exclude-upset")) {
        $("cfg-exclude-upset").checked = config.exclude_high_upset_sports !== false;
      }
      $("cfg-sl-prob").value = (config.stop_loss_prob * 100).toFixed(1).replace(/\.0$/, "");
      $("cfg-scan-interval").value = config.scan_interval_sec;
      $("cfg-max-positions").value = config.max_open_positions;

      if (hk_temp) {
        $("hk-stake-input").value = hkEng.stakeUsdc ?? 10;
        $("hk-pricemin-input").value = Math.round((hkEng.priceMin ?? 0.70) * 100);
        $("hk-gatehour-input").value = hk_temp.gateHour ?? 11;
      }
    }

    // 4. CLOB status inside modal
    if (clob_status) {
      $("clob-pk-status").textContent = clob_status.has_private_key
        ? `✅ 已設定私鑰 (${clob_status.private_key_masked})`
        : "目前狀態：尚未設定私鑰 (僅限 Paper 模擬交易)";
      if (!$("clob-funder-address").value && clob_status.funder_address) {
        $("clob-funder-address").value = clob_status.funder_address;
      }
      $("clob-sig-type").value = String(clob_status.signature_type ?? 1);
    }

    // 5. Render HK Temperature Monitor, Sports Markets Table, Positions, Logs, LP Module, and Live Strategy Descriptions
    if (hk_temp) {
      renderHkTempModule(hk_temp);
    }
    if (state.lp_mm) {
      renderLpModule(state.lp_mm, isLiveView);
    }
    renderMarketsTable(state.markets || [], config);
    renderPositions(activeData.open_positions || [], activeData.closed_positions || [], hkTrades);
    renderLogs(activeData.logs || []);
    updateStrategyDescriptionsFromInputs();
    updateLpDescriptionsFromInputs();
  }

  function updateStrategyDescriptionsFromInputs() {
    const minProbStr = ($("cfg-min-prob")?.value || "70").trim();
    const maxProbStr = ($("cfg-max-prob")?.value || "80").trim();
    const betAmt = Number($("cfg-bet-amount")?.value || 20);
    const dailyBudget = Number($("cfg-daily-budget")?.value || 300);
    const minLiq = Number($("cfg-min-liquidity")?.value || 3000);
    const maxSpreadStr = ($("cfg-max-spread")?.value || "4.0").trim();
    const tpEnabled = Boolean($("cfg-tp-enabled")?.checked);
    const tpProbStr = ($("cfg-tp-prob")?.value || "93").trim();
    const slEnabled = Boolean($("cfg-sl-enabled")?.checked);
    const slMinutes = Number($("cfg-sl-minutes")?.value ?? 30);
    const slProbStr = ($("cfg-sl-prob")?.value || "55").trim();
    const excludeUpset = $("cfg-exclude-upset") ? Boolean($("cfg-exclude-upset").checked) : true;

    const hkPriceMinStr = ($("hk-pricemin-input")?.value || "70").trim();
    const hkStake = Number($("hk-stake-input")?.value || 10);
    const hkGateHour = Number($("hk-gatehour-input")?.value ?? 11);
    const hkGateStr = `${String(hkGateHour).padStart(2, "0")}:00`;

    const scanSec = Number($("cfg-scan-interval")?.value || 5);
    const maxPos = Number($("cfg-max-positions")?.value || 15);
    const maxBetsPerDay = Math.max(1, Math.floor(dailyBudget / Math.max(1, betAmt)));

    const slConditionText = !slEnabled
      ? "自動停損已關閉（跌破不自動賣出）"
      : slMinutes > 0
      ? `賽事剩餘 <= ${slMinutes} 分鐘且勝率 <= ${slProbStr}% 才自動停損`
      : `不限剩餘時間，勝率 <= ${slProbStr}% 即自動停損`;

    // 1. Update all 12 <small> field descriptions directly below inputs
    if ($("desc-min-prob")) {
      $("desc-min-prob").textContent = `當運動選項勝率 >= ${minProbStr}% 時立即觸發買入`;
    }
    if ($("desc-max-prob")) {
      $("desc-max-prob").textContent = `高於 ${maxProbStr}% 不追高（鎖定 ${minProbStr}% ~ ${maxProbStr}% +EV 黃金賠率區）`;
    }
    if ($("desc-bet-amount")) {
      $("desc-bet-amount").textContent = `運動達標時，每場自動買入 $${betAmt.toLocaleString()} USDC`;
    }
    if ($("desc-daily-budget")) {
      $("desc-daily-budget").textContent = `運動單日上限 $${dailyBudget.toLocaleString()} USDC（約可投 ${maxBetsPerDay} 場）`;
    }
    if ($("desc-min-liquidity")) {
      $("desc-min-liquidity").textContent = `僅交易深度 >= $${minLiq.toLocaleString()} USDC 的主流賽事`;
    }
    if ($("desc-max-spread")) {
      $("desc-max-spread").textContent = `買賣價差 > ${maxSpreadStr}% 時自動跳過不買`;
    }
    if ($("desc-tp-prob")) {
      $("desc-tp-prob").textContent = tpEnabled
        ? `升至 ${tpProbStr}% 自動提前鎖利（防終場絕殺翻盤）`
        : "自動停利已關閉（持有至結算或手動平倉）";
    }
    if ($("desc-sl-minutes")) {
      $("desc-sl-minutes").textContent =
        slMinutes > 0
          ? `賽事結束前 ${slMinutes} 分鐘內才允許停損`
          : "已設為 0 分鐘（不限時間皆可觸發停損）";
    }
    if ($("desc-sl-prob")) {
      $("desc-sl-prob").textContent = slConditionText;
    }
    if ($("desc-hk-pricemin")) {
      $("desc-hk-pricemin").textContent = `溫度區間達 ${hkPriceMinStr}% 立即買入（無上限）`;
    }
    if ($("desc-hk-stake")) {
      $("desc-hk-stake").textContent = `每日首個達標溫度選項買入 $${hkStake.toLocaleString()} USDC`;
    }
    if ($("desc-hk-gatehour")) {
      $("desc-hk-gatehour").textContent = `香港時間 ${hkGateStr} 後開始自動監測`;
    }
    if ($("desc-scan-interval")) {
      $("desc-scan-interval").textContent = `每 ${scanSec} 秒自動掃描雙引擎盤口`;
    }
    if ($("desc-scan-positions")) {
      $("desc-scan-positions").textContent = `每 ${scanSec} 秒掃描一次 · 最多同時持有 ${maxPos} 倉`;
    }

    // 2. Update Strategy Panel Header Title, Subtitle & Bottom Summary Bar
    if ($("strategy-panel-title")) {
      $("strategy-panel-title").textContent = `🎯 雙引擎自動投注策略與風控設定`;
    }
    if ($("strategy-panel-hint")) {
      $("strategy-panel-hint").textContent = `運動 ${minProbStr}%~${maxProbStr}% ($${betAmt}) · 氣溫 ${hkPriceMinStr}%+ ($${hkStake})`;
    }
    if ($("strategy-live-summary")) {
      const tpText = tpEnabled ? `升至 ${tpProbStr}% 提前止盈鎖利` : "停利關閉";
      const upsetText = excludeUpset ? "、已排除網球挑戰賽/電競高爆冷盤" : "";
      const slSummary = !slEnabled
        ? "停損關閉"
        : slMinutes > 0
        ? `剩餘 <= ${slMinutes} 分鐘且勝率 <= ${slProbStr}% 自動停損`
        : `勝率 <= ${slProbStr}% 自動停損`;
      $("strategy-live-summary").innerHTML =
        `📋 <strong>目前生效 +EV 策略：</strong>` +
        `【🏟️ 運動賽事】僅買入勝率介於 <strong>${escapeHtml(minProbStr)}% ~ ${escapeHtml(maxProbStr)}%</strong> 黃金賠率區` +
        `（深度 &gt;= <strong>$${minLiq.toLocaleString()}</strong>、點差 &lt;= <strong>${escapeHtml(maxSpreadStr)}%</strong>${upsetText}），` +
        `每場投入 <strong>$${betAmt.toLocaleString()} USDC</strong>` +
        `（單日上限 <strong>$${dailyBudget.toLocaleString()}</strong>，<strong>${escapeHtml(tpText)}</strong> / <strong>${escapeHtml(slSummary)}</strong>）；` +
        `【🌡️ 香港氣溫】於 <strong>${escapeHtml(hkGateStr)} HKT</strong> 後達 <strong>${escapeHtml(hkPriceMinStr)}%+</strong> 自動買入 <strong>$${hkStake.toLocaleString()} USDC</strong>` +
        `（每 <strong>${scanSec} 秒</strong> 掃描，上限 <strong>${maxPos} 倉</strong>）。`;
    }

    // 3. Update Topbar, KPI labels, Buttons & Monitor Headers below Strategy Panel
    if ($("header-brand-badge")) {
      $("header-brand-badge").textContent = `🔀 雙引擎整合總覽 (運動 ${minProbStr}%+ · 氣溫 ${hkPriceMinStr}%+)`;
    }
    if ($("view-hkonly-label")) {
      $("view-hkonly-label").textContent = `🌡️ 專注香港氣溫 (${hkPriceMinStr}%+)`;
    }
    if ($("view-sportsonly-label")) {
      $("view-sportsonly-label").textContent = `🏟️ 專注運動賽事 (${minProbStr}%+)`;
    }
    if ($("kpi-sports-radar-label")) {
      $("kpi-sports-radar-label").textContent = `🏟️ 運動賽事雷達 (>= ${minProbStr}%)`;
    }
    if ($("kpi-hk-gate-label")) {
      $("kpi-hk-gate-label").textContent = `⚡ 香港氣溫 ${hkPriceMinStr}%+ 自動閘門`;
    }
    if ($("hk-gate-countdown")) {
      $("hk-gate-countdown").textContent = `${hkGateStr} HKT 開閘 · 門檻 >= ${hkPriceMinStr}% 買入 $${hkStake}`;
    }
    if ($("sim-breakout-btn")) {
      $("sim-breakout-btn").textContent = `📈 測試 ${minProbStr}%+ 觸發`;
    }
    if ($("bot-toggle-label") && appState?.config) {
      $("bot-toggle-label").textContent = appState.config.bot_enabled
        ? `運動 ${minProbStr}%+ 自動：運行中`
        : `運動 ${minProbStr}%+ 自動：已暫停`;
    }
    if ($("hk-auto-toggle-btn") && appState?.hk_temp?.engine) {
      $("hk-auto-toggle-btn").innerHTML = appState.hk_temp.engine.autoEnabled
        ? `<span>🌡️ 氣溫 ${hkPriceMinStr}%+ 自動：運行中</span>`
        : `<span>⏸️ 氣溫 ${hkPriceMinStr}%+ 自動：已暫停</span>`;
    }
    if ($("hk-subnav-overview-btn")) {
      $("hk-subnav-overview-btn").textContent = `📊 今日溫度區間 (${hkPriceMinStr}%+ 監測)`;
    }
    if ($("hk-rule-prob-text")) {
      $("hk-rule-prob-text").textContent = `${hkPriceMinStr}%`;
    }
    if ($("hk-phase-message")) {
      $("hk-phase-message").textContent = `香港時間 ${hkGateStr} 之後，監測當日所有「是」選項；價格達到 ${hkPriceMinStr}% 或以上時立即以 $${hkStake} USDC 自動投注（無價格上限）。`;
    }
    if ($("sports-monitor-title")) {
      $("sports-monitor-title").textContent = `🏟️ Polymarket 全球運動賽事 ${minProbStr}%+ 即時勝率監測表`;
    }
    if ($("filter-only-70-label")) {
      $("filter-only-70-label").textContent = `🔥 僅看 >= ${minProbStr}% 選項`;
    }

    // Update red threshold markers & manual bet button labels inside HK Temp outcome cards
    const markerLeft = Math.max(5, Math.min(99, Number(hkPriceMinStr) || 70)).toFixed(1);
    document.querySelectorAll(".hk-threshold-marker").forEach((el) => {
      el.style.left = `${markerLeft}%`;
    });
    document.querySelectorAll(".hk-bar-wrap").forEach((el) => {
      el.title = `紅線為 ${hkPriceMinStr}% 自動觸發門檻`;
    });
    document.querySelectorAll(".hk-manual-bet-btn").forEach((btn) => {
      btn.textContent = `⚡ 投注 $${hkStake}`;
    });
  }

  function renderHkTempModule(hk) {
    const curr = hk.current || {};
    const fc = hk.forecast || {};
    const eng = hk.engine || {};
    const mkt = hk.market || {};
    const priceMin = Number(eng.priceMin ?? 0.70);
    const priceMinPct = Math.round(priceMin * 100);
    const stakeVal = Number(eng.stakeUsdc ?? 10);

    // Top badges
    const navBadge = $("hktemp-nav-badge");
    if (navBadge) {
      navBadge.textContent = curr.tempC != null ? `${curr.tempC}°C` : "HKO";
    }

    const obsBadge = $("hk-obs-status-badge");
    if (obsBadge) {
      obsBadge.textContent = hk.observatoryState === "live" ? "天文臺：即時" : "天文臺：延遲";
      obsBadge.className = `badge ${hk.observatoryState === "live" ? "badge-success" : "badge-warn"}`;
    }

    const polyBadge = $("hk-poly-status-badge");
    if (polyBadge) {
      polyBadge.textContent = hk.polymarketState === "live" ? "Polymarket 即時" : "Polymarket 中斷";
      polyBadge.className = `badge ${hk.polymarketState === "live" ? "badge-accent" : "badge-danger"}`;
    }

    // KPI Card 5 & 6 + Weather Strip
    const currTempStr = curr.tempC != null ? `${curr.tempC}°C` : "—";
    if ($("hk-current-temp")) $("hk-current-temp").textContent = currTempStr;
    if ($("hk-strip-current")) $("hk-strip-current").textContent = currTempStr;

    const staleBadge = $("hk-current-stale-badge");
    if (staleBadge) {
      if (curr.stale) {
        staleBadge.textContent = `延遲 (${curr.ageMinutes}m)`;
        staleBadge.className = "badge badge-warn";
      } else {
        staleBadge.textContent = "即時觀測";
        staleBadge.className = "badge badge-success";
      }
    }
    if ($("hk-current-detail")) {
      $("hk-current-detail").textContent = `觀測 ${fmtDateTimeHkt(curr.recordTime)} · ${curr.place || "香港天文臺"}`;
    }

    if ($("hk-forecast-date")) $("hk-forecast-date").textContent = fc.forecastDate || hk.marketDate;
    const fcShortStr = fc.maxC != null ? `預測 ${fc.maxC}°C` : "—";
    const fcFullStr = fc.maxC != null ? `最高 ${fc.maxC}°C` + (fc.minC != null ? ` / 最低 ${fc.minC}°C` : "") : "—";
    if ($("hk-forecast-temp")) $("hk-forecast-temp").textContent = fcShortStr;
    if ($("hk-strip-forecast")) $("hk-strip-forecast").textContent = fcFullStr;
    if ($("hk-forecast-desc")) $("hk-forecast-desc").textContent = fc.weather || "香港天文臺本港地區天氣預報";

    const offMaxStr = hk.officialMax?.label || "觀測中";
    const maxTimeStr = hk.maxTime?.label || "—";
    if ($("hk-official-max")) $("hk-official-max").textContent = offMaxStr;
    if ($("hk-max-time")) $("hk-max-time").textContent = maxTimeStr;
    if ($("hk-strip-maxtime")) $("hk-strip-maxtime").textContent = `${maxTimeStr} (官方日高：${offMaxStr})`;

    if ($("hk-phase-label")) $("hk-phase-label").textContent = eng.phaseLabel || "即時監測中";
    if ($("hk-phase-message")) $("hk-phase-message").textContent = eng.message || "";
    if ($("hk-gate-countdown")) {
      $("hk-gate-countdown").textContent = `${String(hk.gateHour ?? 11).padStart(2, "0")}:00 HKT 開閘 · 門檻 >= ${priceMinPct}% 買入 $${stakeVal}`;
    }

    // HK Temp Toggle Button in Unified Strategy Header
    const autoBtn = $("hk-auto-toggle-btn");
    if (autoBtn) {
      if (eng.autoEnabled) {
        autoBtn.className = "btn btn-bot-active";
        autoBtn.innerHTML = `<span>🌡️ 氣溫 ${priceMinPct}%+ 自動：運行中</span>`;
      } else {
        autoBtn.className = "btn btn-bot-paused";
        autoBtn.innerHTML = `<span>⏸️ 氣溫 ${priceMinPct}%+ 自動：已暫停</span>`;
      }
    }

    if ($("hk-rule-prob-text")) {
      $("hk-rule-prob-text").textContent = `${priceMinPct}%`;
    }

    // Market Title & Temperature Outcomes
    if ($("hk-market-title")) {
      $("hk-market-title").textContent = `🌡️ ${mkt.title || "Highest temperature in Hong Kong"} (${hk.marketDate})`;
    }
    const mktBadge = $("hk-market-status-badge");
    if (mktBadge) {
      mktBadge.textContent = mkt.statusLabel || "開放";
      mktBadge.className = `badge ${mkt.status === "open" ? "badge-success" : "badge-warn"}`;
    }

    const outcomesEl = $("hk-outcomes-container");
    const outcomes = mkt.outcomes || [];

    if (outcomesEl) {
      if (outcomes.length === 0) {
        outcomesEl.innerHTML = `<div class="empty-cell">找不到今日「香港最高氣溫」市場選項，可能尚未開市。</div>`;
      } else {
        outcomesEl.innerHTML = outcomes
          .map((o) => {
            const askPct = (Number(o.execPrice || 0) * 100).toFixed(1);
            const barWidth = Math.max(1, Math.min(100, Number(o.execPrice || 0) * 100)).toFixed(1);
            const markerLeft = Math.max(5, Math.min(99, priceMin * 100)).toFixed(1);
            const cardClass = o.inBand ? "hk-outcome-card hk-in-band" : "hk-outcome-card";
            const badgeClass =
              o.badgeVariant === "hit"
                ? "badge-success"
                : o.badgeVariant === "near" || o.badgeVariant === "standby"
                ? "badge-warn"
                : "badge-outline";

            return `
              <div class="${cardClass}">
                <div class="hk-outcome-top">
                  <div>
                    <div class="hk-outcome-name">${escapeHtml(o.name)}</div>
                    <div class="hk-outcome-q">${escapeHtml(o.question)}</div>
                  </div>
                  <div>
                    <div class="hk-outcome-price ${o.inBand ? "pnl-pos" : ""}">${askPct}%</div>
                    <div style="font-size:10.5px;color:var(--text-muted);text-align:right;">
                      ${o.quoteSource === "clob" ? "CLOB 最佳賣價" : "參考報價"}
                    </div>
                  </div>
                </div>

                <div class="hk-bar-wrap" title="紅線為 ${priceMinPct}% 自動觸發門檻">
                  <div class="hk-bar-fill" style="width:${barWidth}%"></div>
                  <span class="hk-threshold-marker" style="left:${markerLeft}%"></span>
                </div>

                <div class="hk-outcome-foot">
                  <span class="badge ${badgeClass}">${escapeHtml(o.badgeLabel)}</span>
                  <span>買 ${(Number(o.bestBid || 0) * 100).toFixed(1)}% / 賣 ${(Number(o.bestAsk || 0) * 100).toFixed(1)}%</span>
                  <button
                    type="button"
                    class="btn btn-xs btn-bet-now hk-manual-bet-btn"
                    data-outcome-name="${escapeHtml(o.name)}"
                  >
                    ⚡ 投注 $${stakeVal}
                  </button>
                </div>
              </div>
            `;
          })
          .join("");
      }
    }

    // Historical Weather Comparison Table
    const weatherTbody = $("hk-weather-records-tbody");
    const records = hk.weatherRecords || [];
    if (weatherTbody) {
      if (records.length === 0) {
        weatherTbody.innerHTML = `<tr><td colspan="6" class="empty-cell">尚無歷史天氣對照資料</td></tr>`;
      } else {
        weatherTbody.innerHTML = records
          .map((r) => {
            const fcStr = r.forecastMaxC != null ? `${Number(r.forecastMaxC).toFixed(1).replace(/\.0$/, "")}°C` : "—";
            const errStr =
              r.forecastErrorC != null
                ? `${r.forecastErrorC > 0 ? "+" : ""}${Number(r.forecastErrorC).toFixed(1)}°C`
                : "—";
            return `
              <tr>
                <td><strong>${escapeHtml(r.marketDate)}</strong></td>
                <td>${escapeHtml(fcStr)}</td>
                <td>${escapeHtml(fmtDateTimeHkt(r.forecastIssuedAt))}</td>
                <td><span class="badge ${r.officialLabel === "觀測中" ? "badge-warn" : "badge-success"}">${escapeHtml(r.officialLabel || "—")}</span></td>
                <td>${escapeHtml(r.awsTimeLabel || "—")}</td>
                <td>${escapeHtml(errStr)}</td>
              </tr>
            `;
          })
          .join("");
      }
    }
  }

  function renderMarketsTable(markets, config) {
    const tbody = $("markets-tbody");
    if (!tbody) return;

    const minProb = Number(config.min_win_prob || 0.70);
    const maxProb = Number(config.max_win_prob || 0.95);
    const betAmt = Number(config.bet_amount_usdc || 25);

    const filtered = markets.filter((m) => {
      if (selectedSport !== "ALL" && m.sport !== selectedSport) return false;
      if (onlyAbove70 && Number(m.top_outcome?.prob || 0) < minProb) return false;
      if (searchQuery) {
        const hay = `${m.event_title} ${m.question} ${m.sport_label} ${m.outcomes.map((o) => o.label).join(" ")}`.toLowerCase();
        if (!hay.includes(searchQuery.toLowerCase())) return false;
      }
      return true;
    });

    if (filtered.length === 0) {
      tbody.innerHTML = `<tr><td colspan="5" class="empty-cell">目前篩選條件下沒有符合的運動賽事盤口</td></tr>`;
      return;
    }

    tbody.innerHTML = filtered
      .map((m) => {
        const isLiveView = Boolean(appState?.auth?.is_admin) && activeLedgerMode === "LIVE";
        const ev =
          (isLiveView ? m.live_rule_evaluation : m.paper_rule_evaluation) ||
          m.rule_evaluation ||
          {};
        const rowClass = ev.status_code === "QUALIFIED" ? "row-qualified" : "";

        const outcomesHtml = (m.outcomes || [])
          .map((o) => {
            const p = Number(o.prob || 0);
            const pctStr = (p * 100).toFixed(1) + "%";
            let probClass = "prob-low";
            let fillClass = "fill-low";
            if (p >= minProb && p <= maxProb) {
              probClass = "prob-hit";
              fillClass = "fill-hit";
            } else if (p > maxProb) {
              probClass = "prob-over";
              fillClass = "fill-over";
            }
            const widthPct = Math.max(3, Math.min(100, p * 100)).toFixed(1);
            return `
              <div class="outcome-row">
                <div class="outcome-top">
                  <span class="outcome-name">${escapeHtml(o.label)}</span>
                  <span class="outcome-prob ${probClass}">${pctStr} <small style="font-weight:400;opacity:0.75;">(Ask ${(o.best_ask * 100).toFixed(1)}¢)</small></span>
                </div>
                <div class="prob-bar-bg">
                  <div class="prob-bar-fill ${fillClass}" style="width:${widthPct}%"></div>
                </div>
              </div>
            `;
          })
          .join("");

        const topOutcome = m.top_outcome || m.outcomes?.[0] || { index: 0, label: "Yes", prob: 0 };
        const endCountdownStr = fmtEndCountdown(m.end_date);
        const isAlreadyBet = Boolean(ev.already_bet || ev.status_code === "ALREADY_BET");

        return `
          <tr class="${rowClass}">
            <td>
              <div class="market-meta">
                <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap;">
                  <span class="sport-chip">${escapeHtml(m.sport_icon)} ${escapeHtml(m.sport_label)}</span>
                  <span class="badge badge-outline" style="font-size:11px;color:#93c5fd;">${escapeHtml(endCountdownStr)}</span>
                </div>
                <a class="market-title-link" href="${escapeHtml(m.polymarket_url)}" target="_blank" rel="noopener">
                  ${escapeHtml(m.event_title)} ↗
                </a>
                <span class="market-question">${escapeHtml(m.question)}</span>
              </div>
            </td>
            <td>
              <div class="outcomes-stack">${outcomesHtml}</div>
            </td>
            <td>
              <div class="depth-cell">
                <div>流動性：<strong>$${Number(m.liquidity_usdc || 0).toLocaleString()}</strong></div>
                <div>成交量：$${Number(m.volume_usdc || 0).toLocaleString()}</div>
                <div>點差：${(Number(m.spread || 0) * 100).toFixed(1)}%</div>
              </div>
            </td>
            <td>
              <div>
                <span class="verdict-badge verdict-${escapeHtml(ev.status_code || "PROB_TOO_LOW")}">
                  ${escapeHtml(ev.status_label || "監測中")}
                </span>
                <div class="verdict-reason">${escapeHtml(ev.reason || "")}</div>
              </div>
            </td>
            <td class="text-right">
              <button
                type="button"
                class="btn btn-xs ${isAlreadyBet ? "btn-ghost" : "btn-bet-now"} manual-bet-btn"
                data-market-id="${escapeHtml(m.market_id)}"
                data-outcome-index="${Number(topOutcome.index || 0)}"
                data-outcome-label="${escapeHtml(topOutcome.label)}"
                ${isAlreadyBet ? "disabled title='同一個運動項目只進行一次投注'" : ""}
              >
                ${isAlreadyBet ? "✅ 同項目已投注" : `⚡ 買入「${escapeHtml(topOutcome.label)}」($${betAmt})`}
              </button>
            </td>
          </tr>
        `;
      })
      .join("");
  }

  function renderPositions(openPositions, closedPositions, hkTrades) {
    if ($("open-pos-badge")) $("open-pos-badge").textContent = openPositions.length;
    if ($("hk-pos-badge")) $("hk-pos-badge").textContent = hkTrades.length;
    if ($("closed-pos-badge")) $("closed-pos-badge").textContent = closedPositions.length;

    const minProbStr = ($("cfg-min-prob")?.value || "70").trim();
    const hkPriceMinStr = ($("hk-pricemin-input")?.value || "70").trim();
    const hkGateHour = Number($("hk-gatehour-input")?.value ?? 11);
    const hkGateStr = `${String(hkGateHour).padStart(2, "0")}:00`;

    const openEl = $("open-positions-view");
    if (openEl) {
      if (openPositions.length === 0) {
        openEl.innerHTML = `<div class="empty-cell">目前尚無運動活躍持倉。當運動項目勝率 &gt;= ${escapeHtml(minProbStr)}% 時將自動建倉。</div>`;
      } else {
        const slEnabled = Boolean($("cfg-sl-enabled")?.checked);
        const slMin = Number($("cfg-sl-minutes")?.value ?? 20);
        const slProb = Number($("cfg-sl-prob")?.value ?? 52) / 100.0;
        openEl.innerHTML = openPositions
          .map((p) => {
            const pnl = Number(p.unrealized_pnl_usdc || 0);
            const curProb = Number(p.current_prob || 0);
            const endInfo = p.end_date ? fmtEndCountdown(p.end_date) : "";
            let slGuardHtml = "";
            if (slEnabled && curProb <= slProb && slMin > 0) {
              slGuardHtml = `<div style="font-size:11px;color:#fbbf24;margin-top:2px;">⏳ 勝率已低於停損線，等待賽事剩餘 &lt;= ${slMin} 分鐘才觸發停損</div>`;
            }
            return `
              <div class="position-card">
                <div class="pos-top">
                  <div>
                    <div class="pos-title">🏟️ ${escapeHtml(p.event_title)}</div>
                    <div class="pos-outcome">🎯 投注選項：${escapeHtml(p.outcome_label)} (${escapeHtml(p.trading_mode)})</div>
                    ${endInfo ? `<div style="font-size:11px;color:#93c5fd;margin-top:2px;">${escapeHtml(endInfo)}</div>` : ""}
                    ${slGuardHtml}
                  </div>
                  <span class="badge ${pnl >= 0 ? "badge-success" : "badge-danger"}">${fmtUsd(pnl, true)}</span>
                </div>
                <div class="pos-metrics">
                  <div>進場勝率：<strong>${(Number(p.entry_prob) * 100).toFixed(1)}%</strong></div>
                  <div>目前勝率：<strong>${(curProb * 100).toFixed(1)}%</strong></div>
                  <div>持有股數：<strong>${Number(p.shares).toFixed(2)}</strong></div>
                  <div>投入本金：<strong>$${Number(p.amount_usdc).toFixed(2)}</strong></div>
                  <div>勝出可領：<strong>$${Number(p.shares).toFixed(2)}</strong></div>
                  <div>觸發方式：<strong>${p.trigger_type === "AUTO_70_RULE" ? "自動觸發" : "手動"}</strong></div>
                </div>
                <div class="pos-actions">
                  <button type="button" class="btn btn-xs btn-outline pos-close-btn" data-id="${p.id}" data-action="MARKET_CLOSE">
                    市價平倉
                  </button>
                  <button type="button" class="btn btn-xs btn-bet-now pos-close-btn" data-id="${p.id}" data-action="SETTLE_WIN">
                    🏆 勝出結算 (100¢)
                  </button>
                  <button type="button" class="btn btn-xs btn-ghost pos-close-btn" data-id="${p.id}" data-action="SETTLE_LOSS">
                    落敗 (0¢)
                  </button>
                </div>
              </div>
            `;
          })
          .join("");
      }
    }

    const hkEl = $("hk-positions-view");
    if (hkEl) {
      if (hkTrades.length === 0) {
        hkEl.innerHTML = `<div class="empty-cell">目前尚無香港氣溫注項。當 ${escapeHtml(hkGateStr)} HKT 後溫度區間勝率 &gt;= ${escapeHtml(hkPriceMinStr)}% 時將自動建倉。</div>`;
      } else {
        hkEl.innerHTML = hkTrades
          .map((t) => {
            const pnl = Number(t.pnl_usdc || 0);
            return `
              <div class="position-card">
                <div class="pos-top">
                  <div>
                    <div class="pos-title">🌡️ 香港最高氣溫 (${escapeHtml(t.market_date)})</div>
                    <div class="pos-outcome">🎯 溫度區間：<strong>${escapeHtml(t.outcome_name)} (是)</strong> · ${t.mode === "live" ? "實盤" : "模擬"}</div>
                  </div>
                  <span class="badge ${pnl >= 0 ? "badge-success" : "badge-danger"}">${fmtUsd(pnl, true)}</span>
                </div>
                <div class="pos-metrics">
                  <div>進場價格：<strong>${(Number(t.price) * 100).toFixed(1)}%</strong></div>
                  <div>目前價格：<strong>${(Number(t.current_price) * 100).toFixed(1)}%</strong></div>
                  <div>持有張數：<strong>${Number(t.size_shares).toFixed(2)}</strong></div>
                  <div>投入本金：<strong>$${Number(t.stake_usdc).toFixed(2)}</strong></div>
                  <div>狀態：<strong>${t.status === "filled" ? "✅ 已成交" : escapeHtml(t.status)}</strong></div>
                  <div>時間：<strong>${escapeHtml(fmtDateTimeHkt(t.created_at))}</strong></div>
                </div>
              </div>
            `;
          })
          .join("");
      }
    }

    const closedEl = $("closed-positions-view");
    if (closedEl) {
      if (closedPositions.length === 0) {
        closedEl.innerHTML = `<div class="empty-cell">尚無已平倉或結算紀錄</div>`;
      } else {
        closedEl.innerHTML = closedPositions
          .map((p) => {
            const pnl = Number(p.realized_pnl_usdc || 0);
            return `
              <div class="position-card">
                <div class="pos-top">
                  <div>
                    <div class="pos-title">${escapeHtml(p.event_title)}</div>
                    <div class="pos-outcome">${escapeHtml(p.outcome_label)} · 狀態：${escapeHtml(p.status)}</div>
                  </div>
                  <span class="badge ${pnl >= 0 ? "badge-success" : "badge-danger"}">${fmtUsd(pnl, true)}</span>
                </div>
                <div class="pos-metrics">
                  <div>進場：${(Number(p.entry_prob) * 100).toFixed(1)}%</div>
                  <div>出場：${(Number(p.exit_prob ?? p.current_prob) * 100).toFixed(1)}%</div>
                  <div>本金：$${Number(p.amount_usdc).toFixed(2)}</div>
                </div>
              </div>
            `;
          })
          .join("");
      }
    }
  }

  function renderLogs(logs) {
    const listEl = $("trade-logs-list");
    if (!listEl) return;
    if (logs.length === 0) {
      listEl.innerHTML = `<div class="empty-cell">尚無交易日誌</div>`;
      return;
    }
    listEl.innerHTML = logs
      .map((l) => {
        return `
          <div class="log-item log-${escapeHtml(l.level)}">
            <span class="log-time">[${fmtTime(l.created_at)}]</span>
            <span>${escapeHtml(l.message)}</span>
          </div>
        `;
      })
      .join("");
  }

  function connectWebSocket() {
    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    const qs = adminToken ? `?token=${encodeURIComponent(adminToken)}` : "";
    ws = new WebSocket(`${proto}//${window.location.host}/ws${qs}`);
    ws.onopen = () => {
      if (adminToken && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: "AUTH", token: adminToken }));
      }
    };
    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        if (msg.type === "STATE_UPDATE") {
          renderState(msg.data);
        }
      } catch (err) {
        console.error("WS parse error", err);
      }
    };
    ws.onclose = () => {
      const wsDot = $("ws-dot");
      if (wsDot) wsDot.classList.add("offline");
      setTimeout(connectWebSocket, 3000);
    };
  }

  function activatePosTab(tabKey) {
    ["tab-open-pos-btn", "tab-hk-pos-btn", "tab-closed-pos-btn"].forEach((id) => {
      const el = $(id);
      if (el) el.classList.remove("active");
    });
    ["open-positions-view", "hk-positions-view", "closed-positions-view"].forEach((id) => {
      const el = $(id);
      if (el) el.classList.add("hidden");
    });

    if (tabKey === "hk") {
      $("tab-hk-pos-btn")?.classList.add("active");
      $("hk-positions-view")?.classList.remove("hidden");
    } else if (tabKey === "closed") {
      $("tab-closed-pos-btn")?.classList.add("active");
      $("closed-positions-view")?.classList.remove("hidden");
    } else {
      $("tab-open-pos-btn")?.classList.add("active");
      $("open-positions-view")?.classList.remove("hidden");
    }
  }

  function updateLpDescriptionsFromInputs() {
    const cap = Number($("lp-cfg-capital")?.value || 500);
    const resPct = Number($("lp-cfg-reserve")?.value ?? 20);
    const slots = Number($("lp-cfg-slots")?.value || 4);
    const perSlot = Number($("lp-cfg-perslot")?.value || 100);
    const offsetStr = ($("lp-cfg-offset")?.value || "1.2").trim();
    const midMin = Number($("lp-cfg-mid-min")?.value || 25);
    const midMax = Number($("lp-cfg-mid-max")?.value || 75);
    const shield = Number($("lp-cfg-shield")?.value ?? 150);
    const velStr = ($("lp-cfg-velocity")?.value || "2.5").trim();
    const invMax = Number($("lp-cfg-inventory")?.value || 60);
    const cutoffHrs = Number($("lp-cfg-cutoff")?.value || 6);
    const cooldownSec = Number($("lp-cfg-cooldown")?.value || 120);

    const reserveUsd = Math.round(cap * (resPct / 100.0));
    const deployableUsd = Math.max(0, cap - reserveUsd);
    const halfSlot = Math.round(perSlot / 2);

    if ($("lp-desc-capital")) {
      $("lp-desc-capital").textContent = `總資金 $${cap.toLocaleString()}（可動用掛單 $${deployableUsd.toLocaleString()} + 緩衝 $${reserveUsd.toLocaleString()}）`;
    }
    if ($("lp-desc-reserve")) {
      $("lp-desc-reserve").textContent = `保留 ${resPct}% ($${reserveUsd.toLocaleString()} USDC) 應對單邊接單與對沖`;
    }
    if ($("lp-desc-slots")) {
      $("lp-desc-slots").textContent = `分散於前 ${slots} 名最高獎勵效率的利基市場`;
    }
    if ($("lp-desc-perslot")) {
      $("lp-desc-perslot").textContent = `每市場買單 (Bid) $${halfSlot} + 賣單 (Ask) $${halfSlot}`;
    }
    if ($("lp-desc-offset")) {
      $("lp-desc-offset").textContent = `掛在 Mid ± ${offsetStr}¢（兼顧平方高分與前檔肉盾）`;
    }
    if ($("lp-desc-mid-min")) {
      $("lp-desc-mid-min").textContent = `低於 ${midMin}¢ 的極端冷門盤自動撤單禁入`;
    }
    if ($("lp-desc-mid-max")) {
      $("lp-desc-mid-max").textContent = `高於 ${midMax}¢ 的極端尾盤自動撤單禁入`;
    }
    if ($("lp-desc-shield")) {
      $("lp-desc-shield").textContent = `前檔保護深度 < $${shield.toLocaleString()} 時自動撤單避險`;
    }
    if ($("lp-desc-velocity")) {
      $("lp-desc-velocity").textContent = `15 秒內中價跳動 > ${velStr}¢ 立即自動撤單`;
    }
    if ($("lp-desc-inventory")) {
      $("lp-desc-inventory").textContent = `單邊被成交達 $${invMax.toLocaleString()} 停止接刀，專注反手平倉`;
    }
    if ($("lp-desc-cutoff")) {
      $("lp-desc-cutoff").textContent = `開賽或結算前 ${cutoffHrs} 小時自動撤除所有做市單`;
    }
    if ($("lp-desc-cooldown")) {
      $("lp-desc-cooldown").textContent = `觸發閃電撤單後暫停報價 ${cooldownSec} 秒`;
    }
    if ($("lp-kpi-max-inv")) {
      $("lp-kpi-max-inv").textContent = `$${invMax}`;
    }
    if ($("lp-kpi-cutoff-hrs")) {
      $("lp-kpi-cutoff-hrs").textContent = `${cutoffHrs}h`;
    }

    // Immediately update deploy button labels in LP Radar Table
    document.querySelectorAll(".lp-deploy-btn:not([disabled])").forEach((btn) => {
      btn.textContent = `➕ 雙邊掛單 ($${perSlot})`;
    });

    if ($("lp-strategy-live-summary")) {
      $("lp-strategy-live-summary").innerHTML =
        `📋 <strong>目前做市策略描述：</strong>` +
        `以總資金 <strong>$${cap.toLocaleString()} USDC</strong> 切分為 <strong>${slots} 個做市槽位</strong>` +
        `（每槽位 <strong>$${perSlot.toLocaleString()}</strong>：買方 $${halfSlot} + 賣方 $${halfSlot}，預留 <strong>${resPct}% / $${reserveUsd.toLocaleString()}</strong> 緩衝），` +
        `僅挑選中價 <strong>${midMin}¢ ~ ${midMax}¢</strong>、距離結算 <strong>&gt; ${cutoffHrs} 小時</strong> 且前檔肉盾 <strong>&gt;= $${shield.toLocaleString()}</strong> 的高獎勵盤，` +
        `於 <strong>Mid &plusmn; ${escapeHtml(offsetStr)}¢</strong> 雙邊被動掛單（單盤淨庫存上限 <strong>$${invMax}</strong>）；` +
        `若中價瞬跳 <strong>&gt; ${escapeHtml(velStr)}¢</strong> 則毫秒級自動撤單並冷卻 <strong>${cooldownSec}s</strong>。`;
    }
  }

  function renderLpModule(lp, isLiveView) {
    const cfg = lp.config || {};
    const modeData = (isLiveView && lp.live ? lp.live : lp.paper) || {};
    const activeQuotes = modeData.active_quotes || [];
    const historyQuotes = modeData.history_quotes || [];
    const logs = modeData.logs || [];
    const markets = lp.markets || [];

    // Topbar badge
    const navBadge = $("lp-nav-badge");
    if (navBadge) {
      navBadge.textContent = `${modeData.active_slots_count || 0}/${modeData.max_slots || 4} 槽位 · +$${Number(modeData.est_daily_reward_usd || 0).toFixed(2)}/日`;
    }

    // 1. LP 4-Card KPI Strip
    if ($("lp-kpi-capital-label")) {
      $("lp-kpi-capital-label").textContent = isLiveView
        ? "⚡ 實盤做市總淨值 (Live LP)"
        : `🧪 模擬做市總淨值 ($${Number(cfg.total_capital_usdc || 500).toFixed(0)} 起始)`;
    }
    if ($("lp-reset-paper-btn")) {
      $("lp-reset-paper-btn").classList.toggle("hidden", isLiveView);
    }
    if ($("lp-kpi-equity")) $("lp-kpi-equity").textContent = fmtUsd(modeData.total_equity_usdc || 0);
    if ($("lp-kpi-deployed")) $("lp-kpi-deployed").textContent = fmtUsd(modeData.deployed_usdc || 0);
    if ($("lp-kpi-slots")) {
      $("lp-kpi-slots").textContent = `${modeData.active_slots_count || 0} / ${modeData.max_slots || 4} 槽位`;
    }
    if ($("lp-kpi-reserve")) $("lp-kpi-reserve").textContent = fmtUsd(modeData.reserve_usdc || 0);

    if ($("lp-kpi-apr-badge")) {
      $("lp-kpi-apr-badge").textContent = `預估 APR ${Number(modeData.est_apr_pct || 0).toFixed(1)}%`;
    }
    if ($("lp-kpi-daily-reward")) {
      $("lp-kpi-daily-reward").textContent = `+${fmtUsd(modeData.est_daily_reward_usd || 0)} / 日`;
    }
    if ($("lp-kpi-monthly-reward")) {
      $("lp-kpi-monthly-reward").textContent = `+${fmtUsd(modeData.est_monthly_reward_usd || 0)} / 月`;
    }
    if ($("lp-kpi-qscore")) {
      $("lp-kpi-qscore").textContent = `Q = ${Number(modeData.total_q_score || 0).toFixed(1)}`;
    }

    const totProfit = Number(modeData.total_net_profit_usd || 0);
    const profEl = $("lp-kpi-total-profit");
    if (profEl) {
      profEl.textContent = fmtUsd(totProfit, true);
      profEl.className = `kpi-value ${totProfit >= 0 ? "pnl-pos" : "pnl-neg"}`;
    }
    if ($("lp-kpi-accrued-reward")) {
      $("lp-kpi-accrued-reward").textContent = `+$${Number(modeData.accrued_reward_usd || 0).toFixed(4)}`;
    }
    if ($("lp-kpi-spread-pnl")) {
      $("lp-kpi-spread-pnl").textContent = fmtUsd(modeData.spread_pnl_usd || 0, true);
    }
    if ($("lp-kpi-inventory")) {
      $("lp-kpi-inventory").textContent = fmtUsd(modeData.net_inventory_usdc || 0);
    }

    // Ledger badges
    const lpLedgerBadge = $("lp-ledger-badge");
    if (lpLedgerBadge) {
      lpLedgerBadge.textContent = isLiveView ? "⚡ 實盤做市帳本" : "🧪 模擬做市帳本";
      lpLedgerBadge.className = `badge ${isLiveView ? "badge-danger" : "badge-accent"}`;
    }
    if ($("lp-logs-title")) {
      $("lp-logs-title").textContent = isLiveView
        ? "⚡ 被動做市與防毒流撤單日誌 (實盤 LIVE)"
        : "🧪 被動做市與防毒流撤單日誌 (模擬盤 PAPER)";
    }

    // 2. LP Strategy Toggle & Form
    const autoBtn = $("lp-auto-toggle-btn");
    if (autoBtn) {
      if (cfg.auto_enabled) {
        autoBtn.className = "btn btn-bot-active";
        autoBtn.innerHTML = `<span>💧 自動選盤做市：運行中</span>`;
      } else {
        autoBtn.className = "btn btn-bot-paused";
        autoBtn.innerHTML = `<span>⏸️ 自動選盤做市：已暫停</span>`;
      }
    }

    if (!lpFormDirty) {
      if ($("lp-cfg-capital")) $("lp-cfg-capital").value = cfg.total_capital_usdc ?? 500;
      if ($("lp-cfg-reserve")) $("lp-cfg-reserve").value = cfg.reserve_ratio_pct ?? 20;
      if ($("lp-cfg-slots")) $("lp-cfg-slots").value = cfg.max_slots ?? 4;
      if ($("lp-cfg-perslot")) $("lp-cfg-perslot").value = cfg.per_slot_usdc ?? 100;
      if ($("lp-cfg-offset")) $("lp-cfg-offset").value = cfg.quote_offset_cents ?? 1.2;
      if ($("lp-cfg-mid-min")) $("lp-cfg-mid-min").value = Math.round((cfg.safe_mid_min ?? 0.25) * 100);
      if ($("lp-cfg-mid-max")) $("lp-cfg-mid-max").value = Math.round((cfg.safe_mid_max ?? 0.75) * 100);
      if ($("lp-cfg-shield")) $("lp-cfg-shield").value = cfg.min_shield_depth_usdc ?? 150;
      if ($("lp-cfg-velocity")) $("lp-cfg-velocity").value = cfg.max_velocity_cents ?? 2.5;
      if ($("lp-cfg-inventory")) $("lp-cfg-inventory").value = cfg.max_inventory_usdc ?? 60;
      if ($("lp-cfg-cutoff")) $("lp-cfg-cutoff").value = cfg.pre_event_cutoff_hours ?? 6;
      if ($("lp-cfg-cooldown")) $("lp-cfg-cooldown").value = cfg.cooldown_seconds ?? 120;
    }

    // 3. LP Rewards Radar Table
    const tbody = $("lp-markets-tbody");
    const perSlotVal = Number($("lp-cfg-perslot")?.value || cfg.per_slot_usdc || 100);
    if (tbody) {
      if (markets.length === 0) {
        tbody.innerHTML = `<tr><td colspan="6" class="empty-cell">目前尚無符合條件的流動性獎勵盤口</td></tr>`;
      } else {
        tbody.innerHTML = markets
          .map((m) => {
            const ev = (isLiveView ? m.live_eval : m.paper_eval) || m.eval || {};
            const met = ev.metrics || {};
            const isQuoting = ev.status_code === "ACTIVE_QUOTING";
            const rowClass = isQuoting || ev.status_code === "ELIGIBLE_HIGH_YIELD" ? "row-qualified" : "";
            const hrs = Number(m.hours_to_end || 0);
            const hrsText = hrs >= 24 ? `${Math.round(hrs / 24)} 天 (${hrs.toFixed(0)}h)` : `${hrs.toFixed(1)} 小時`;

            return `
              <tr class="${rowClass}">
                <td>
                  <div class="market-meta">
                    <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap;">
                      <span class="sport-chip">${escapeHtml(m.category)}</span>
                      <span class="badge badge-outline" style="font-size:11px;color:#93c5fd;">⏱️ 距結算 ${escapeHtml(hrsText)}</span>
                    </div>
                    <a class="market-title-link" href="${escapeHtml(m.polymarket_url || "https://polymarket.com")}" target="_blank" rel="noopener">
                      ${escapeHtml(m.event_title)} ↗
                    </a>
                    <span class="market-question">${escapeHtml(m.question)}</span>
                  </div>
                </td>
                <td>
                  <div style="display:flex;flex-direction:column;gap:5px;">
                    <div>中價 (Mid)：<strong class="mono-num">${(Number(m.midpoint) * 100).toFixed(1)}¢</strong></div>
                    <div class="quote-band-pill">
                      <span class="pnl-pos">Bid ${(Number(met.bid_price || 0) * 100).toFixed(1)}¢</span>
                      <span>/</span>
                      <span style="color:#60a5fa;">Ask ${(Number(met.ask_price || 0) * 100).toFixed(1)}¢</span>
                    </div>
                  </div>
                </td>
                <td>
                  <div class="depth-cell">
                    <div>日獎勵池：<strong class="pnl-pos">$${Number(m.rewards_daily_usd).toFixed(0)} / 日</strong></div>
                    <div>有效點差 (v)：&plusmn;${Number(m.rewards_max_spread_cents).toFixed(1)}¢</div>
                    <div>最低股數：${Number(m.rewards_min_size).toFixed(0)} 股</div>
                  </div>
                </td>
                <td>
                  <div class="depth-cell">
                    <div>預估日領：<strong class="pnl-pos">+$${Number(met.est_daily_reward_usd || 0).toFixed(2)} / 日</strong> <span class="badge badge-success">${Number(met.est_apr_pct || 0).toFixed(0)}% APR</span></div>
                    <div>雙邊平方分：<strong>Q = ${Number(met.q_score || 0).toFixed(1)}</strong> (佔池 ${Number(met.pool_share_pct || 0).toFixed(2)}%)</div>
                    <div>第一檔肉盾：<strong>$${Number(m.shield_depth_usdc || 0).toLocaleString()} USDC</strong></div>
                  </div>
                </td>
                <td>
                  <div>
                    <span class="verdict-badge verdict-${escapeHtml(ev.status_code || "SKIP_MID_RANGE")}">
                      ${escapeHtml(ev.status_label || "評估中")}
                    </span>
                    <div class="verdict-reason">${escapeHtml(ev.reason || "")}</div>
                  </div>
                </td>
                <td class="text-right">
                  <button
                    type="button"
                    class="btn btn-xs ${isQuoting ? "btn-ghost" : "btn-bet-now"} lp-deploy-btn"
                    data-market-id="${escapeHtml(m.market_id)}"
                    data-event-title="${escapeHtml(m.event_title)}"
                    ${isQuoting ? "disabled" : ""}
                  >
                    ${isQuoting ? "✅ 雙邊計分中" : `➕ 雙邊掛單 ($${perSlotVal})`}
                  </button>
                </td>
              </tr>
            `;
          })
          .join("");
      }
    }

    // 4. Active & History LP Slots in Right Column
    if ($("lp-active-badge")) $("lp-active-badge").textContent = activeQuotes.length;
    if ($("lp-history-badge")) $("lp-history-badge").textContent = historyQuotes.length;

    const actEl = $("lp-active-slots-view");
    if (actEl) {
      if (activeQuotes.length === 0) {
        actEl.innerHTML = `<div class="empty-cell">目前尚無運行中的雙邊掛單槽位。可開啟「💧 自動選盤做市」或於左側點擊「➕ 雙邊掛單」。</div>`;
      } else {
        actEl.innerHTML = activeQuotes
          .map((q) => {
            const acc = Number(q.accrued_reward_usd || 0);
            const spPnl = Number(q.spread_pnl_usd || 0);
            const invUsd = Number(q.net_inventory_usdc || 0);
            return `
              <div class="position-card">
                <div class="pos-top">
                  <div>
                    <div class="pos-title">💧 ${escapeHtml(q.event_title)}</div>
                    <div class="pos-outcome">
                      Bid ${(Number(q.bid_price) * 100).toFixed(1)}¢ (${Number(q.bid_size_shares).toFixed(0)}股)
                      · Ask ${(Number(q.ask_price) * 100).toFixed(1)}¢ (${Number(q.ask_size_shares).toFixed(0)}股)
                    </div>
                    <div style="font-size:11px;color:#93c5fd;margin-top:2px;">🛡️ ${escapeHtml(q.last_action || "POST_ONLY 雙邊計分中")}</div>
                  </div>
                  <span class="badge badge-success">+$${Number(q.est_daily_reward_usd).toFixed(2)}/日</span>
                </div>
                <div class="pos-metrics">
                  <div>配置資金：<strong>$${Number(q.allocated_usdc).toFixed(0)}</strong></div>
                  <div>平方得分：<strong>Q=${Number(q.q_score).toFixed(1)}</strong></div>
                  <div>前檔肉盾：<strong>$${Number(q.shield_depth_usdc).toFixed(0)}</strong></div>
                  <div>已領獎勵：<strong class="pnl-pos">+$${acc.toFixed(4)}</strong></div>
                  <div>價差損益：<strong class="${spPnl >= 0 ? "pnl-pos" : "pnl-neg"}">${fmtUsd(spPnl, true)}</strong></div>
                  <div>淨庫存：<strong>$${invUsd.toFixed(1)}</strong></div>
                </div>
                <div class="pos-actions">
                  <button type="button" class="btn btn-xs btn-outline lp-cancel-btn" data-slot-id="${q.id}">
                    🛑 撤單並釋放槽位 ($${Number(q.allocated_usdc).toFixed(0)})
                  </button>
                </div>
              </div>
            `;
          })
          .join("");
      }
    }

    const histEl = $("lp-history-slots-view");
    if (histEl) {
      if (historyQuotes.length === 0) {
        histEl.innerHTML = `<div class="empty-cell">尚無已撤單或防毒流清退紀錄</div>`;
      } else {
        histEl.innerHTML = historyQuotes
          .map((q) => {
            const tot = Number(q.accrued_reward_usd || 0) + Number(q.spread_pnl_usd || 0);
            return `
              <div class="position-card">
                <div class="pos-top">
                  <div>
                    <div class="pos-title">${escapeHtml(q.event_title)}</div>
                    <div class="pos-outcome" style="color:#fbbf24;">狀態：${escapeHtml(q.status)} · ${escapeHtml(q.last_action)}</div>
                  </div>
                  <span class="badge ${tot >= 0 ? "badge-success" : "badge-danger"}">${fmtUsd(tot, true)}</span>
                </div>
                <div class="pos-metrics">
                  <div>領取獎勵：+$${Number(q.accrued_reward_usd).toFixed(4)}</div>
                  <div>價差損益：${fmtUsd(q.spread_pnl_usd, true)}</div>
                  <div>配置：$${Number(q.allocated_usdc).toFixed(0)}</div>
                </div>
              </div>
            `;
          })
          .join("");
      }
    }

    // 5. LP Execution Logs
    const lpLogEl = $("lp-logs-list");
    if (lpLogEl) {
      if (logs.length === 0) {
        lpLogEl.innerHTML = `<div class="empty-cell">尚無做市日誌</div>`;
      } else {
        lpLogEl.innerHTML = logs
          .map(
            (l) => `
              <div class="log-item log-${escapeHtml(l.level)}">
                <span class="log-time">[${fmtTime(l.created_at)}]</span>
                <span>${escapeHtml(l.message)}</span>
              </div>
            `
          )
          .join("");
      }
    }
  }

  function bindEvents() {
    // Topbar Workspace Switcher: Directional Dual-Engine vs $500 Passive LP Market Making
    $("nav-directional-btn")?.addEventListener("click", () => {
      $("nav-directional-btn")?.classList.add("active");
      $("nav-directional-btn")?.setAttribute("aria-selected", "true");
      $("nav-lp-btn")?.classList.remove("active");
      $("nav-lp-btn")?.setAttribute("aria-selected", "false");
      $("view-directional-dashboard")?.classList.remove("hidden");
      $("view-lp-dashboard")?.classList.add("hidden");
    });

    $("nav-lp-btn")?.addEventListener("click", () => {
      $("nav-lp-btn")?.classList.add("active");
      $("nav-lp-btn")?.setAttribute("aria-selected", "true");
      $("nav-directional-btn")?.classList.remove("active");
      $("nav-directional-btn")?.setAttribute("aria-selected", "false");
      $("view-lp-dashboard")?.classList.remove("hidden");
      $("view-directional-dashboard")?.classList.add("hidden");
    });

    // LP Strategy Form & Controls ($500 Passive Market Making)
    const lpForm = $("lp-strategy-form");
    let lpAutoSaveTimer = null;

    async function saveLpConfig(silent = false) {
      if (lpAutoSaveTimer) {
        clearTimeout(lpAutoSaveTimer);
        lpAutoSaveTimer = null;
      }
      try {
        const payload = {
          total_capital_usdc: Number($("lp-cfg-capital")?.value) || 500,
          reserve_ratio_pct: Number($("lp-cfg-reserve")?.value ?? 20),
          max_slots: Number($("lp-cfg-slots")?.value) || 4,
          per_slot_usdc: Number($("lp-cfg-perslot")?.value) || 100,
          quote_offset_cents: Number($("lp-cfg-offset")?.value) || 1.2,
          safe_mid_min: (Number($("lp-cfg-mid-min")?.value) || 25) / 100.0,
          safe_mid_max: (Number($("lp-cfg-mid-max")?.value) || 75) / 100.0,
          min_shield_depth_usdc: Number($("lp-cfg-shield")?.value ?? 150),
          max_velocity_cents: Number($("lp-cfg-velocity")?.value) || 2.5,
          max_inventory_usdc: Number($("lp-cfg-inventory")?.value) || 60,
          pre_event_cutoff_hours: Number($("lp-cfg-cutoff")?.value) || 6,
          cooldown_seconds: Number($("lp-cfg-cooldown")?.value) || 120,
        };
        const res = await apiPost("/api/lp/config", payload);
        lpFormDirty = false;
        renderState(res.state);
        if (!silent) {
          showToast(`✅ 被動做市設定已生效：總資金 $${payload.total_capital_usdc} (${payload.max_slots} 槽位 × $${payload.per_slot_usdc}) · 偏移 ±${payload.quote_offset_cents}¢`);
        }
      } catch (err) {
        if (!silent) {
          showToast(`❌ 做市設定儲存失敗：${err.message}`);
        }
      }
    }

    function onLpInputChanged() {
      lpFormDirty = true;
      updateLpDescriptionsFromInputs();
      if (lpAutoSaveTimer) clearTimeout(lpAutoSaveTimer);
      lpAutoSaveTimer = setTimeout(() => {
        saveLpConfig(true);
      }, 700);
    }

    lpForm?.addEventListener("input", onLpInputChanged);
    lpForm?.addEventListener("change", onLpInputChanged);

    $("lp-save-config-btn")?.addEventListener("click", async () => {
      await saveLpConfig(false);
    });

    $("lp-toggle-body-btn")?.addEventListener("click", () => {
      const formEl = $("lp-strategy-form");
      const btnEl = $("lp-toggle-body-btn");
      if (!formEl || !btnEl) return;
      const isHidden = formEl.classList.toggle("hidden");
      btnEl.textContent = isHidden ? "🔽 展開參數" : "🔼 收合參數";
    });

    $("lp-auto-toggle-btn")?.addEventListener("click", async () => {
      const curr = Boolean(appState?.lp_mm?.config?.auto_enabled);
      try {
        const res = await apiPost("/api/lp/config", { auto_enabled: !curr });
        renderState(res.state);
        showToast(!curr ? "💧 自動選盤做市已啟動！" : "⏸️ 自動選盤做市已暫停");
      } catch (err) {
        showToast(`❌ ${err.message}`);
      }
    });

    $("lp-test-killswitch-btn")?.addEventListener("click", async () => {
      try {
        const res = await apiPost("/api/lp/simulate-killswitch", { mode: activeLedgerMode });
        renderState(res.state);
        const velThresh = res.state?.lp_mm?.config?.max_velocity_cents ?? 2.5;
        showToast(`⚡ 防毒流測試成功！「${res.target?.event_title}」偵測中價瞬跳 > ${velThresh}¢，已毫秒級自動撤單並轉移槽位！`);
      } catch (err) {
        showToast(`❌ ${err.message}`);
      }
    });

    $("lp-reset-paper-btn")?.addEventListener("click", async () => {
      try {
        const res = await apiPost("/api/lp/reset");
        renderState(res.state);
        showToast("🔄 已重置 $500.00 USDC 模擬被動做市帳戶與 4 個雙邊掛單槽位！");
      } catch (err) {
        showToast(`❌ ${err.message}`);
      }
    });

    $("lp-markets-tbody")?.addEventListener("click", async (e) => {
      const btn = e.target.closest(".lp-deploy-btn");
      if (!btn) return;
      btn.disabled = true;
      try {
        const res = await apiPost("/api/lp/deploy", {
          market_id: btn.dataset.marketId,
          mode: activeLedgerMode,
        });
        renderState(res.state);
        showToast(`💧 已於「${btn.dataset.eventTitle}」建立雙邊 POST_ONLY 被動做市掛單！`);
      } catch (err) {
        showToast(`❌ 掛單失敗：${err.message}`);
      } finally {
        btn.disabled = false;
      }
    });

    $("lp-active-slots-view")?.addEventListener("click", async (e) => {
      const btn = e.target.closest(".lp-cancel-btn");
      if (!btn) return;
      btn.disabled = true;
      try {
        const res = await apiPost("/api/lp/cancel", {
          slot_id: Number(btn.dataset.slotId),
          mode: activeLedgerMode,
        });
        renderState(res.state);
        showToast("🛑 已撤銷雙邊掛單並釋放做市槽位資金！");
      } catch (err) {
        showToast(`❌ 撤單失敗：${err.message}`);
      } finally {
        btn.disabled = false;
      }
    });

    $("tab-lp-active-btn")?.addEventListener("click", () => {
      $("tab-lp-active-btn")?.classList.add("active");
      $("tab-lp-history-btn")?.classList.remove("active");
      $("lp-active-slots-view")?.classList.remove("hidden");
      $("lp-history-slots-view")?.classList.add("hidden");
    });

    $("tab-lp-history-btn")?.addEventListener("click", () => {
      $("tab-lp-history-btn")?.classList.add("active");
      $("tab-lp-active-btn")?.classList.remove("active");
      $("lp-history-slots-view")?.classList.remove("hidden");
      $("lp-active-slots-view")?.classList.add("hidden");
    });

    // HK Temp Sub-navigation (Today's 11 Buckets vs Weather Records Comparison)
    document.querySelectorAll(".subnav-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        document.querySelectorAll(".subnav-btn").forEach((b) => b.classList.remove("active"));
        btn.classList.add("active");
        activeHkTab = btn.dataset.hktab || "overview";
        if (activeHkTab === "records") {
          $("hktab-records").classList.remove("hidden");
          $("hktab-overview").classList.add("hidden");
        } else {
          $("hktab-overview").classList.remove("hidden");
          $("hktab-records").classList.add("hidden");
        }
      });
    });

    // HK Temp Auto-Toggle
    $("hk-auto-toggle-btn")?.addEventListener("click", async () => {
      const currEnabled = Boolean(appState?.hk_temp?.engine?.autoEnabled);
      try {
        const res = await apiPost("/api/hk-temp/config", { auto_enabled: !currEnabled });
        renderState(res.state);
        showToast(!currEnabled ? "🌡️ 香港氣溫 70%+ 自動交易已啟動！" : "⏸️ 香港氣溫自動交易已暫停");
      } catch (err) {
        showToast(`❌ ${err.message}`);
      }
    });

    // HK Temp Manual Bet Button Delegation (Scoped to activeLedgerMode)
    $("hk-outcomes-container")?.addEventListener("click", async (e) => {
      const btn = e.target.closest(".hk-manual-bet-btn");
      if (!btn) return;
      btn.disabled = true;
      try {
        const res = await apiPost("/api/hk-temp/bet", {
          outcome_name: btn.dataset.outcomeName,
          mode: activeLedgerMode,
        });
        renderState(res.state);
        activatePosTab("hk");
        showToast(`🌡️ 已於「${activeLedgerMode === "LIVE" ? "實盤" : "模擬盤"}」投注香港最高氣溫選項「${btn.dataset.outcomeName}」！`);
      } catch (err) {
        showToast(`❌ 下單失敗：${err.message}`);
      } finally {
        btn.disabled = false;
      }
    });

    // Unified Strategy Form (Real-time description updates + auto-sync + manual save)
    const form = $("strategy-form");
    let autoSaveTimer = null;

    async function saveStrategyConfig(silent = false) {
      if (autoSaveTimer) {
        clearTimeout(autoSaveTimer);
        autoSaveTimer = null;
      }
      try {
        const minP = Number($("cfg-min-prob").value) || 70;
        const maxP = Number($("cfg-max-prob").value) || 95;
        const hkP = Number($("hk-pricemin-input").value) || 70;
        const sportsPayload = {
          min_win_prob: minP / 100.0,
          max_win_prob: maxP / 100.0,
          bet_amount_usdc: Number($("cfg-bet-amount").value) || 25,
          daily_budget_usdc: Number($("cfg-daily-budget").value) || 300,
          min_liquidity_usdc: Number($("cfg-min-liquidity").value) || 0,
          max_spread: (Number($("cfg-max-spread").value) || 5.0) / 100.0,
          auto_take_profit_enabled: $("cfg-tp-enabled").checked,
          take_profit_prob: (Number($("cfg-tp-prob").value) || 93) / 100.0,
          auto_stop_loss_enabled: $("cfg-sl-enabled").checked,
          stop_loss_minutes_before_end: Math.max(0, Number($("cfg-sl-minutes")?.value ?? 30)),
          stop_loss_prob: (Number($("cfg-sl-prob").value) || 55) / 100.0,
          exclude_high_upset_sports: $("cfg-exclude-upset") ? Boolean($("cfg-exclude-upset").checked) : true,
          scan_interval_sec: Number($("cfg-scan-interval").value) || 5,
          max_open_positions: Number($("cfg-max-positions").value) || 15,
        };
        const hkPayload = {
          stake_usdc: Number($("hk-stake-input").value) || 10,
          price_min: hkP / 100.0,
          gate_hour_hkt: Number($("hk-gatehour-input").value ?? 11),
        };

        await apiPost("/api/config", sportsPayload);
        const res = await apiPost("/api/hk-temp/config", hkPayload);
        formDirty = false;
        renderState(res.state);
        if (!silent) {
          showToast(`✅ 雙引擎設定已儲存生效：運動 ${minP}%~${maxP}% ($${sportsPayload.bet_amount_usdc}) · 氣溫 ${hkP}%+ ($${hkPayload.stake_usdc})`);
        }
      } catch (err) {
        if (!silent) {
          showToast(`❌ 儲存失敗：${err.message}`);
        }
      }
    }

    function onStrategyInputChanged() {
      formDirty = true;
      updateStrategyDescriptionsFromInputs();
      if (appState) {
        const previewConfig = {
          ...appState.config,
          min_win_prob: (Number($("cfg-min-prob")?.value) || 70) / 100.0,
          max_win_prob: (Number($("cfg-max-prob")?.value) || 80) / 100.0,
          bet_amount_usdc: Number($("cfg-bet-amount")?.value) || 20,
        };
        renderMarketsTable(appState.markets || [], previewConfig);
      }
      if (autoSaveTimer) clearTimeout(autoSaveTimer);
      autoSaveTimer = setTimeout(() => {
        saveStrategyConfig(true);
      }, 700);
    }

    form?.addEventListener("input", onStrategyInputChanged);
    form?.addEventListener("change", onStrategyInputChanged);

    $("save-config-btn")?.addEventListener("click", async () => {
      await saveStrategyConfig(false);
    });

    $("toggle-strategy-body-btn")?.addEventListener("click", () => {
      const formEl = $("strategy-form");
      const btnEl = $("toggle-strategy-body-btn");
      if (!formEl || !btnEl) return;
      const isHidden = formEl.classList.toggle("hidden");
      btnEl.textContent = isHidden ? "🔽 展開參數" : "🔼 收合參數";
    });

    $("bot-toggle-btn")?.addEventListener("click", async () => {
      if (!appState) return;
      try {
        const nextState = !appState.config.bot_enabled;
        const res = await apiPost("/api/config", { bot_enabled: nextState });
        renderState(res.state);
        showToast(nextState ? "🏟️ 運動 70%+ 自動投注已啟動！" : "⏸️ 運動自動投注已暫停");
      } catch (err) {
        showToast(`❌ 操作失敗：${err.message}`);
      }
    });

    // Switch to PAPER Ledger (Trial Run)
    $("mode-paper-btn")?.addEventListener("click", async () => {
      activeLedgerMode = "PAPER";
      if (appState) renderState(appState);
      try {
        if (appState?.auth?.is_admin && appState?.config?.trading_mode === "LIVE") {
          await apiPost("/api/config", { trading_mode: "PAPER" });
          const res = await apiPost("/api/hk-temp/config", { mode: "PAPER" });
          renderState(res.state);
        }
        showToast("🧪 已切換至「模擬盤 (Trial Run)」獨立帳本與策略測試模式");
      } catch (err) {
        showToast(`❌ ${err.message}`);
      }
    });

    // Switch to LIVE Ledger (Requires Admin Password Login)
    $("mode-live-btn")?.addEventListener("click", async () => {
      if (!appState?.auth?.is_admin) {
        showToast("🔒 實盤專區受密碼保護，請輸入管理員密碼解鎖實盤獨立帳本");
        openAuthModal();
        return;
      }

      activeLedgerMode = "LIVE";
      renderState(appState);

      if (appState?.clob_status?.ready_for_live) {
        try {
          await apiPost("/api/config", { trading_mode: "LIVE" });
          const res = await apiPost("/api/hk-temp/config", { mode: "LIVE" });
          renderState(res.state);
          showToast("⚡ 已進入「實盤專區 (LIVE)」並同步開啟 CLOB 實盤自動下單！");
        } catch (err) {
          showToast(`⚠️ ${err.message}`);
        }
      } else {
        showToast("🔓 已切換至「實盤專區 (LIVE)」獨立帳本！如需自動下單，請於右上角設定 CLOB 錢包私鑰。");
      }
    });

    // Admin Auth Modal Events (Login / Change Password / Logout)
    $("open-auth-modal-btn")?.addEventListener("click", () => openAuthModal());
    $("close-auth-modal-btn")?.addEventListener("click", () => $("auth-modal")?.close());

    $("auth-login-form")?.addEventListener("submit", async (e) => {
      e.preventDefault();
      const pw = $("auth-password-input")?.value || "";
      try {
        const res = await apiPost("/api/auth/login", { password: pw });
        adminToken = res.token || "";
        localStorage.setItem("poly_admin_token", adminToken);
        $("auth-password-input").value = "";
        if (ws && ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ type: "AUTH", token: adminToken }));
        }
        activeLedgerMode = "LIVE";
        renderState(res.state);
        $("auth-modal")?.close();

        if (res.state?.clob_status?.ready_for_live) {
          try {
            await apiPost("/api/config", { trading_mode: "LIVE" });
            const r2 = await apiPost("/api/hk-temp/config", { mode: "LIVE" });
            renderState(r2.state);
          } catch (_) {}
        }
        showToast("🔓 實盤管理員驗證成功！已切換至「⚡ 實盤專區」獨立帳本。");
      } catch (err) {
        showToast(`${err.message}`);
      }
    });

    $("auth-change-pw-form")?.addEventListener("submit", async (e) => {
      e.preventDefault();
      try {
        const res = await apiPost("/api/auth/password", {
          current_password: $("auth-curr-pw").value,
          new_password: $("auth-new-pw").value,
        });
        adminToken = res.token || "";
        localStorage.setItem("poly_admin_token", adminToken);
        $("auth-curr-pw").value = "";
        $("auth-new-pw").value = "";
        if (ws && ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ type: "AUTH", token: adminToken }));
        }
        renderState(res.state);
        showToast("🔑 實盤管理員密碼已成功更新！");
      } catch (err) {
        showToast(`❌ 密碼修改失敗：${err.message}`);
      }
    });

    $("auth-logout-btn")?.addEventListener("click", async () => {
      try {
        const res = await apiPost("/api/auth/logout");
        adminToken = "";
        localStorage.removeItem("poly_admin_token");
        activeLedgerMode = "PAPER";
        if (ws && ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ type: "AUTH", token: "" }));
        }
        renderState(res.state);
        $("auth-modal")?.close();
        showToast("🔒 已登出並鎖定實盤專區，畫面已切回模擬盤 (Trial Run)");
      } catch (err) {
        showToast(`❌ ${err.message}`);
      }
    });

    $("data-source-select")?.addEventListener("change", async (e) => {
      try {
        const res = await apiPost("/api/config", { data_source_mode: e.target.value });
        renderState(res.state);
        showToast(`📡 數據源已切換為：${e.target.value}`);
      } catch (err) {
        showToast(`❌ ${err.message}`);
      }
    });

    $("sim-breakout-btn")?.addEventListener("click", async () => {
      try {
        const res = await apiPost("/api/simulate-breakout");
        renderState(res.state);
        const s = res.surge;
        showToast(`🔥 突破觸發！${s.outcome_label} 勝率躍升至 ${(s.new_prob * 100).toFixed(1)}%，機器人已執行掃描與自動下單！`);
      } catch (err) {
        showToast(`❌ ${err.message}`);
      }
    });

    $("manual-scan-btn")?.addEventListener("click", async () => {
      try {
        const res = await apiPost("/api/scan");
        renderState(res.state);
        showToast("🔄 已完成運動與香港氣溫盤口即時掃描");
      } catch (err) {
        showToast(`❌ ${err.message}`);
      }
    });

    $("reset-paper-btn")?.addEventListener("click", async () => {
      try {
        const res = await apiPost("/api/portfolio/reset", { initial_balance: 1000 });
        renderState(res.state);
        showToast("🔄 模擬帳戶已重置為 $1,000.00 USDC（實盤數據不受影響）");
      } catch (err) {
        showToast(`❌ ${err.message}`);
      }
    });

    $("sport-tabs")?.addEventListener("click", (e) => {
      const btn = e.target.closest(".sport-tab");
      if (!btn) return;
      document.querySelectorAll(".sport-tab").forEach((el) => el.classList.remove("active"));
      btn.classList.add("active");
      selectedSport = btn.dataset.sport || "ALL";
      if (appState) renderMarketsTable(appState.markets || [], appState.config);
    });

    $("filter-only-70")?.addEventListener("change", (e) => {
      onlyAbove70 = e.target.checked;
      if (appState) renderMarketsTable(appState.markets || [], appState.config);
    });

    $("market-search-input")?.addEventListener("input", (e) => {
      searchQuery = e.target.value.trim();
      if (appState) renderMarketsTable(appState.markets || [], appState.config);
    });

    $("markets-tbody")?.addEventListener("click", async (e) => {
      const btn = e.target.closest(".manual-bet-btn");
      if (!btn) return;
      btn.disabled = true;
      try {
        const res = await apiPost("/api/bet", {
          market_id: btn.dataset.marketId,
          outcome_index: Number(btn.dataset.outcomeIndex || 0),
          trading_mode: activeLedgerMode,
        });
        renderState(res.state);
        activatePosTab("open");
        showToast(`⚡ 已於「${activeLedgerMode === "LIVE" ? "實盤" : "模擬盤"}」買入「${btn.dataset.outcomeLabel}」！`);
      } catch (err) {
        showToast(`❌ 下單失敗：${err.message}`);
      } finally {
        btn.disabled = false;
      }
    });

    $("open-positions-view")?.addEventListener("click", async (e) => {
      const btn = e.target.closest(".pos-close-btn");
      if (!btn) return;
      btn.disabled = true;
      try {
        const res = await apiPost(`/api/positions/${btn.dataset.id}/close`, {
          action: btn.dataset.action,
        });
        renderState(res.state);
        showToast("✅ 持倉已平倉結算！");
      } catch (err) {
        showToast(`❌ 平倉失敗：${err.message}`);
      } finally {
        btn.disabled = false;
      }
    });

    $("tab-open-pos-btn")?.addEventListener("click", () => activatePosTab("open"));
    $("tab-hk-pos-btn")?.addEventListener("click", () => activatePosTab("hk"));
    $("tab-closed-pos-btn")?.addEventListener("click", () => activatePosTab("closed"));

    const clobModal = $("clob-modal");
    $("open-clob-modal-btn")?.addEventListener("click", () => {
      if (!appState?.auth?.is_admin) {
        showToast("🔒 設定 CLOB 實盤錢包憑證需先輸入實盤管理員密碼");
        openAuthModal();
        return;
      }
      clobModal.showModal();
    });
    $("close-clob-modal-btn")?.addEventListener("click", () => clobModal.close());

    const deployModal = $("deploy-modal");
    $("open-deploy-modal-btn")?.addEventListener("click", () => deployModal.showModal());
    $("close-deploy-modal-btn")?.addEventListener("click", () => deployModal.close());

    $("clob-form")?.addEventListener("submit", async (e) => {
      e.preventDefault();
      try {
        const payload = {
          private_key: $("clob-private-key").value,
          funder_address: $("clob-funder-address").value,
          signature_type: Number($("clob-sig-type").value),
          api_key: $("clob-api-key").value,
          api_secret: $("clob-api-secret").value,
          api_passphrase: $("clob-api-passphrase").value,
        };
        const res = await apiPost("/api/clob/credentials", payload);
        $("clob-private-key").value = "";
        renderState(res.state);
        showToast("🔐 CLOB 錢包與 API 憑證已安全儲存");
      } catch (err) {
        showToast(`❌ 儲存失敗：${err.message}`);
      }
    });

    $("verify-clob-btn")?.addEventListener("click", async () => {
      const banner = $("clob-verify-banner");
      banner.textContent = "⏳ 正在驗證 Polymarket CLOB 憑證與連線...";
      try {
        if ($("clob-private-key").value.trim()) {
          await apiPost("/api/clob/credentials", {
            private_key: $("clob-private-key").value,
            funder_address: $("clob-funder-address").value,
            signature_type: Number($("clob-sig-type").value),
          });
          $("clob-private-key").value = "";
        }
        const resp = await fetch("/api/clob/verify", {
          method: "POST",
          headers: getAuthHeaders(),
        });
        const data = await resp.json();
        banner.textContent = (data.ok ? "✅ " : "⚠️ ") + (data.message || data.error || "");
      } catch (err) {
        banner.textContent = `❌ 驗證錯誤：${err.message}`;
      }
    });
  }

  async function init() {
    updateHktClock();
    setInterval(updateHktClock, 1000);
    bindEvents();
    try {
      const resp = await fetch("/api/state", { headers: getAuthHeaders() });
      if (resp.ok) {
        const state = await resp.json();
        renderState(state);
      }
    } catch (err) {
      console.error("Initial state fetch error", err);
    }
    connectWebSocket();
  }

  document.addEventListener("DOMContentLoaded", init);
})();
