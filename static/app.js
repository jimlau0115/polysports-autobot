(() => {
  let appState = null;
  let selectedSport = "ALL";
  let onlyAbove70 = false;
  let searchQuery = "";
  let formDirty = false;
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
      return d.toLocaleTimeString("zh-HK", { hour12: false });
    } catch {
      return isoStr;
    }
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

  async function apiPost(url, payload = {}) {
    const resp = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await resp.json();
    if (!resp.ok || data.ok === false) {
      throw new Error(data.error || data.message || `Request failed (${resp.status})`);
    }
    return data;
  }

  function renderState(state) {
    if (!state) return;
    appState = state;
    const { engine, config, portfolio, clob_status } = state;

    // 1. Topbar controls
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

    // Trading Mode Buttons
    const paperBtn = $("mode-paper-btn");
    const liveBtn = $("mode-live-btn");
    if (config.trading_mode === "LIVE") {
      paperBtn.className = "seg-btn";
      liveBtn.className = "seg-btn active active-live";
    } else {
      paperBtn.className = "seg-btn active";
      liveBtn.className = "seg-btn";
    }

    // Bot Toggle Button
    const botBtn = $("bot-toggle-btn");
    const botLabel = $("bot-toggle-label");
    if (config.bot_enabled) {
      botBtn.className = "btn btn-bot-active";
      botLabel.textContent = "自動投注：運行中 (ON)";
    } else {
      botBtn.className = "btn btn-bot-paused";
      botLabel.textContent = "自動投注：已暫停 (PAUSED)";
    }

    // 2. KPI Cards
    $("kpi-equity").textContent = fmtUsd(portfolio.paper_total_equity_usdc);
    $("kpi-cash").textContent = fmtUsd(portfolio.paper_cash_balance);
    $("kpi-open-value").textContent = fmtUsd(portfolio.open_market_value_usdc);

    const totalPnlEl = $("kpi-total-pnl");
    totalPnlEl.textContent = fmtUsd(portfolio.total_pnl_usdc, true);
    totalPnlEl.className = `kpi-value ${portfolio.total_pnl_usdc >= 0 ? "pnl-pos" : "pnl-neg"}`;

    const roiBadge = $("kpi-roi-badge");
    const roiSign = portfolio.roi_pct >= 0 ? "+" : "";
    roiBadge.textContent = `${roiSign}${portfolio.roi_pct.toFixed(2)}%`;
    roiBadge.className = `badge ${portfolio.roi_pct >= 0 ? "badge-success" : "badge-danger"}`;

    $("kpi-unrealized").textContent = fmtUsd(portfolio.unrealized_pnl_usdc, true);
    $("kpi-realized").textContent = fmtUsd(portfolio.realized_pnl_usdc, true);

    $("kpi-positions-count").textContent = `${portfolio.open_positions_count} / ${config.max_open_positions} 倉位`;
    $("kpi-winrate-tag").textContent =
      portfolio.closed_positions_count > 0 ? `勝率 ${portfolio.win_rate_pct}%` : "尚無結算";
    $("kpi-closed-stats").textContent = `${portfolio.wins} 勝 ${portfolio.losses} 敗`;
    $("kpi-invested").textContent = fmtUsd(portfolio.open_invested_usdc);

    const budgetPct =
      portfolio.daily_budget_usdc > 0
        ? Math.min(100, Math.round((portfolio.today_spend_usdc / portfolio.daily_budget_usdc) * 100))
        : 0;
    $("kpi-budget-pct").textContent = `${budgetPct}%`;
    $("kpi-daily-spend").textContent = `${fmtUsd(portfolio.today_spend_usdc)} / $${portfolio.daily_budget_usdc.toFixed(0)}`;
    $("kpi-budget-bar").style.width = `${budgetPct}%`;

    const srcBadge = $("kpi-source-badge");
    if (engine.active_data_source === "LIVE_GAMMA_API") {
      srcBadge.textContent = "LIVE GAMMA API";
      srcBadge.className = "badge badge-success";
    } else {
      srcBadge.textContent = "動態運動模擬流";
      srcBadge.className = "badge badge-accent";
    }

    $("kpi-markets-summary").textContent = `${engine.above_threshold_count} / ${engine.total_sports_markets} 場 >= ${Math.round(config.min_win_prob * 100)}%`;
    $("kpi-active-rule-summary").textContent = `條件：勝率 ${Math.round(config.min_win_prob * 100)}% ~ ${Math.round(config.max_win_prob * 100)}% 立即買入 $${config.bet_amount_usdc} USDC`;
    $("last-scan-timestamp").textContent = `最後更新：${fmtTime(engine.last_scan_at)}`;

    // 3. Strategy Form (only populate if user isn't currently editing)
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
      $("cfg-sl-prob").value = (config.stop_loss_prob * 100).toFixed(1).replace(/\.0$/, "");
      $("cfg-scan-interval").value = config.scan_interval_sec;
      $("cfg-max-positions").value = config.max_open_positions;
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

    // 5. Render Markets, Positions, and Logs
    renderMarketsTable(state.markets || [], config);
    renderPositions(state.open_positions || [], state.closed_positions || []);
    renderLogs(state.logs || []);
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
        const ev = m.rule_evaluation || {};
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

        return `
          <tr class="${rowClass}">
            <td>
              <div class="market-meta">
                <span class="sport-chip">${escapeHtml(m.sport_icon)} ${escapeHtml(m.sport_label)}</span>
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
                class="btn btn-xs btn-bet-now manual-bet-btn"
                data-market-id="${escapeHtml(m.market_id)}"
                data-outcome-index="${Number(topOutcome.index || 0)}"
                data-outcome-label="${escapeHtml(topOutcome.label)}"
              >
                ⚡ 買入「${escapeHtml(topOutcome.label)}」($${betAmt})
              </button>
            </td>
          </tr>
        `;
      })
      .join("");
  }

  function renderPositions(openPositions, closedPositions) {
    $("open-pos-badge").textContent = openPositions.length;
    $("closed-pos-badge").textContent = closedPositions.length;

    const openEl = $("open-positions-view");
    if (openPositions.length === 0) {
      openEl.innerHTML = `<div class="empty-cell">目前尚無活躍持倉。當運動項目勝率 &gt;= 70% 時將自動建倉。</div>`;
    } else {
      openEl.innerHTML = openPositions
        .map((p) => {
          const pnl = Number(p.unrealized_pnl_usdc || 0);
          const pnlClass = pnl >= 0 ? "pnl-pos" : "pnl-neg";
          return `
            <div class="position-card">
              <div class="pos-top">
                <div>
                  <div class="pos-title">${escapeHtml(p.event_title)}</div>
                  <div class="pos-outcome">🎯 投注選項：${escapeHtml(p.outcome_label)} (${escapeHtml(p.trading_mode)})</div>
                </div>
                <span class="badge ${pnl >= 0 ? "badge-success" : "badge-danger"}">${fmtUsd(pnl, true)}</span>
              </div>
              <div class="pos-metrics">
                <div>進場勝率：<strong>${(Number(p.entry_prob) * 100).toFixed(1)}%</strong></div>
                <div>目前勝率：<strong>${(Number(p.current_prob) * 100).toFixed(1)}%</strong></div>
                <div>持有股數：<strong>${Number(p.shares).toFixed(2)}</strong></div>
                <div>投入本金：<strong>$${Number(p.amount_usdc).toFixed(2)}</strong></div>
                <div>勝出可領：<strong>$${Number(p.shares).toFixed(2)}</strong></div>
                <div>觸發方式：<strong>${p.trigger_type === "AUTO_70_RULE" ? "70%+自動" : "手動"}</strong></div>
              </div>
              <div class="pos-actions">
                <button type="button" class="btn btn-xs btn-outline pos-close-btn" data-id="${p.id}" data-action="MARKET_CLOSE">
                  市價平倉
                </button>
                <button type="button" class="btn btn-xs btn-bet-now pos-close-btn" data-id="${p.id}" data-action="SETTLE_WIN" title="模擬該球隊最終贏得比賽 (以 100¢ 結算)">
                  🏆 勝出結算 (100¢)
                </button>
                <button type="button" class="btn btn-xs btn-ghost pos-close-btn" data-id="${p.id}" data-action="SETTLE_LOSS" title="模擬該球隊落敗 (以 0¢ 結算)">
                  落敗 (0¢)
                </button>
              </div>
            </div>
          `;
        })
        .join("");
    }

    const closedEl = $("closed-positions-view");
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
    ws = new WebSocket(`${proto}//${window.location.host}/ws`);
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

  function bindEvents() {
    // Track form dirty state
    const form = $("strategy-form");
    form.addEventListener("input", () => {
      formDirty = true;
    });

    // Save config button
    $("save-config-btn").addEventListener("click", async () => {
      try {
        const payload = {
          min_win_prob: Number($("cfg-min-prob").value) / 100.0,
          max_win_prob: Number($("cfg-max-prob").value) / 100.0,
          bet_amount_usdc: Number($("cfg-bet-amount").value),
          daily_budget_usdc: Number($("cfg-daily-budget").value),
          min_liquidity_usdc: Number($("cfg-min-liquidity").value),
          max_spread: Number($("cfg-max-spread").value) / 100.0,
          auto_take_profit_enabled: $("cfg-tp-enabled").checked,
          take_profit_prob: Number($("cfg-tp-prob").value) / 100.0,
          auto_stop_loss_enabled: $("cfg-sl-enabled").checked,
          stop_loss_prob: Number($("cfg-sl-prob").value) / 100.0,
          scan_interval_sec: Number($("cfg-scan-interval").value),
          max_open_positions: Number($("cfg-max-positions").value),
        };
        const res = await apiPost("/api/config", payload);
        formDirty = false;
        renderState(res.state);
        showToast("✅ 策略與風控規則已儲存並立即生效！");
      } catch (err) {
        showToast(`❌ 儲存失敗：${err.message}`);
      }
    });

    // Bot Enable/Pause Toggle
    $("bot-toggle-btn").addEventListener("click", async () => {
      if (!appState) return;
      try {
        const nextState = !appState.config.bot_enabled;
        const res = await apiPost("/api/config", { bot_enabled: nextState });
        renderState(res.state);
        showToast(nextState ? "🤖 70%+ 自動投注機器人已啟動！" : "⏸️ 自動投注機器人已暫停");
      } catch (err) {
        showToast(`❌ 操作失敗：${err.message}`);
      }
    });

    // Mode Switch (Paper vs Live)
    $("mode-paper-btn").addEventListener("click", async () => {
      try {
        const res = await apiPost("/api/config", { trading_mode: "PAPER" });
        renderState(res.state);
        showToast("🧪 已切換為「模擬交易 (Paper Trading)」模式");
      } catch (err) {
        showToast(`❌ ${err.message}`);
      }
    });

    $("mode-live-btn").addEventListener("click", async () => {
      try {
        const res = await apiPost("/api/config", { trading_mode: "LIVE" });
        renderState(res.state);
        showToast("⚡ 已切換為「Polymarket CLOB 真實自動下單」模式");
      } catch (err) {
        showToast(`⚠️ ${err.message}`);
        $("clob-modal").showModal();
      }
    });

    // Data source switcher
    $("data-source-select").addEventListener("change", async (e) => {
      try {
        const res = await apiPost("/api/config", { data_source_mode: e.target.value });
        renderState(res.state);
        showToast(`📡 數據源已切換為：${e.target.value}`);
      } catch (err) {
        showToast(`❌ ${err.message}`);
      }
    });

    // Simulate 70%+ Breakout Button
    $("sim-breakout-btn").addEventListener("click", async () => {
      try {
        const res = await apiPost("/api/simulate-breakout");
        renderState(res.state);
        const s = res.surge;
        showToast(`🔥 突破觸發！${s.outcome_label} 勝率躍升至 ${(s.new_prob * 100).toFixed(1)}%，機器人已執行掃描與自動下單！`);
      } catch (err) {
        showToast(`❌ ${err.message}`);
      }
    });

    // Manual Scan
    $("manual-scan-btn").addEventListener("click", async () => {
      try {
        const res = await apiPost("/api/scan");
        renderState(res.state);
        showToast("🔄 已完成即時盤口掃描");
      } catch (err) {
        showToast(`❌ ${err.message}`);
      }
    });

    // Reset Paper Account
    $("reset-paper-btn").addEventListener("click", async () => {
      try {
        const res = await apiPost("/api/portfolio/reset", { initial_balance: 1000 });
        renderState(res.state);
        showToast("🔄 模擬帳戶已重置為 $1,000.00 USDC，可重新測試策略");
      } catch (err) {
        showToast(`❌ ${err.message}`);
      }
    });

    // Sport Category Tabs
    $("sport-tabs").addEventListener("click", (e) => {
      const btn = e.target.closest(".sport-tab");
      if (!btn) return;
      document.querySelectorAll(".sport-tab").forEach((el) => el.classList.remove("active"));
      btn.classList.add("active");
      selectedSport = btn.dataset.sport || "ALL";
      if (appState) renderMarketsTable(appState.markets || [], appState.config);
    });

    // Filter >= 70% & Search
    $("filter-only-70").addEventListener("change", (e) => {
      onlyAbove70 = e.target.checked;
      if (appState) renderMarketsTable(appState.markets || [], appState.config);
    });

    $("market-search-input").addEventListener("input", (e) => {
      searchQuery = e.target.value.trim();
      if (appState) renderMarketsTable(appState.markets || [], appState.config);
    });

    // Delegate Manual Bet button clicks in table
    $("markets-tbody").addEventListener("click", async (e) => {
      const btn = e.target.closest(".manual-bet-btn");
      if (!btn) return;
      btn.disabled = true;
      try {
        const res = await apiPost("/api/bet", {
          market_id: btn.dataset.marketId,
          outcome_index: Number(btn.dataset.outcomeIndex || 0),
        });
        renderState(res.state);
        showToast(`⚡ 已立即買入「${btn.dataset.outcomeLabel}」！`);
      } catch (err) {
        showToast(`❌ 下單失敗：${err.message}`);
      } finally {
        btn.disabled = false;
      }
    });

    // Delegate Position Close/Settle clicks
    $("open-positions-view").addEventListener("click", async (e) => {
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

    // Open / Closed Positions Tabs
    $("tab-open-pos-btn").addEventListener("click", () => {
      $("tab-open-pos-btn").classList.add("active");
      $("tab-closed-pos-btn").classList.remove("active");
      $("open-positions-view").classList.remove("hidden");
      $("closed-positions-view").classList.add("hidden");
    });

    $("tab-closed-pos-btn").addEventListener("click", () => {
      $("tab-closed-pos-btn").classList.add("active");
      $("tab-open-pos-btn").classList.remove("active");
      $("closed-positions-view").classList.remove("hidden");
      $("open-positions-view").classList.add("hidden");
    });

    // Modals (CLOB & Deploy Guide)
    const clobModal = $("clob-modal");
    $("open-clob-modal-btn").addEventListener("click", () => clobModal.showModal());
    $("close-clob-modal-btn").addEventListener("click", () => clobModal.close());

    const deployModal = $("deploy-modal");
    $("open-deploy-modal-btn").addEventListener("click", () => deployModal.showModal());
    $("close-deploy-modal-btn").addEventListener("click", () => deployModal.close());

    // Save CLOB credentials
    $("clob-form").addEventListener("submit", async (e) => {
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

    // Verify CLOB Connection
    $("verify-clob-btn").addEventListener("click", async () => {
      const banner = $("clob-verify-banner");
      banner.textContent = "⏳ 正在驗證 Polymarket CLOB 憑證與連線...";
      try {
        // Save first if user typed a new private key
        if ($("clob-private-key").value.trim()) {
          await apiPost("/api/clob/credentials", {
            private_key: $("clob-private-key").value,
            funder_address: $("clob-funder-address").value,
            signature_type: Number($("clob-sig-type").value),
          });
          $("clob-private-key").value = "";
        }
        const resp = await fetch("/api/clob/verify", { method: "POST" });
        const data = await resp.json();
        banner.textContent = (data.ok ? "✅ " : "⚠️ ") + data.message;
      } catch (err) {
        banner.textContent = `❌ 驗證錯誤：${err.message}`;
      }
    });
  }

  async function init() {
    bindEvents();
    try {
      const resp = await fetch("/api/state");
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
