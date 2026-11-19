(() => {
  let appState = null;
  let activeModule = "SPORTS"; // "SPORTS" or "HKTEMP"
  let activeHkTab = "overview"; // "overview" or "records"
  let selectedSport = "ALL";
  let onlyAbove70 = false;
  let searchQuery = "";
  let formDirty = false;
  let hkFormDirty = false;
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
    const { engine, config, portfolio, clob_status, hk_temp } = state;

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
      botLabel.textContent = "運動自動投注：運行中 (ON)";
    } else {
      botBtn.className = "btn btn-bot-paused";
      botLabel.textContent = "運動自動投注：已暫停 (PAUSED)";
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

    // 3. Strategy Form
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

    // 5. Render Sports Markets, Positions, Logs, and HK Temperature Module
    renderMarketsTable(state.markets || [], config);
    renderPositions(state.open_positions || [], state.closed_positions || []);
    renderLogs(state.logs || []);
    if (hk_temp) {
      renderHkTempModule(hk_temp);
    }
  }

  function renderHkTempModule(hk) {
    const curr = hk.current || {};
    const fc = hk.forecast || {};
    const eng = hk.engine || {};
    const sum = hk.summary || {};
    const mkt = hk.market || {};

    // Top badges
    const navBadge = $("hktemp-nav-badge");
    if (navBadge) {
      navBadge.textContent = curr.tempC != null ? `${curr.tempC}°C` : "氣溫";
    }

    const obsBadge = $("hk-obs-status-badge");
    if (obsBadge) {
      obsBadge.textContent = hk.observatoryState === "live" ? "天文臺：即時" : "天文臺：延遲";
      obsBadge.className = `badge ${hk.observatoryState === "live" ? "badge-success" : "badge-warn"}`;
    }

    const polyBadge = $("hk-poly-status-badge");
    if (polyBadge) {
      polyBadge.textContent = hk.polymarketState === "live" ? "Polymarket：即時連線" : "Polymarket：中斷";
      polyBadge.className = `badge ${hk.polymarketState === "live" ? "badge-accent" : "badge-danger"}`;
    }

    // Phase Banner
    const phaseBox = $("hk-phase-banner");
    if (phaseBox) {
      phaseBox.className = `hk-phase-box phase-${eng.phase || "monitoring"}`;
    }
    $("hk-phase-label").textContent = eng.phaseLabel || "監測中";
    $("hk-phase-message").textContent = eng.message || "";

    // Weather KPI Cards
    $("hk-current-temp").textContent = curr.tempC != null ? `${curr.tempC}°C` : "—";
    const staleBadge = $("hk-current-stale-badge");
    if (curr.stale) {
      staleBadge.textContent = `延遲 (${curr.ageMinutes} 分鐘前)`;
      staleBadge.className = "badge badge-warn";
    } else {
      staleBadge.textContent = "即時觀測";
      staleBadge.className = "badge badge-success";
    }
    $("hk-current-detail").textContent = `觀測時間 ${fmtDateTimeHkt(curr.recordTime)} · 測站：${curr.place || "香港天文臺"}`;

    $("hk-forecast-date").textContent = `預報 ${fc.forecastDate || hk.marketDate}`;
    $("hk-forecast-temp").textContent =
      fc.maxC != null ? `最高 ${fc.maxC}°C` + (fc.minC != null ? ` / 最低 ${fc.minC}°C` : "") : "—";
    $("hk-forecast-desc").textContent = fc.weather || "香港天文臺本港地區天氣預報";

    $("hk-official-max").textContent = hk.officialMax?.label || "觀測中";
    $("hk-max-time").textContent = hk.maxTime?.label || "—";
    $("hk-max-time-note").textContent = hk.maxTime?.annotation || "午夜至今最高氣溫首次出現時間";

    $("hk-mode-tag").textContent = eng.mode === "live" ? "實盤 (LIVE)" : "模擬盤 (PAPER)";
    $("hk-balance-val").textContent = fmtUsd(eng.paperCash);
    $("hk-open-count").textContent = String(sum.openCount ?? 0);
    $("hk-exposure-val").textContent = `${Number(sum.exposureUsdc || 0).toFixed(2)} USDC`;

    // HK Temp Control Form
    const autoBtn = $("hk-auto-toggle-btn");
    if (eng.autoEnabled) {
      autoBtn.className = "btn btn-bot-active";
      autoBtn.textContent = "🤖 自動交易已開啟（點擊停止自動交易）";
    } else {
      autoBtn.className = "btn btn-bot-paused";
      autoBtn.textContent = "⏸️ 自動交易已停止（點擊開始自動交易）";
    }

    if (!hkFormDirty) {
      $("hk-mode-select").value = (eng.mode || "paper").toUpperCase();
      $("hk-stake-input").value = eng.stakeUsdc ?? 10;
      $("hk-pricemin-input").value = Math.round((eng.priceMin ?? 0.75) * 100);
      $("hk-gatehour-input").value = hk.gateHour ?? 11;
    }
    $("hk-rule-hour-text").textContent = `${String(hk.gateHour ?? 11).padStart(2, "0")}:00`;
    $("hk-rule-prob-text").textContent = `${Math.round((eng.priceMin ?? 0.75) * 100)}%`;

    // Market Title & Temperature Outcomes
    $("hk-market-title").textContent = `${mkt.title || "Highest temperature in Hong Kong"} (${hk.marketDate})`;
    const mktBadge = $("hk-market-status-badge");
    mktBadge.textContent = mkt.statusLabel || "開放";
    mktBadge.className = `badge ${mkt.status === "open" ? "badge-success" : "badge-warn"}`;

    const outcomesEl = $("hk-outcomes-container");
    const outcomes = mkt.outcomes || [];
    const priceMin = Number(eng.priceMin ?? 0.75);
    const stakeVal = Number(eng.stakeUsdc ?? 10);

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

              <div class="hk-bar-wrap" title="紅線為 ${Math.round(priceMin * 100)}% 觸發門檻">
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

    // HK Trades Table
    const tradesTbody = $("hk-trades-tbody");
    const trades = hk.trades || [];
    if (trades.length === 0) {
      tradesTbody.innerHTML = `<tr><td colspan="10" class="empty-cell">目前沒有香港氣溫市場的委託紀錄。當香港時間 ${String(hk.gateHour ?? 11).padStart(2, "0")}:00 後選項達到 ${Math.round(priceMin * 100)}% 時將自動下單。</td></tr>`;
    } else {
      tradesTbody.innerHTML = trades
        .map((t) => {
          const pnl = Number(t.pnl_usdc || 0);
          return `
            <tr>
              <td>${escapeHtml(fmtDateTimeHkt(t.created_at))}</td>
              <td><span class="badge badge-outline">${t.mode === "live" ? "實盤" : "模擬"}</span></td>
              <td>${escapeHtml(t.market_date)}</td>
              <td><strong>${escapeHtml(t.outcome_name)}</strong></td>
              <td>${(Number(t.price) * 100).toFixed(1)}%</td>
              <td>${(Number(t.current_price) * 100).toFixed(1)}%</td>
              <td>$${Number(t.stake_usdc).toFixed(2)}</td>
              <td>${Number(t.size_shares).toFixed(2)}</td>
              <td>${t.status === "filled" ? "✅ 已成交" : escapeHtml(t.status)}</td>
              <td class="${pnl >= 0 ? "pnl-pos" : "pnl-neg"}">${fmtUsd(pnl, true)}</td>
            </tr>
          `;
        })
        .join("");
    }

    // Historical Weather Comparison Table
    const weatherTbody = $("hk-weather-records-tbody");
    const records = hk.weatherRecords || [];
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
    // Primary Module Switcher (Sports vs HK Temperature)
    $("module-sports-btn").addEventListener("click", () => {
      activeModule = "SPORTS";
      $("module-sports-btn").classList.add("active");
      $("module-hktemp-btn").classList.remove("active");
      $("view-sports").classList.remove("hidden");
      $("view-hktemp").classList.add("hidden");
    });

    $("module-hktemp-btn").addEventListener("click", () => {
      activeModule = "HKTEMP";
      $("module-hktemp-btn").classList.add("active");
      $("module-sports-btn").classList.remove("active");
      $("view-hktemp").classList.remove("hidden");
      $("view-sports").classList.add("hidden");
    });

    // HK Temp Sub-navigation (Overview vs Records)
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

    // HK Temp form dirty tracking
    ["hk-mode-select", "hk-stake-input", "hk-pricemin-input", "hk-gatehour-input"].forEach((id) => {
      const el = $(id);
      if (el) {
        el.addEventListener("input", () => {
          hkFormDirty = true;
        });
      }
    });

    // HK Temp Auto-Toggle
    $("hk-auto-toggle-btn").addEventListener("click", async () => {
      const currEnabled = Boolean(appState?.hk_temp?.engine?.autoEnabled);
      try {
        const res = await apiPost("/api/hk-temp/config", { auto_enabled: !currEnabled });
        renderState(res.state);
        showToast(!currEnabled ? "🌡️ 香港氣溫 75%+ 自動交易已啟動！" : "⏸️ 香港氣溫自動交易已停止");
      } catch (err) {
        showToast(`❌ ${err.message}`);
      }
    });

    // HK Temp Save Config
    $("hk-save-cfg-btn").addEventListener("click", async () => {
      try {
        const payload = {
          mode: $("hk-mode-select").value,
          stake_usdc: Number($("hk-stake-input").value),
          price_min: Number($("hk-pricemin-input").value) / 100.0,
          gate_hour_hkt: Number($("hk-gatehour-input").value),
        };
        const res = await apiPost("/api/hk-temp/config", payload);
        hkFormDirty = false;
        renderState(res.state);
        showToast("✅ 香港氣溫交易設定已儲存並立即生效！");
      } catch (err) {
        showToast(`❌ ${err.message}`);
        if (err.message.includes("CLOB")) {
          $("clob-modal").showModal();
        }
      }
    });

    // HK Temp Manual Bet Button Delegation
    $("hk-outcomes-container").addEventListener("click", async (e) => {
      const btn = e.target.closest(".hk-manual-bet-btn");
      if (!btn) return;
      btn.disabled = true;
      try {
        const res = await apiPost("/api/hk-temp/bet", {
          outcome_name: btn.dataset.outcomeName,
        });
        renderState(res.state);
        showToast(`🌡️ 已投注香港最高氣溫選項「${btn.dataset.outcomeName}」！`);
      } catch (err) {
        showToast(`❌ 下單失敗：${err.message}`);
      } finally {
        btn.disabled = false;
      }
    });

    // Sports Strategy Form
    const form = $("strategy-form");
    form.addEventListener("input", () => {
      formDirty = true;
    });

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
        showToast("✅ 運動策略與風控規則已儲存並立即生效！");
      } catch (err) {
        showToast(`❌ 儲存失敗：${err.message}`);
      }
    });

    $("bot-toggle-btn").addEventListener("click", async () => {
      if (!appState) return;
      try {
        const nextState = !appState.config.bot_enabled;
        const res = await apiPost("/api/config", { bot_enabled: nextState });
        renderState(res.state);
        showToast(nextState ? "🤖 運動 70%+ 自動投注機器人已啟動！" : "⏸️ 運動自動投注機器人已暫停");
      } catch (err) {
        showToast(`❌ 操作失敗：${err.message}`);
      }
    });

    $("mode-paper-btn").addEventListener("click", async () => {
      try {
        const res = await apiPost("/api/config", { trading_mode: "PAPER" });
        await apiPost("/api/hk-temp/config", { mode: "PAPER" });
        renderState(res.state);
        showToast("🧪 已切換為「模擬交易 (Paper Trading)」模式");
      } catch (err) {
        showToast(`❌ ${err.message}`);
      }
    });

    $("mode-live-btn").addEventListener("click", async () => {
      try {
        const res = await apiPost("/api/config", { trading_mode: "LIVE" });
        await apiPost("/api/hk-temp/config", { mode: "LIVE" });
        renderState(res.state);
        showToast("⚡ 已切換為「Polymarket CLOB 真實自動下單」模式");
      } catch (err) {
        showToast(`⚠️ ${err.message}`);
        $("clob-modal").showModal();
      }
    });

    $("data-source-select").addEventListener("change", async (e) => {
      try {
        const res = await apiPost("/api/config", { data_source_mode: e.target.value });
        renderState(res.state);
        showToast(`📡 數據源已切換為：${e.target.value}`);
      } catch (err) {
        showToast(`❌ ${err.message}`);
      }
    });

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

    $("manual-scan-btn").addEventListener("click", async () => {
      try {
        const res = await apiPost("/api/scan");
        renderState(res.state);
        showToast("🔄 已完成運動與香港氣溫盤口即時掃描");
      } catch (err) {
        showToast(`❌ ${err.message}`);
      }
    });

    $("reset-paper-btn").addEventListener("click", async () => {
      try {
        const res = await apiPost("/api/portfolio/reset", { initial_balance: 1000 });
        renderState(res.state);
        showToast("🔄 模擬帳戶已重置為 $1,000.00 USDC");
      } catch (err) {
        showToast(`❌ ${err.message}`);
      }
    });

    $("sport-tabs").addEventListener("click", (e) => {
      const btn = e.target.closest(".sport-tab");
      if (!btn) return;
      document.querySelectorAll(".sport-tab").forEach((el) => el.classList.remove("active"));
      btn.classList.add("active");
      selectedSport = btn.dataset.sport || "ALL";
      if (appState) renderMarketsTable(appState.markets || [], appState.config);
    });

    $("filter-only-70").addEventListener("change", (e) => {
      onlyAbove70 = e.target.checked;
      if (appState) renderMarketsTable(appState.markets || [], appState.config);
    });

    $("market-search-input").addEventListener("input", (e) => {
      searchQuery = e.target.value.trim();
      if (appState) renderMarketsTable(appState.markets || [], appState.config);
    });

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

    const clobModal = $("clob-modal");
    $("open-clob-modal-btn").addEventListener("click", () => clobModal.showModal());
    $("close-clob-modal-btn").addEventListener("click", () => clobModal.close());

    const deployModal = $("deploy-modal");
    $("open-deploy-modal-btn").addEventListener("click", () => deployModal.showModal());
    $("close-deploy-modal-btn").addEventListener("click", () => deployModal.close());

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

    $("verify-clob-btn").addEventListener("click", async () => {
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
        const resp = await fetch("/api/clob/verify", { method: "POST" });
        const data = await resp.json();
        banner.textContent = (data.ok ? "✅ " : "⚠️ ") + data.message;
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
