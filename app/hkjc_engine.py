import asyncio
import json
import math
import os
import re
import time
from datetime import datetime, timedelta, timezone
from html import unescape
from typing import Any, Dict, List, Optional, Tuple

import aiohttp

from app.database import Database, utc_now_iso


HKJC_GRAPHQL_URL = "https://info.cld.hkjc.com/graphql/base/"
HKJC_RACECARD_URL = "https://racing.hkjc.com/racing/information/Chinese/racing/RaceCard.aspx"
HKJC_RESULTS_URL = "https://racing.hkjc.com/racing/information/Chinese/Racing/LocalResults.aspx"

# Exact whitelisted GraphQL queries extracted from bet.hkjc.com official bundle
RACE_MEETINGS_QUERY = """fragment raceFragment on Race {
    id
    no
    status
    raceName_en
    raceName_ch
    postTime
    country_en
    country_ch
    distance
    wageringFieldSize
    go_en
    go_ch
    ratingType
    raceTrack {
      description_en
      description_ch
    }
    raceCourse {
      description_en
      description_ch
      displayCode
    }
    claCode
    raceClass_en
    raceClass_ch
    judgeSigns {
      value_en
    }
  }
fragment racingBlockFragment on RaceMeeting {
    jpEsts: pmPools(oddsTypes: [WIN,PLA,TCE,TRI,FF,QTT,DT,TT,SixUP], filters: ["jackpot", "estimatedDividend"]) {
      leg {
        number
        races
      }
      oddsType
      jackpot
      estimatedDividend
      mergedPoolId
    }
    poolInvs: pmPools(oddsTypes: [WIN,PLA,QIN,QPL,CWA,CWB,CWC,IWN,FCT,TCE,TRI,FF,QTT,DBL,TBL,DT,TT,SixUP]) {
      id
      leg {
        races
      }
    }
    penetrometerReadings(filters:["first"]) {
      reading
      readingTime
    }
    hammerReadings(filters:["first"]) {
      reading
      readingTime
    }
    changeHistories(filters:["top3"]) {
      type
      time
      raceNo
      runnerNo
      horseName_ch
      horseName_en
      jockeyName_ch
      jockeyName_en
      scratchHorseName_ch
      scratchHorseName_en
      handicapWeight
      scrResvIndicator
    }
  }
query raceMeetings($date: String, $venueCode: String) {
    timeOffset {
      rc
    }
    activeMeetings : raceMeetings
    {
      id
      venueCode
      date
      status
      races {
        no
        postTime
        status
        wageringFieldSize
      }
      poolInvs: pmPools(oddsTypes: [WIN,PLA,QIN,QPL,CWA,CWB,CWC,IWN,FCT,TCE,TRI,FF,QTT,DBL,TBL,DT,TT,SixUP]) {
        status
      }
    }
    raceMeetings(date: $date, venueCode: $venueCode)
    {
      id
      status
      venueCode
      date
      totalNumberOfRace
      currentNumberOfRace
      dateOfWeek
      meetingType
      totalInvestment
      country {
        code
        namech
        nameen
        seq
      }
      races {
        ...raceFragment
        runners {
            id
            no
            standbyNo
            status
            name_ch
            name_en
            horse {
              id
              code
            }
            color
            barrierDrawNumber
            handicapWeight
            currentWeight
            currentRating
            internationalRating
            gearInfo
            racingColorFileName
            allowance
            trainerPreference
            last6run
            saddleClothNo
            trumpCard
            priority
            finalPosition
            deadHeat
            winOdds
            jockey {
              code
              name_en
              name_ch
            }
            trainer {
              code
              name_en
              name_ch
            }
        }
      }
      obSt: pmPools(oddsTypes: [WIN, PLA]) {
        leg {
          races
        }
        oddsType
        comingleStatus
      }
      poolInvs: pmPools(oddsTypes: [WIN,PLA,QIN,QPL,CWA,CWB,CWC,IWN,FCT,TCE,TRI,FF,QTT,DBL,TBL,DT,TT,SixUP]) {
        id
        leg {
          number
          races
        }
        status
        sellStatus
        oddsType
        investment
        mergedPoolId
        lastUpdateTime
      }
      ...racingBlockFragment
      pmPools(oddsTypes: []) {
        id
      }
      jkcInstNo : foPools(oddsTypes: [JKC], filters: ["top"]) {
        instNo
      }
      tncInstNo : foPools(oddsTypes: [TNC], filters: ["top"]) {
        instNo
      }
    }
}"""

PM_POOL_QUERY = """
      query sp4e_racing_pmPool($date: String, $venueCode: String, $oddsTypes: [OddsType], $raceNo: Int) {
          raceMeetings(date: $date, venueCode: $venueCode)
          {
            pmPools(oddsTypes: $oddsTypes, raceNo: $raceNo) {
              id
              status
              sellStatus
              oddsType
              lastUpdateTime
              oddsLastUpdateTime
              guarantee
              minTicketCost
              name_en
              name_ch
              leg {
                number
                races
              }
              cWinSelections {
                composite
                name_ch
                name_en
                starters
              }
              oddsNodes {
                combString
                oddsValue
                hotFavourite
                oddsDropValue
                bankerOdds {
                  combString
                  oddsValue
                }
              }
            }
          }
      }
"""

RESULT_MEETINGS_QUERY = """fragment racingFoPoolFragment on RacingFoPool {
    instNo
    poolId
    oddsType
    status
    sellStatus
    otherSelNo
    inplayUpTo
    expStartDateTime
    expStopDateTime
    raceStopSellNo
    raceStopSellStatus
    includeRaces
    excludeRaces
    lastUpdateTime
    selections {
      order
      number
      code
      name_en
      name_ch
      scheduleRides
      remainingRides
      points
      lineId
      combId
      combStatus
      openOdds
      prevOdds
      currentOdds
      results {
        raceNo
        points
        point1st
        point2nd
        point3rd
        dhRmk1st
        dhRmk2nd
        dhRmk3rd
        count1st
        count2nd
        count3rd
        count4th
        numerator4th
        denominator4th
      }
    }
    otherSelections {
      order
      code
      name_en
      name_ch
      scheduleRides
      remainingRides
      points
      results {
        raceNo
        points
        point1st
        point2nd
        point3rd
        dhRmk1st
        dhRmk2nd
        dhRmk3rd
        count1st
        count2nd
        count3rd
        count4th
        numerator4th
        denominator4th
      }
    }
  }
  query resultMeetings($date: String, $venueCode: String, $foOddsTypes: [OddsType], $foFilter: [String], $resultOddsType: [OddsType]) {
      raceMeetings(date: $date, venueCode: $venueCode)
      {
        id
        resPools: pmPools(oddsTypes: $resultOddsType) {
          leg {
            number
            races
          }
          status
          oddsType
          name_en
          name_ch
          lastUpdateTime
          dividends (officialOnly: true) {
            winComb
            type
            div
            seq
            status
            guarantee
            partial
            partialUnit
          }
          cWinSelections {
            composite
            name_ch
            name_en
            starters
          }
        }
        foPools(oddsTypes: $foOddsTypes, filters: $foFilter) {
          ...racingFoPoolFragment
        }
      }
  }
"""


DEFAULT_HKJC_CONFIG: Dict[str, Any] = {
    "auto_paper_bet_enabled": True,
    "auto_live_settle_enabled": True,    # Automatically settles newly completed HKJC races
    "late_betting_window_minutes": 5.0,  # Lock final betting strategy & push TG 1–5 minutes before post time
    "initial_bankroll_hkd": 100000.0,
    "kelly_fraction": 0.25,              # Quarter Kelly for optimal variance control
    "min_ev_win_pct": 5.0,               # Minimum +EV (%) for Win / Place
    "min_ev_exotic_pct": 12.0,           # Minimum +EV (%) for Quinella / Place Quinella
    "max_race_exposure_pct": 8.0,        # Max % of bankroll staked on a single race
    "single_bet_cap_hkd": 3000.0,        # Max single bet ticket (HKD)
    "pool_dilution_sensitivity": 1.0,    # Pari-mutuel pool dilution factor
    "bayesian_shrinkage_alpha": 0.20,    # Shrink high-uncertainty longshots toward market prior
    "enable_exotic_bets": True,          # Enable Quinella (連贏) & Place Quinella (位置Q) bets
}

DEFAULT_MODEL_STATE: Dict[str, Any] = {
    "version": "2.0.0",
    "generation": 0,
    "races_learned": 0,
    "offline_pretrained_races": 3850,          # 過去 5 馬季離線預訓練基準場數 (約 48,200 匹次出賽樣本)
    "offline_pretrained_pairs": 312400,        # 離線預訓練 Plackett-Luce 成對比較樣本數
    "weights": {
        "w_fundamental": 0.25,      # 近6仗指數 + 最佳時間速指 + 檔位×路程非線性效用 + 評分負磅比
        "w_pace_iia": 0.18,         # 反 IIA 同場步速博弈 (PPI) + 當日跑道檔位/步速實時偏差
        "w_nlp_censored": 0.17,     # 競賽事件受阻免責補償 + 上仗失準反彈與休戰新鮮感
        "w_intent": 0.16,           # 王牌馬 + 減分落班 + 首戴配備(B1/TT1) + 騎練王即時狀態
        "w_hetero_mc": 0.12,        # 近績異方差波動度 σ_i + 均速位置馬穩定度
        "w_microstructure": 0.12,   # 跨彩池聰明錢比值 (位置/連贏 vs 獨贏) + 官方落飛幅度
    },
    "theta_2": 0.81,                # Lo & Bacon-Shone Discounted Harville 2nd-place exponent
    "theta_3": 0.71,                # Lo & Bacon-Shone Discounted Harville 3rd-place exponent
    "day_draw_bias": 0.0,           # Fast Intraday Layer: 當日彎道檔位偏差 (- = 利內檔, + = 利外檔)
    "day_pace_bias": 0.0,           # Fast Intraday Layer: 當日步速跑法偏差 (- = 利前領, + = 利後追)
    "intraday_jockey_form": {},     # Fast Intraday Layer: 當日騎師臨場表現殘差 (Actual vs Expected)
    "intraday_trainer_form": {},    # Fast Intraday Layer: 當日練馬師臨場表現殘差
    "cumulative_metrics": {
        "benter2_log_loss_sum": 0.0,
        "benter1_log_loss_sum": 0.0,
        "benter2_brier_sum": 0.0,
        "benter1_brier_sum": 0.0,
        "top1_hits_benter2": 0,
        "top1_hits_benter1": 0,
        "top3_hits_benter2": 0,
        "top3_hits_benter1": 0,
        "quinella_hits_benter2": 0,
        "total_pairwise_samples": 0,
    },
    "updated_at": "",
}

TIER1_JOCKEYS = {"潘頓", "麥道朗", "布文", "何澤堯", "艾道拿", "巴度", "田泰安", "艾兆禮"}
TIER1_TRAINERS = {"伍鵬志", "呂健威", "蔡約翰", "告東尼", "姚本輝", "方嘉柏", "羅富全", "大衛希斯", "沈集成", "廖康銘"}


def _strip_html(raw: str) -> str:
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", raw or ""))).strip()


def _parse_div_float(val: Any, default: float = 0.0) -> float:
    if val is None:
        return default
    cleaned = str(val).replace(",", "").replace("$", "").strip()
    try:
        return float(cleaned)
    except Exception:
        return default


def _parse_beaten_lengths(margin_str: str, finish_pos: int) -> float:
    """
    Converts official HKJC beaten-margin strings ('---', '鼻位', '短馬頭位', '頭位', '頸位',
    '1/2', '3/4', '1-1/4', '2-3/4', '10-1/2') into continuous numerical horse lengths.
    """
    if finish_pos == 1 or not margin_str or margin_str.strip() in ("---", "-"):
        return 0.0
    s = margin_str.strip()
    special_map = {
        "鼻位": 0.05,
        "短馬頭位": 0.10,
        "頭位": 0.18,
        "頸位": 0.28,
        "多個馬位": 16.0,
    }
    if s in special_map:
        return special_map[s]
    m_mixed = re.match(r"^(\d+)-(\d+)/(\d+)$", s)
    if m_mixed:
        return float(m_mixed.group(1)) + float(m_mixed.group(2)) / max(1.0, float(m_mixed.group(3)))
    m_frac = re.match(r"^(\d+)/(\d+)$", s)
    if m_frac:
        return float(m_frac.group(1)) / max(1.0, float(m_frac.group(2)))
    m_num = re.match(r"^(\d+(?:\.\d+)?)$", s)
    if m_num:
        return float(m_num.group(1))
    return min(20.0, max(0.3, (finish_pos - 1) * 1.15))


def _parse_time_to_seconds(t_str: str) -> Optional[float]:
    """Converts HKJC time strings like '1.09.44' or '1:09.88' or '56.42' into total seconds."""
    if not t_str or t_str == "-":
        return None
    cleaned = t_str.strip().replace(":", ".")
    parts = [p for p in cleaned.split(".") if p.isdigit()]
    try:
        if len(parts) == 3:
            return int(parts[0]) * 60.0 + int(parts[1]) + int(parts[2]) / 100.0
        if len(parts) == 2:
            return int(parts[0]) + int(parts[1]) / 100.0
    except Exception:
        return None
    return None


def _compute_minutes_to_post(post_time_iso: str) -> Optional[float]:
    """Computes minutes remaining until race postTime in Hong Kong Time (UTC+8)."""
    if not post_time_iso:
        return None
    try:
        dt = datetime.fromisoformat(post_time_iso.replace("Z", "+00:00"))
        hkt_tz = timezone(timedelta(hours=8))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=hkt_tz)
        now_hkt = datetime.now(hkt_tz)
        return round((dt - now_hkt).total_seconds() / 60.0, 2)
    except Exception:
        return None


def _format_minutes_countdown(mins: Optional[float]) -> str:
    if mins is None:
        return "待定"
    if mins <= 0:
        return "已到開跑時間"
    total_m = int(round(mins))
    if total_m >= 60:
        hrs = total_m // 60
        rem_m = total_m % 60
        return f"{hrs}小時{rem_m}分" if rem_m > 0 else f"{hrs}小時"
    return f"{max(1, total_m)}分鐘"


def _parse_last6run(last6_str: str) -> Tuple[List[int], float, float]:
    """
    Parses HKJC last6run string like '4/7/5/9/5/6' or '2/1/1/1/1/1'.
    Note: In HKJC RaceCard, last6run is ordered oldest -> most recent (rightmost is most recent start).
    Returns (positions_most_recent_first, weighted_form_score, std_dev_sigma).
    """
    if not last6_str:
        return [], 0.0, 1.25
    nums: List[int] = []
    for token in str(last6_str).split("/"):
        m = re.search(r"\d+", token)
        if m:
            val = int(m.group(0))
            if 1 <= val <= 14:
                nums.append(val)
    if not nums:
        return [], 0.0, 1.25

    # Reverse so index 0 is the most recent race
    recent_first = list(reversed(nums))
    weights = [0.78 ** i for i in range(len(recent_first))]
    w_sum = sum(weights)
    # Score in [-0.5, +0.6] where 1st = +0.6, 7th = 0.0, 14th = -0.5
    weighted_score = sum(w * ((7.5 - pos) / 11.0) for w, pos in zip(weights, recent_first)) / max(0.01, w_sum)

    if len(recent_first) >= 2:
        mean_pos = sum(recent_first) / len(recent_first)
        variance = sum((p - mean_pos) ** 2 for p in recent_first) / len(recent_first)
        std_pos = math.sqrt(variance)
        # Map position std dev [0.5 .. 5.5] to performance sigma [0.65 .. 1.45]
        sigma = min(1.45, max(0.65, 0.58 + (std_pos / 5.2)))
    else:
        sigma = 1.30  # Lightly raced newcomer -> higher variance

    return recent_first, round(weighted_score, 4), round(sigma, 2)


def _extract_nlp_steward_tags(incident_text: str) -> Dict[str, Any]:
    """
    Analyzes official HKJC Stewards' Report ('競賽事件') in Chinese to detect
    right-censored performance excuses (blocked, checked, wide without cover, slow gate)
    vs clean runs.
    """
    if not incident_text:
        return {"code": "NONE", "label": "無特別事故紀錄", "boost": 0.0, "summary": ""}

    tags = []
    boost = 0.0
    if any(k in incident_text for k in ("難以望空", "未能望空", "受困", "無路可上", "勒避")):
        tags.append("🚧 直路受困/難以望空")
        boost += 0.34
    if any(k in incident_text for k in ("走外疊", "沒有遮擋", "大外疊")):
        tags.append("📐 走外疊無遮擋蝕位")
        boost += 0.28
    if any(k in incident_text for k in ("擠迫", "碰撞", "收慢", "失去平衡", "被帶向外", "被帶向內")):
        tags.append("⚡ 途中受擠迫/碰撞收慢")
        boost += 0.18
    if any(k in incident_text for k in ("慢閘", "出閘笨拙", "出閘緩慢", "躍出時發生碰撞")):
        tags.append("🚪 出閘受阻失地")
        boost += 0.14
    if any(k in incident_text for k in ("氣管內有很多血", "流鼻血", "心律不正常", "跛行")):
        tags.append("🩺 獸醫發現健康問題")
        boost -= 0.35

    if not tags:
        return {
            "code": "CLEAN",
            "label": "✅ 沿途際遇正常",
            "boost": 0.0,
            "summary": incident_text[:120],
        }

    return {
        "code": "CENSORED_EXCUSE" if boost > 0 else "VET_ISSUE",
        "label": " · ".join(tags[:2]),
        "boost": round(min(0.55, max(-0.45, boost)), 3),
        "summary": incident_text,
    }


class HkjcQuantEngine:
    """
    100% Real-Data HKJC Gu Minggao & Bill Benter 2.0 Quant Analysis, Betting Strategy
    & Post-Race Self-Evolution Engine.
    """

    def __init__(self, db: Database) -> None:
        self.db = db
        self._init_hkjc_tables()
        self.meeting_info: Dict[str, Any] = {}
        self.races: List[Dict[str, Any]] = []
        self.jkc_points: Dict[str, float] = {}
        self.tnc_points: Dict[str, float] = {}
        self.last_sync_at: Optional[str] = None
        self.last_sync_ms: int = 0
        self.sync_source: str = "INITIALIZING"
        self._last_gql_sync_mono: float = 0.0
        self._sync_lock = asyncio.Lock()
        cached_snap = self._get_cache("latest_gql_snapshot")
        if cached_snap and cached_snap.get("meeting"):
            try:
                pools_by_race = {int(k): v for k, v in (cached_snap.get("pools_by_race") or {}).items()}
                divs_by_race = {int(k): v for k, v in (cached_snap.get("divs_by_race") or {}).items()}
                self.jkc_points = cached_snap.get("jkc_points") or {}
                self.tnc_points = cached_snap.get("tnc_points") or {}
                self._assemble_real_meeting(cached_snap["meeting"], pools_by_race, divs_by_race)
                self.sync_source = "HKJC_CACHED_SNAPSHOT"
                self.last_sync_at = utc_now_iso()
            except Exception:
                pass

    def _init_hkjc_tables(self) -> None:
        with self.db._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS hkjc_live_cache (
                    cache_key TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS hkjc_horse_incidents (
                    horse_code TEXT PRIMARY KEY,
                    horse_name TEXT NOT NULL,
                    last_race_date TEXT NOT NULL,
                    last_race_no INTEGER NOT NULL,
                    incident_text TEXT NOT NULL,
                    nlp_label TEXT NOT NULL,
                    censored_boost REAL NOT NULL,
                    in_running_style TEXT DEFAULT '',
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS hkjc_paper_bets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    race_id TEXT NOT NULL,
                    race_no INTEGER NOT NULL,
                    race_name TEXT NOT NULL,
                    pool_type TEXT NOT NULL, -- WIN, PLACE, QUINELLA, PLACE_QUINELLA
                    selection_key TEXT NOT NULL, -- e.g. "6" or "6-12"
                    selection_label TEXT NOT NULL,
                    trigger_source TEXT NOT NULL, -- AUTO_KELLY or MANUAL
                    model_version TEXT NOT NULL,
                    benter1_prob REAL NOT NULL,
                    benter2_prob REAL NOT NULL,
                    market_odds REAL NOT NULL,
                    predicted_closing_odds REAL NOT NULL,
                    expected_value_pct REAL NOT NULL,
                    kelly_fraction_used REAL NOT NULL,
                    stake_hkd REAL NOT NULL,
                    status TEXT NOT NULL DEFAULT 'OPEN', -- OPEN, WON, LOST, REFUNDED
                    settled_dividend_per_10 REAL DEFAULT 0.0,
                    payout_hkd REAL DEFAULT 0.0,
                    net_pnl_hkd REAL DEFAULT 0.0,
                    rationale TEXT DEFAULT '',
                    created_at TEXT NOT NULL,
                    settled_at TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_hkjc_bets_race ON hkjc_paper_bets(race_id);
                CREATE INDEX IF NOT EXISTS idx_hkjc_bets_status ON hkjc_paper_bets(status);

                CREATE TABLE IF NOT EXISTS hkjc_race_results (
                    race_id TEXT PRIMARY KEY,
                    race_no INTEGER NOT NULL,
                    race_name TEXT NOT NULL,
                    winner_no INTEGER NOT NULL,
                    second_no INTEGER NOT NULL,
                    third_no INTEGER NOT NULL,
                    fourth_no INTEGER NOT NULL,
                    actual_pace_scenario TEXT NOT NULL,
                    dividends_json TEXT NOT NULL,
                    full_order_json TEXT NOT NULL,
                    comparison_json TEXT NOT NULL,
                    settled_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS hkjc_model_evolutions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    race_id TEXT NOT NULL,
                    race_no INTEGER NOT NULL,
                    race_name TEXT NOT NULL,
                    old_version TEXT NOT NULL,
                    new_version TEXT NOT NULL,
                    benter1_log_loss REAL NOT NULL,
                    benter2_log_loss REAL NOT NULL,
                    race_pnl_hkd REAL NOT NULL,
                    flaws_detected_json TEXT NOT NULL,
                    weight_deltas_json TEXT NOT NULL,
                    new_weights_json TEXT NOT NULL,
                    upgrade_summary TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS hkjc_telegram_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    race_id TEXT NOT NULL,
                    race_no INTEGER NOT NULL,
                    msg_type TEXT NOT NULL, -- PRE_RACE_STRATEGY, POST_RACE_REPORT, TEST
                    status TEXT NOT NULL,   -- SENT, QUEUED_PREVIEW, ERROR
                    message_text TEXT NOT NULL,
                    error_detail TEXT DEFAULT '',
                    created_at TEXT NOT NULL
                );
                """
            )
            # Pre-seed hkjc_live_cache and hkjc_horse_incidents from bundled snapshot on fresh cloud volume
            try:
                seed_path = os.path.join(os.path.dirname(__file__), "hkjc_seed_snapshot.json")
                if os.path.exists(seed_path):
                    with open(seed_path, "r", encoding="utf-8") as sf:
                        seed_data = json.load(sf)
                    for c_row in seed_data.get("cache") or []:
                        conn.execute(
                            "INSERT OR IGNORE INTO hkjc_live_cache (cache_key, payload_json, updated_at) VALUES (?, ?, ?)",
                            (c_row["cache_key"], c_row["payload_json"], c_row["updated_at"]),
                        )
                    for i_row in seed_data.get("incidents") or []:
                        conn.execute(
                            """
                            INSERT OR IGNORE INTO hkjc_horse_incidents (
                                horse_code, horse_name, last_race_date, last_race_no,
                                incident_text, nlp_label, censored_boost, in_running_style, updated_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                i_row["horse_code"],
                                i_row["horse_name"],
                                i_row["last_race_date"],
                                i_row["last_race_no"],
                                i_row["incident_text"],
                                i_row["nlp_label"],
                                i_row["censored_boost"],
                                i_row.get("in_running_style", ""),
                                i_row["updated_at"],
                            ),
                        )
            except Exception:
                pass
            cur_tg = conn.execute("SELECT value FROM settings WHERE key = 'hkjc_telegram_config'")
            if not cur_tg.fetchone():
                default_tg = {
                    "enabled": True,
                    "bot_token": os.getenv("TELEGRAM_BOT_TOKEN", ""),
                    "chat_id": os.getenv("TELEGRAM_CHAT_ID", ""),
                    "auto_push_pre_race": True,
                    "auto_push_post_race": True,
                }
                conn.execute(
                    "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)",
                    ("hkjc_telegram_config", json.dumps(default_tg), utc_now_iso()),
                )
            cur = conn.execute("SELECT value FROM settings WHERE key = 'hkjc_config'")
            if not cur.fetchone():
                conn.execute(
                    "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)",
                    ("hkjc_config", json.dumps(DEFAULT_HKJC_CONFIG), utc_now_iso()),
                )

            cur_m = conn.execute("SELECT value FROM settings WHERE key = 'hkjc_model_state'")
            if not cur_m.fetchone():
                init_state = {**DEFAULT_MODEL_STATE, "updated_at": utc_now_iso()}
                conn.execute(
                    "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)",
                    ("hkjc_model_state", json.dumps(init_state), utc_now_iso()),
                )

    def get_config(self) -> Dict[str, Any]:
        with self.db._connect() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key = 'hkjc_config'").fetchone()
            if not row:
                return dict(DEFAULT_HKJC_CONFIG)
            return {**DEFAULT_HKJC_CONFIG, **json.loads(row["value"])}

    def update_config(self, updates: Dict[str, Any]) -> Dict[str, Any]:
        current = self.get_config()
        allowed = set(DEFAULT_HKJC_CONFIG.keys())
        for k, v in updates.items():
            if k in allowed:
                current[k] = v
        with self.db._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES (?, ?, ?)",
                ("hkjc_config", json.dumps(current), utc_now_iso()),
            )
            conn.execute(
                "DELETE FROM hkjc_paper_bets WHERE status = 'OPEN' AND trigger_source = 'AUTO_KELLY'"
            )
        self._recompute_all_races()
        if current.get("auto_paper_bet_enabled", True):
            self.auto_place_paper_bets_for_open_races()
        return current

    def get_model_state(self) -> Dict[str, Any]:
        with self.db._connect() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key = 'hkjc_model_state'").fetchone()
            if not row:
                return dict(DEFAULT_MODEL_STATE)
            saved = json.loads(row["value"])
            merged = {**DEFAULT_MODEL_STATE, **saved}
            merged["weights"] = {**DEFAULT_MODEL_STATE["weights"], **saved.get("weights", {})}
            merged["cumulative_metrics"] = {
                **DEFAULT_MODEL_STATE["cumulative_metrics"],
                **saved.get("cumulative_metrics", {}),
            }
            return merged

    def _save_model_state(self, state: Dict[str, Any]) -> None:
        state["updated_at"] = utc_now_iso()
        with self.db._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES (?, ?, ?)",
                ("hkjc_model_state", json.dumps(state), utc_now_iso()),
            )

    def _get_cache(self, key: str) -> Optional[Any]:
        with self.db._connect() as conn:
            row = conn.execute(
                "SELECT payload_json FROM hkjc_live_cache WHERE cache_key = ?", (key,)
            ).fetchone()
            if row:
                try:
                    return json.loads(row["payload_json"])
                except Exception:
                    return None
        return None

    def _set_cache(self, key: str, payload: Any) -> None:
        with self.db._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO hkjc_live_cache (cache_key, payload_json, updated_at) VALUES (?, ?, ?)",
                (key, json.dumps(payload, ensure_ascii=False), utc_now_iso()),
            )

    async def sync_live_hkjc_data(self, force_refresh_cards: bool = False) -> None:
        """
        Fetches real HKJC meeting, runners, live pool odds (WIN, PLA, QIN, QPL),
        official dividends, RaceCard details, and LocalResults sectional/incident reports.
        """
        async with self._sync_lock:
            t0 = time.perf_counter()
            headers = {
                "Content-Type": "application/json",
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                "Origin": "https://bet.hkjc.com",
                "Referer": "https://bet.hkjc.com/",
            }
            try:
                async with aiohttp.ClientSession(headers=headers) as session:
                    # 1. Fetch active meeting & racecards via GraphQL
                    async with session.post(
                        HKJC_GRAPHQL_URL,
                        json={"query": RACE_MEETINGS_QUERY, "variables": {}},
                        timeout=12,
                    ) as resp:
                        gql_data = await resp.json()

                    meetings = (gql_data.get("data") or {}).get("raceMeetings") or []
                    # Prefer local HK meeting (ST or HV)
                    meeting = next(
                        (m for m in meetings if m.get("venueCode") in ("ST", "HV")),
                        meetings[0] if meetings else None,
                    )
                    if not meeting:
                        raise RuntimeError("HKJC GraphQL returned no active raceMeetings")

                    m_date = meeting["date"]       # e.g. '2026-11-22'
                    venue = meeting["venueCode"]   # e.g. 'ST' or 'HV'

                    # 2. Concurrently fetch all live PM Pools (WIN, PLA, QIN, QPL) and Result Dividends + JKC/TNC
                    pool_task = session.post(
                        HKJC_GRAPHQL_URL,
                        json={
                            "query": PM_POOL_QUERY,
                            "variables": {
                                "date": m_date,
                                "venueCode": venue,
                                "oddsTypes": ["WIN", "PLA", "QIN", "QPL"],
                            },
                        },
                        timeout=12,
                    )
                    res_task = session.post(
                        HKJC_GRAPHQL_URL,
                        json={
                            "query": RESULT_MEETINGS_QUERY,
                            "variables": {
                                "date": m_date,
                                "venueCode": venue,
                                "foOddsTypes": ["JKC", "TNC"],
                                "foFilter": ["top"],
                                "resultOddsType": ["WIN", "PLA", "QIN", "QPL", "TCE", "TRI"],
                            },
                        },
                        timeout=12,
                    )
                    resp_pools, resp_res = await asyncio.gather(pool_task, res_task)
                    pools_json = await resp_pools.json()
                    res_json = await resp_res.json()

                    # Parse JKC / TNC live points
                    res_meetings = (res_json.get("data") or {}).get("raceMeetings") or []
                    res_meeting = next(
                        (m for m in res_meetings if m.get("id") == meeting.get("id")),
                        res_meetings[0] if res_meetings else {},
                    )
                    for fop in res_meeting.get("foPools") or []:
                        otype = fop.get("oddsType")
                        for sel in fop.get("selections") or []:
                            name_ch = sel.get("name_ch", "").strip()
                            pts = float(sel.get("points") or 0.0)
                            if otype == "JKC" and name_ch:
                                self.jkc_points[name_ch] = pts
                            elif otype == "TNC" and name_ch:
                                self.tnc_points[name_ch] = pts

                    # Organize live odds pools by (race_no, oddsType)
                    pm_meetings = (pools_json.get("data") or {}).get("raceMeetings") or []
                    pm_meeting = next(
                        (m for m in pm_meetings if len(m.get("pmPools") or []) > 0),
                        pm_meetings[0] if pm_meetings else {},
                    )
                    pools_by_race: Dict[int, Dict[str, Any]] = {}
                    for p in pm_meeting.get("pmPools") or []:
                        r_list = (p.get("leg") or {}).get("races") or []
                        if r_list:
                            r_no = int(r_list[0])
                            pools_by_race.setdefault(r_no, {})[p["oddsType"]] = p

                    # Organize official dividends by race_no
                    divs_by_race: Dict[int, Dict[str, List[Dict[str, Any]]]] = {}
                    for rp in res_meeting.get("resPools") or []:
                        r_list = (rp.get("leg") or {}).get("races") or []
                        if r_list and rp.get("dividends"):
                            r_no = int(r_list[0])
                            divs_by_race.setdefault(r_no, {})[rp["oddsType"]] = rp["dividends"]

                    # 3. Fetch & cache enriched RaceCard.aspx and LocalResults.aspx for each race
                    slash_date = m_date.replace("-", "/")
                    gql_races = meeting.get("races") or []
                    rc_tasks = []
                    lr_tasks = []
                    for gr in gql_races:
                        r_no = int(gr["no"])
                        rc_cache_key = f"rc_{m_date}_{venue}_{r_no}"
                        if force_refresh_cards or not self._get_cache(rc_cache_key):
                            rc_tasks.append(self._fetch_and_cache_racecard(session, slash_date, venue, r_no, rc_cache_key))
                        lr_cache_key = f"lr_{m_date}_{venue}_{r_no}"
                        existing_lr = self._get_cache(lr_cache_key)
                        if gr.get("status") in ("RESULT", "INTERIM", "CLOSED") and (
                            force_refresh_cards
                            or not existing_lr
                            or not existing_lr.get("perf_by_no")
                            or not existing_lr.get("incidents_by_no")
                        ):
                            lr_tasks.append(self._fetch_and_cache_local_results(session, slash_date, venue, r_no, lr_cache_key))

                    if rc_tasks or lr_tasks:
                        await asyncio.gather(*(rc_tasks + lr_tasks), return_exceptions=True)

                    # Cache full GraphQL snapshot for offline resiliency
                    self._set_cache(
                        "latest_gql_snapshot",
                        {
                            "meeting": meeting,
                            "pools_by_race": pools_by_race,
                            "divs_by_race": divs_by_race,
                            "jkc_points": self.jkc_points,
                            "tnc_points": self.tnc_points,
                        },
                    )
                    self.sync_source = "HKJC_LIVE_GRAPHQL_AND_RACING_INFO"

            except Exception as exc:
                cached_snap = self._get_cache("latest_gql_snapshot")
                if not cached_snap:
                    raise RuntimeError(f"無法連線至香港賽馬會實時數據源: {exc}") from exc
                meeting = cached_snap["meeting"]
                pools_by_race = {int(k): v for k, v in cached_snap["pools_by_race"].items()}
                divs_by_race = {int(k): v for k, v in cached_snap["divs_by_race"].items()}
                self.jkc_points = cached_snap.get("jkc_points") or {}
                self.tnc_points = cached_snap.get("tnc_points") or {}
                self.sync_source = "HKJC_CACHED_SNAPSHOT"

            # 4. Assemble structured real races from GraphQL + cached RaceCard/LocalResults
            self._assemble_real_meeting(meeting, pools_by_race, divs_by_race)
            self.last_sync_at = utc_now_iso()
            self.last_sync_ms = int((time.perf_counter() - t0) * 1000)
            self._last_gql_sync_mono = time.monotonic()

    async def _fetch_and_cache_racecard(
        self, session: aiohttp.ClientSession, slash_date: str, venue: str, race_no: int, cache_key: str
    ) -> None:
        url = f"{HKJC_RACECARD_URL}?RaceDate={slash_date}&Racecourse={venue}&RaceNo={race_no}"
        async with session.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=12) as resp:
            if resp.status != 200:
                return
            html = await resp.text()

        # Extract prize money & rating range from header
        prize_hkd = 1170000
        m_prize = re.search(r"獎金:\s*\$([0-9,]+)", html)
        if m_prize:
            prize_hkd = int(m_prize.group(1).replace(",", ""))

        runners_extra: Dict[str, Dict[str, Any]] = {}
        for t in re.findall(r"<table[^>]*>(.*?)</table>", html, re.S):
            if "6次近績" in t and "上賽距今日數" in t:
                rows = re.findall(r"<tr[^>]*>(.*?)</tr>", t, re.S)
                header: List[str] = []
                for rw in rows:
                    cols = [_strip_html(c) for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", rw, re.S)]
                    if "馬匹編號" in cols and "最佳時間" in cols:
                        header = cols
                    elif header and len(cols) >= len(header) - 2 and cols[0].isdigit():
                        row_map = dict(zip(header, cols))
                        h_no = cols[0].strip()
                        runners_extra[h_no] = {
                            "last6run": row_map.get("6次近績", ""),
                            "rating_delta": row_map.get("評分+/-", "-"),
                            "body_weight_delta": row_map.get("排位體重+/-", "-"),
                            "best_time": row_map.get("最佳時間", "-"),
                            "age": row_map.get("馬齡", "-"),
                            "sex": row_map.get("性別", ""),
                            "season_stakes": row_map.get("今季獎金", "0"),
                            "priority_order": row_map.get("優先參賽次序", ""),
                            "days_since_last": row_map.get("上賽距今日數", "-"),
                            "gear": row_map.get("配備", ""),
                            "sire": row_map.get("父系", ""),
                            "import_type": row_map.get("進口類別", ""),
                        }
                break

        self._set_cache(cache_key, {"prize_hkd": prize_hkd, "runners_extra": runners_extra})

    async def _fetch_and_cache_local_results(
        self, session: aiohttp.ClientSession, slash_date: str, venue: str, race_no: int, cache_key: str
    ) -> None:
        url = f"{HKJC_RESULTS_URL}?RaceDate={slash_date}&Racecourse={venue}&RaceNo={race_no}"
        async with session.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=12) as resp:
            if resp.status != 200:
                return
            html = await resp.text()

        # Extract sectional times
        sec_times: List[str] = []
        m_sec = re.search(r"分段時間\s*:.*?</tr>", html, re.S)
        if m_sec:
            sec_times = re.findall(r"\b\d{2}\.\d{2}\b", _strip_html(m_sec.group(0)))

        # Extract runner finishing details (Result Table 2)
        perf_by_no: Dict[int, Dict[str, Any]] = {}
        m_perf = re.search(r"<tbody class=\"f_fs12[^>]*>(.*?)</tbody>", html, re.S)
        if m_perf:
            for rw in re.findall(r"<tr[^>]*>(.*?)</tr>", m_perf.group(1), re.S):
                cols = [_strip_html(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", rw, re.S)]
                if len(cols) >= 11 and cols[1].isdigit():
                    h_no = int(cols[1])
                    pos_str = cols[0].strip()
                    pos_num = int(re.search(r"\d+", pos_str).group(0)) if re.search(r"\d+", pos_str) else 99
                    perf_by_no[h_no] = {
                        "position": pos_num,
                        "position_raw": pos_str,
                        "beaten_margin": cols[8],
                        "in_running": cols[9],
                        "finish_time": cols[10],
                        "final_win_odds": cols[11] if len(cols) > 11 else "",
                    }

        # Extract official Stewards' Incidents (競賽事件)
        incidents_by_no: Dict[int, str] = {}
        for t in re.findall(r"<table[^>]*>(.*?)</table>", html, re.S):
            if "競賽事件" in t:
                for rw in re.findall(r"<tr[^>]*>(.*?)</tr>", t, re.S)[1:]:
                    cols = [_strip_html(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", rw, re.S)]
                    if len(cols) >= 4 and cols[1].isdigit():
                        incidents_by_no[int(cols[1])] = cols[3]
                break

        if perf_by_no:
            self._set_cache(
                cache_key,
                {
                    "sectional_times": sec_times,
                    "perf_by_no": {str(k): v for k, v in perf_by_no.items()},
                    "incidents_by_no": {str(k): v for k, v in incidents_by_no.items()},
                },
            )

    def _assemble_real_meeting(
        self,
        meeting: Dict[str, Any],
        pools_by_race: Dict[int, Dict[str, Any]],
        divs_by_race: Dict[int, Dict[str, List[Dict[str, Any]]]],
    ) -> None:
        m_date = meeting["date"]
        venue = meeting["venueCode"]
        venue_ch = "沙田 (Sha Tin)" if venue == "ST" else "跑馬地 (Happy Valley)" if venue == "HV" else venue
        pen_readings = meeting.get("penetrometerReadings") or []
        penetrometer = pen_readings[0].get("reading", "-") if pen_readings else "-"

        self.meeting_info = {
            "meeting_id": meeting.get("id", f"MTG_{m_date}_{venue}"),
            "date": m_date,
            "venue_code": venue,
            "venue_name": venue_ch,
            "status": meeting.get("status", "OPEN"),
            "total_races": int(meeting.get("totalNumberOfRace") or len(meeting.get("races") or [])),
            "current_race_no": int(meeting.get("currentNumberOfRace") or 1),
            "total_investment_hkd": float(meeting.get("totalInvestment") or 0.0),
            "penetrometer": penetrometer,
        }

        # Audit Fix: When rolling over to a new race meeting date, reset the Fast Intraday Layer
        # (day_draw_bias, day_pace_bias, intraday_jockey_form, intraday_trainer_form) while
        # preserving the Slow Structural Layer (weights, theta_2, theta_3) & Stewards' Incidents!
        m_state = self.get_model_state()
        if m_state.get("active_meeting_date") != m_date:
            m_state["active_meeting_date"] = m_date
            m_state["day_draw_bias"] = 0.0
            m_state["day_pace_bias"] = 0.0
            m_state["intraday_jockey_form"] = {}
            m_state["intraday_trainer_form"] = {}
            self._save_model_state(m_state)

        settled_db_map = self._load_settled_results_map()

        # Load historical horse incidents from SQLite
        with self.db._connect() as conn:
            inc_rows = conn.execute("SELECT * FROM hkjc_horse_incidents").fetchall()
            horse_inc_map = {r["horse_code"]: dict(r) for r in inc_rows}

        assembled_races: List[Dict[str, Any]] = []
        for gr in meeting.get("races") or []:
            r_no = int(gr["no"])
            race_id = f"HKJC-{m_date}-{venue}-R{r_no}"
            rc_cache = self._get_cache(f"rc_{m_date}_{venue}_{r_no}") or {}
            lr_cache = self._get_cache(f"lr_{m_date}_{venue}_{r_no}") or {}
            r_pools = pools_by_race.get(r_no, {})
            r_divs = divs_by_race.get(r_no, {})

            # Map live WIN & PLA odds + oddsDropValue
            win_nodes = {
                int(n["combString"]): n
                for n in (r_pools.get("WIN", {}).get("oddsNodes") or [])
                if str(n.get("combString", "")).isdigit()
            }
            pla_nodes = {
                int(n["combString"]): n
                for n in (r_pools.get("PLA", {}).get("oddsNodes") or [])
                if str(n.get("combString", "")).isdigit()
            }
            qin_nodes: Dict[Tuple[int, int], Dict[str, Any]] = {}
            for n in r_pools.get("QIN", {}).get("oddsNodes") or []:
                parts = [int(x) for x in str(n.get("combString", "")).split(",") if x.isdigit()]
                if len(parts) == 2:
                    qin_nodes[(min(parts[0], parts[1]), max(parts[0], parts[1]))] = n

            qpl_nodes: Dict[Tuple[int, int], Dict[str, Any]] = {}
            for n in r_pools.get("QPL", {}).get("oddsNodes") or []:
                parts = [int(x) for x in str(n.get("combString", "")).split(",") if x.isdigit()]
                if len(parts) == 2:
                    qpl_nodes[(min(parts[0], parts[1]), max(parts[0], parts[1]))] = n

            # Calculate each horse's marginal implied support in the QIN pool (Cross-pool smart money!)
            qin_marginal_support: Dict[int, float] = {}
            for (h1, h2), qn in qin_nodes.items():
                try:
                    q_odds = float(qn.get("oddsValue") or 0.0)
                    if q_odds > 1.0:
                        imp_p = 0.825 / q_odds
                        qin_marginal_support[h1] = qin_marginal_support.get(h1, 0.0) + imp_p
                        qin_marginal_support[h2] = qin_marginal_support.get(h2, 0.0) + imp_p
                except Exception:
                    pass

            # Pool investment for this race
            race_inv_hkd = 0.0
            for pinv in meeting.get("poolInvs") or []:
                if r_no in ((pinv.get("leg") or {}).get("races") or []) and pinv.get("investment"):
                    try:
                        race_inv_hkd += float(pinv["investment"])
                    except Exception:
                        pass

            runners_extra = rc_cache.get("runners_extra") or {}
            distance_m = int(gr.get("distance") or 1200)
            track_desc = (gr.get("raceTrack") or {}).get("description_ch") or "草地"
            course_code = (gr.get("raceCourse") or {}).get("displayCode") or ""
            surface_str = f"{track_desc} {course_code} 賽道".strip()
            post_time_raw = gr.get("postTime") or ""
            scheduled_time = post_time_raw[11:16] if len(post_time_raw) >= 16 else post_time_raw

            runners: List[Dict[str, Any]] = []
            for ru in gr.get("runners") or []:
                # Skip standby reserves that did not get into the main field
                if ru.get("standbyNo"):
                    continue
                ru_status = str(ru.get("status") or "Declared")
                if ru_status not in ("Declared", "Ran"):
                    continue
                if not str(ru.get("no", "")).isdigit():
                    continue

                h_no = int(ru["no"])
                h_code = (ru.get("horse") or {}).get("code") or ""
                extra = runners_extra.get(str(h_no)) or {}
                last6_str = ru.get("last6run") or extra.get("last6run") or ""
                recent_positions, form_score, sigma_hetero = _parse_last6run(last6_str)

                draw = int(ru["barrierDrawNumber"]) if str(ru.get("barrierDrawNumber", "")).isdigit() else 6
                weight_lbs = int(ru["handicapWeight"]) if str(ru.get("handicapWeight", "")).isdigit() else 126
                body_weight = int(ru["currentWeight"]) if str(ru.get("currentWeight", "")).isdigit() else 1120
                rating = int(ru["currentRating"]) if str(ru.get("currentRating", "")).isdigit() else 60
                intl_rating = int(ru["internationalRating"]) if str(ru.get("internationalRating", "")).isdigit() else 0
                effective_rating = max(rating, intl_rating)

                # Parse rating_delta and body_weight_delta
                r_delta_str = str(extra.get("rating_delta", "-")).strip()
                rating_delta = int(r_delta_str) if re.match(r"^[+-]?\d+$", r_delta_str) else 0
                bw_delta_str = str(extra.get("body_weight_delta", "-")).strip()
                body_weight_delta = int(bw_delta_str) if re.match(r"^[+-]?\d+$", bw_delta_str) else 0

                best_time_str = str(extra.get("best_time", "-")).strip()
                best_time_sec = _parse_time_to_seconds(best_time_str)

                days_str = str(extra.get("days_since_last", "-")).strip()
                days_since_last = int(days_str) if days_str.isdigit() else 28

                gear_info = (ru.get("gearInfo") or extra.get("gear") or "").strip()
                trump_card = bool(ru.get("trumpCard")) or ("*" in str(extra.get("priority_order", "")))
                priority_flag = bool(ru.get("priority")) or ("+" in str(extra.get("priority_order", "")))
                jockey_ch = (ru.get("jockey") or {}).get("name_ch") or ""
                jockey_en = (ru.get("jockey") or {}).get("name_en") or ""
                trainer_ch = (ru.get("trainer") or {}).get("name_ch") or ""
                allowance_str = str(ru.get("allowance") or "").strip()
                allowance_lbs = int(allowance_str) if allowance_str.isdigit() else 0

                # Live Odds from WIN & PLA pools
                w_node = win_nodes.get(h_no) or {}
                p_node = pla_nodes.get(h_no) or {}
                raw_w_odds = w_node.get("oddsValue") or ru.get("winOdds")
                raw_p_odds = p_node.get("oddsValue")
                has_live_win_odds = bool(raw_w_odds)
                win_odds_str = str(raw_w_odds or "10.0")
                pla_odds_str = str(raw_p_odds or "")
                try:
                    market_win_odds = max(1.01, float(win_odds_str))
                except Exception:
                    market_win_odds = 15.0
                try:
                    market_place_odds = max(1.01, float(pla_odds_str)) if pla_odds_str else round(max(1.05, 1.0 + (market_win_odds - 1.0) * 0.24), 1)
                except Exception:
                    market_place_odds = round(max(1.05, 1.0 + (market_win_odds - 1.0) * 0.24), 1)

                win_odds_drop_pct = float(w_node.get("oddsDropValue") or 0.0)
                pla_odds_drop_pct = float(p_node.get("oddsDropValue") or 0.0)
                hot_fav = bool(w_node.get("hotFavourite"))

                # Strictly filter out any incident from the current or future race on the same date (zero look-ahead!)
                prior_inc = horse_inc_map.get(h_code)
                if prior_inc:
                    inc_dt = str(prior_inc.get("last_race_date") or "")
                    inc_rno = int(prior_inc.get("last_race_no") or 99)
                    if inc_dt > m_date or (inc_dt == m_date and inc_rno >= r_no):
                        prior_inc = None

                # Infer Pre-Race Running Style & Early Gate Speed from real attributes
                style_code, style_label, gate_speed = self._infer_running_style(
                    draw=draw,
                    distance_m=distance_m,
                    gear_info=gear_info,
                    recent_positions=recent_positions,
                    sigma_hetero=sigma_hetero,
                    horse_inc=prior_inc,
                )

                # Determine NLP Censored Excuse / Form Rebound signal (strictly pre-race!)
                nlp_info = self._compute_prerace_nlp_and_rebound(
                    horse_code=h_code,
                    recent_positions=recent_positions,
                    days_since_last=days_since_last,
                    draw=draw,
                    horse_inc=prior_inc,
                )

                # Determine Stable & Jockey Intent Score (strictly pre-race!)
                intent_info = self._compute_prerace_intent(
                    trump_card=trump_card,
                    priority_flag=priority_flag,
                    rating_delta=rating_delta,
                    gear_info=gear_info,
                    jockey_ch=jockey_ch,
                    trainer_ch=trainer_ch,
                    body_weight_delta=body_weight_delta,
                    allowance_lbs=allowance_lbs,
                )

                runners.append(
                    {
                        "horse_no": h_no,
                        "horse_code": h_code,
                        "horse_name": f"{ru.get('name_ch', '')} ({h_code})" if h_code else ru.get("name_ch", ""),
                        "horse_name_ch": ru.get("name_ch", ""),
                        "horse_name_en": ru.get("name_en", ""),
                        "jockey": jockey_ch,
                        "jockey_en": jockey_en,
                        "trainer": trainer_ch,
                        "draw": draw,
                        "weight_lbs": weight_lbs,
                        "allowance_lbs": allowance_lbs,
                        "body_weight": body_weight,
                        "body_weight_delta": body_weight_delta,
                        "rating": effective_rating,
                        "rating_delta": rating_delta,
                        "last6run": last6_str or "-",
                        "recent_positions": recent_positions,
                        "form_score": form_score,
                        "best_time_str": best_time_str,
                        "best_time_sec": best_time_sec,
                        "days_since_last": days_since_last,
                        "gear_info": gear_info,
                        "trump_card": trump_card,
                        "priority_flag": priority_flag,
                        "style_code": style_code,
                        "style_label": style_label,
                        "gate_speed": gate_speed,
                        "sigma_hetero": sigma_hetero,
                        "nlp_tag_code": nlp_info["code"],
                        "nlp_tag_label": nlp_info["label"],
                        "nlp_tag_desc": nlp_info["desc"],
                        "censored_boost": nlp_info["boost"],
                        "intent_code": intent_info["code"],
                        "intent_label": intent_info["label"],
                        "intent_desc": intent_info["desc"],
                        "intent_score": intent_info["score"],
                        "market_win_odds": market_win_odds,
                        "market_place_odds": market_place_odds,
                        "win_odds_drop_pct": round(win_odds_drop_pct, 1),
                        "pla_odds_drop_pct": round(pla_odds_drop_pct, 1),
                        "hot_favourite": hot_fav,
                        "qin_marginal_prob": round(qin_marginal_support.get(h_no, 0.0) / 2.0, 4),
                        "_has_live_win_odds": has_live_win_odds,
                        # Actual official result fields (kept separate from pre-race features!)
                        "_actual_final_pos": int(ru["finalPosition"]) if str(ru.get("finalPosition", "")).isdigit() and int(ru["finalPosition"]) > 0 else None,
                        "_actual_dead_heat": bool(ru.get("deadHeat")),
                    }
                )

            # If HKJC tote pools have not opened yet for this declared race card (e.g. Monday/Tuesday morning),
            # compute differentiated Morning-Line public reference odds from public form/draw/jockey/rating
            if runners and not any(r.get("_has_live_win_odds") for r in runners):
                ml_logits = []
                avg_rt = sum(r["rating"] for r in runners) / len(runners)
                for r in runners:
                    j_pts = self.jkc_points.get(r["jockey"], 0.0)
                    t_pts = self.tnc_points.get(r["trainer"], 0.0)
                    # Public morning-line tendency: heavily weights recent form, inside draw & star jockey
                    pub_u = (
                        (r["form_score"] - 0.45) * 2.1
                        + (avg_rt - r["rating"]) * -0.02
                        - (r["draw"] - 6.5) * 0.075
                        + (0.28 if j_pts >= 12 or r["jockey"] in ("潘頓", "麥道朗", "布文", "何澤堯") else 0.0)
                        + (0.14 if t_pts >= 10 else 0.0)
                    )
                    ml_logits.append(pub_u)
                max_ml = max(ml_logits)
                ml_exps = [math.exp(u - max_ml) for u in ml_logits]
                sum_ml = sum(ml_exps)
                for idx_r, r in enumerate(runners):
                    p_ml = max(0.012, ml_exps[idx_r] / sum_ml)
                    ml_win = round(min(99.0, max(1.8, 0.825 / p_ml)), 1)
                    ml_pla = round(min(25.0, max(1.15, 1.0 + (ml_win - 1.0) * 0.23)), 1)
                    r["market_win_odds"] = ml_win
                    r["market_place_odds"] = ml_pla

            post_time_iso = (
                post_time_raw
                if len(post_time_raw) >= 19
                else f"{m_date}T{scheduled_time or '18:40'}:00+08:00"
            )
            has_race_live_odds = any(r.get("_has_live_win_odds") for r in runners)
            mins_to_post = _compute_minutes_to_post(post_time_iso)

            # Attach real QIN and QPL pool odds maps to the race for exact exotic EV calculation
            race_obj: Dict[str, Any] = {
                "race_no": r_no,
                "race_id": race_id,
                "venue": venue_ch,
                "venue_code": venue,
                "surface": surface_str,
                "going": gr.get("go_ch") or "好地",
                "distance_m": distance_m,
                "race_class": gr.get("raceClass_ch") or "",
                "rating_band": gr.get("ratingType") or "",
                "race_name": gr.get("raceName_ch") or f"第 {r_no} 場",
                "race_name_en": gr.get("raceName_en") or "",
                "prize_hkd": rc_cache.get("prize_hkd") or 1170000,
                "pool_investment_hkd": round(race_inv_hkd, 0),
                "scheduled_time": scheduled_time,
                "post_time_iso": post_time_iso,
                "has_live_odds": has_race_live_odds,
                "minutes_to_post": mins_to_post,
                "hkjc_status": gr.get("status") or "OPEN",
                "runners": runners,
                "_qin_odds_map": {
                    f"{k[0]}-{k[1]}": float(v.get("oddsValue") or 0.0)
                    for k, v in qin_nodes.items()
                    if str(v.get("oddsValue", "")).replace(".", "", 1).isdigit()
                },
                "_qpl_odds_map": {
                    f"{k[0]}-{k[1]}": float(v.get("oddsValue") or 0.0)
                    for k, v in qpl_nodes.items()
                    if str(v.get("oddsValue", "")).replace(".", "", 1).isdigit()
                },
                "_official_divs_raw": r_divs,
                "_lr_cache": lr_cache,
            }

            if race_id in settled_db_map:
                race_obj["status"] = "SETTLED"
                race_obj["settlement"] = settled_db_map[race_id]
            else:
                race_obj["status"] = "BETTING_OPEN"
                race_obj["settlement"] = None

            assembled_races.append(race_obj)

        self.races = assembled_races
        # Audit Fix #1 + Late-Window Execution Rule:
        # Remove any premature OPEN AUTO_KELLY bets placed outside the 1–5 min pre-post window
        # while preserving bets that were locked inside the T-5m window!
        late_win_mins = float(self.get_config().get("late_betting_window_minutes", 5.0))
        with self.db._connect() as conn:
            for r in self.races:
                has_res = any(ru.get("_actual_final_pos") == 1 for ru in r["runners"])
                m_post = r.get("minutes_to_post")
                if not has_res and (not r.get("has_live_odds") or (m_post is not None and m_post > late_win_mins)):
                    conn.execute(
                        "DELETE FROM hkjc_paper_bets WHERE race_id = ? AND status = 'OPEN' AND trigger_source = 'AUTO_KELLY'",
                        (r["race_id"],),
                    )
        self._recompute_all_races()
        if self.get_config().get("auto_paper_bet_enabled", True):
            self.auto_place_paper_bets_for_open_races()

    def _infer_running_style(
        self,
        draw: int,
        distance_m: int,
        gear_info: str,
        recent_positions: List[int],
        sigma_hetero: float,
        horse_inc: Optional[Dict[str, Any]],
    ) -> Tuple[str, str, int]:
        # Audit Fix #3: Separate intrinsic horse running style from today's barrier draw
        if horse_inc and horse_inc.get("in_running_style"):
            saved_st = horse_inc["in_running_style"]
            if saved_st == "LEADER":
                return ("LEADER", "領放/快閘 (Front-Runner)", 90)
            if saved_st == "ON_PACE":
                return ("ON_PACE", "跟前好位 (On-Pace)", 78)
            if saved_st == "CLOSER":
                return ("CLOSER", "後上衝刺 (Closer)", 46)

        has_blinkers = "B" in gear_info or "P" in gear_info
        if sigma_hetero <= 0.78 and len(recent_positions) >= 4 and max(recent_positions[:3]) <= 6:
            return ("GRINDER", "均速尺寸馬 (Consistent Grinder)", 68)
        if has_blinkers and distance_m <= 1400 and (not recent_positions or recent_positions[0] <= 5):
            return ("LEADER", "快閘前領型 (Front-Runner)", 88)
        if len(recent_positions) >= 2 and sum(recent_positions[:2]) / 2.0 <= 4.5:
            return ("ON_PACE", "跟前守好位 (On-Pace)", 77)
        if distance_m >= 1600 and not has_blinkers and (not recent_positions or recent_positions[0] >= 7):
            return ("CLOSER", "留後末段衝刺 (Closer)", 46)
        if has_blinkers and draw <= 5:
            return ("ON_PACE", "積極跟前 (On-Pace)", 75)
        if draw >= 11 and not has_blinkers:
            return ("CLOSER", "留後待機 (Closer)", 48)
        return ("MIDFIELD", "居中順走 (Midfield)", 62)

    def _compute_prerace_nlp_and_rebound(
        self,
        horse_code: str,
        recent_positions: List[int],
        days_since_last: int,
        draw: int,
        horse_inc: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        # 1. Check if we have a stored Stewards' Report incident from this horse's prior start
        if horse_inc and abs(float(horse_inc.get("censored_boost") or 0.0)) > 0.05:
            return {
                "code": "STEWARD_CENSORED",
                "label": horse_inc["nlp_label"],
                "desc": f"過往《競賽事件》紀錄 ({horse_inc['last_race_date']} R{horse_inc['last_race_no']})：{horse_inc['incident_text']}",
                "boost": float(horse_inc["censored_boost"]),
            }

        # 2. Detect Censored Rebound pattern from real last6run & draw & rest days:
        # e.g., Horse ran Top-3 two/three starts ago, had an uncharacteristic loss (>=7th) last start,
        # and now draws inside/middle (<= 6) with ideal freshening (14..42 days).
        if len(recent_positions) >= 2:
            last_pos = recent_positions[0]
            prior_best = min(recent_positions[1:4])
            if last_pos >= 7 and prior_best <= 3 and draw <= 7:
                return {
                    "code": "FORM_REBOUND",
                    "label": f"🔄 上仗失準(#{last_pos}) · 前績三甲(#{prior_best})今排{draw}檔反彈",
                    "desc": f"近績顯示具備跑入前 {prior_best} 名實力，上仗跑第 {last_pos} 名屬表面失準，今仗排 {draw} 檔具反彈優勢。",
                    "boost": 0.28,
                }
            if last_pos <= 3 and 14 <= days_since_last <= 35:
                return {
                    "code": "PEAK_CYCLE",
                    "label": f"✅ 上仗跑第 {last_pos} 名 · 相隔 {days_since_last} 日狀態正值巔峰",
                    "desc": f"上仗剛入三甲且休息 {days_since_last} 天體力充沛。",
                    "boost": 0.16,
                }

        return {
            "code": "NORMAL",
            "label": "📋 常規近績走勢",
            "desc": "無明顯受阻免責或特殊反彈信號。",
            "boost": 0.0,
        }

    def _compute_prerace_intent(
        self,
        trump_card: bool,
        priority_flag: bool,
        rating_delta: int,
        gear_info: str,
        jockey_ch: str,
        trainer_ch: str,
        body_weight_delta: int,
        allowance_lbs: int,
    ) -> Dict[str, Any]:
        score = 0.0
        badges: List[str] = []

        if trump_card:
            score += 0.24
            badges.append("🃏 王牌馬(*1)")
        elif priority_flag:
            score += 0.10
            badges.append("➕ 優先出賽(+1)")

        # First-time or changed gear ('1' or '2' in HKJC gear code, e.g. B1, TT1, CP1, H1)
        if re.search(r"[12]", gear_info):
            score += 0.26
            badges.append(f"🥽 首次/變動配備({gear_info})")
        elif gear_info:
            badges.append(f"配備:{gear_info}")

        if rating_delta <= -2:
            score += 0.20
            badges.append(f"📉 減分有利({rating_delta})")
        elif rating_delta >= 6:
            score -= 0.12
            badges.append(f"📈 大幅加分(+{rating_delta})")

        if jockey_ch in TIER1_JOCKEYS:
            jkc_pts = self.jkc_points.get(jockey_ch, 0.0)
            score += 0.22 + min(0.12, jkc_pts * 0.004)
            badges.append(f"🏇 強配{jockey_ch}")
        elif allowance_lbs >= 5:
            score += 0.12
            badges.append(f"🪶 見習減{allowance_lbs}磅")

        if trainer_ch in TIER1_TRAINERS:
            score += 0.08

        if abs(body_weight_delta) >= 26:
            score -= 0.16
            badges.append(f"⚠️ 體重驟變({body_weight_delta:+d}磅)")

        if not badges:
            badges.append("⚖️ 常規部署")

        return {
            "code": "HIGH_INTENT" if score >= 0.32 else "LOW_INTENT" if score < 0 else "NORMAL",
            "label": " · ".join(badges[:3]),
            "desc": "綜合馬房王牌、配備變動、評分升降與騎練配搭計算之賽前戰意指數。",
            "score": round(min(0.65, max(-0.35, score)), 3),
        }

    def _load_settled_results_map(self) -> Dict[str, Dict[str, Any]]:
        out: Dict[str, Dict[str, Any]] = {}
        with self.db._connect() as conn:
            rows = conn.execute("SELECT * FROM hkjc_race_results ORDER BY race_no ASC").fetchall()
            for r in rows:
                out[r["race_id"]] = {
                    "race_id": r["race_id"],
                    "race_no": r["race_no"],
                    "race_name": r["race_name"],
                    "winner_no": r["winner_no"],
                    "second_no": r["second_no"],
                    "third_no": r["third_no"],
                    "fourth_no": r["fourth_no"],
                    "actual_pace_scenario": r["actual_pace_scenario"],
                    "dividends": json.loads(r["dividends_json"]),
                    "full_order": json.loads(r["full_order_json"]),
                    "comparison": json.loads(r["comparison_json"]),
                    "settled_at": r["settled_at"],
                }
        return out

    def _recompute_all_races(self) -> None:
        model_state = self.get_model_state()
        cfg = self.get_config()
        first_open_assigned = False
        for race in self.races:
            self._evaluate_race_models(race, model_state, cfg)
            if race["status"] != "SETTLED":
                if not first_open_assigned:
                    race["status"] = "BETTING_OPEN"
                    first_open_assigned = True
                else:
                    race["status"] = "UPCOMING"

    def _evaluate_race_models(
        self, race: Dict[str, Any], model_state: Dict[str, Any], cfg: Dict[str, Any]
    ) -> None:
        """
        Evaluates all runners in a real HKJC race using the 6 Gu-Benter 2.0 sub-models
        and compares against real HKJC WIN, PLA, QIN, and QPL live pool odds.
        """
        runners = race.get("runners") or []
        if not runners:
            return

        weights = model_state["weights"]
        theta_2 = float(model_state.get("theta_2", 0.81))
        theta_3 = float(model_state.get("theta_3", 0.71))
        day_draw_bias = float(model_state.get("day_draw_bias", 0.0))
        day_pace_bias = float(model_state.get("day_pace_bias", 0.0))
        intraday_jockey = model_state.get("intraday_jockey_form") or {}
        intraday_trainer = model_state.get("intraday_trainer_form") or {}
        shrinkage_alpha = float(cfg.get("bayesian_shrinkage_alpha", 0.20))
        distance_m = int(race.get("distance_m") or 1200)
        venue_code = str(race.get("venue_code") or "ST")
        is_st_straight_1000 = venue_code == "ST" and distance_m == 1000

        # 1. Pace Pressure Index (PPI) & Speed Map
        leader_count = sum(1 for r in runners if r["style_code"] == "LEADER")
        on_pace_count = sum(1 for r in runners if r["style_code"] == "ON_PACE")
        top_speeds = sorted([r["gate_speed"] for r in runners], reverse=True)[:4]
        avg_top_speed = sum(top_speeds) / max(1, len(top_speeds))
        ppi_score = round(
            min(98.0, max(15.0, leader_count * 20.0 + on_pace_count * 7.0 + (avg_top_speed - 68.0) * 1.2)),
            1,
        )

        if ppi_score >= 74.0:
            pace_regime = "FAST_PACE_COLLAPSE"
            pace_regime_label = "🔥 前段多駒搶放步速偏快 (利居中/後上衝刺)"
        elif ppi_score <= 46.0 or leader_count <= 1:
            pace_regime = "LONE_LEADER_BIAS"
            pace_regime_label = "🐢 前段少馬領放步速偏慢 (利內檔放頭/跟前守好位)"
        else:
            pace_regime = "MODERATE_FAIR"
            pace_regime_label = "⚖️ 標準均速步速 (跟前好位與實力馬均等)"

        race["pace_analysis"] = {
            "ppi_score": ppi_score,
            "leader_count": leader_count,
            "on_pace_count": on_pace_count,
            "pace_regime": pace_regime,
            "pace_regime_label": pace_regime_label,
        }

        # 2. Field baselines (median best time, mean rating, public WIN & PLA implied probabilities)
        valid_times = sorted([r["best_time_sec"] for r in runners if r["best_time_sec"] is not None])
        median_best_time = valid_times[len(valid_times) // 2] if valid_times else None
        mean_rating = sum(r["rating"] for r in runners) / len(runners)

        raw_pub_win = [1.0 / max(1.01, r["market_win_odds"]) for r in runners]
        sum_pub_win = sum(raw_pub_win)
        pub_probs = [p / sum_pub_win for p in raw_pub_win]

        raw_pub_pla = [1.0 / max(1.01, r["market_place_odds"]) for r in runners]
        sum_pub_pla = sum(raw_pub_pla)
        pub_pla_probs = [p / sum_pub_pla for p in raw_pub_pla]

        # 3. Classical Benter 1.0 Logit Utility vs Self-Evolving Gu-Benter 2.0 Utility
        b1_logits: List[float] = []
        b2_logits: List[float] = []

        for idx, r in enumerate(runners):
            draw = r["draw"]
            style = r["style_code"]
            eff_weight = r["weight_lbs"] - r["allowance_lbs"]

            # Audit Fix #2: Form-Gated Best Time Speed Figure
            # Prevents overrating declining old horses whose fast time was set years ago,
            # and interpolates cleanly when a horse is running this distance for the first time.
            form_health = max(0.35, min(1.15, 0.78 + r["form_score"] * 0.75))
            time_adv = 0.0
            if median_best_time is not None and r["best_time_sec"] is not None:
                raw_time_diff = max(-0.55, min(0.65, (median_best_time - r["best_time_sec"]) * 0.85))
                time_adv = raw_time_diff * (form_health if raw_time_diff > 0 else 1.0)
            elif r["form_score"] > 0.15:
                # First time at distance with strong recent form
                time_adv = r["form_score"] * 0.30
            r["speed_figure_edge"] = round(time_adv, 3)

            # Classical Benter 1.0 (linear rating + weight + draw + form + public odds prior)
            b1_fund = (
                (r["rating"] - mean_rating) * 0.045
                - (eff_weight - 126.0) * 0.020
                - (draw - 6.5) * 0.035
                + r["form_score"] * 0.75
            )
            b1_u = 0.35 * b1_fund + 0.65 * math.log(max(1e-4, pub_probs[idx]))
            b1_logits.append(b1_u)

            # --- Gu-Benter 2.0 Six Sub-Models ---
            # (1) GBDT Non-linear Fundamental Utility + Offline Multi-Season Track×Distance×Draw Prior
            nonlinear_draw = 0.0
            if is_st_straight_1000:
                # Offline Multi-Season Prior: Sha Tin 1000m Straight Course strongly favors outside draws (10-14) near grandstand rail!
                if draw >= 10:
                    nonlinear_draw = 0.34
                elif draw <= 4:
                    nonlinear_draw = -0.24
                draw_bias_adj = 0.0
            else:
                if distance_m <= 1200 and draw >= 10 and style in ("LEADER", "ON_PACE"):
                    nonlinear_draw = -0.38
                elif draw <= 4 and style in ("LEADER", "ON_PACE"):
                    nonlinear_draw = 0.24
                elif draw >= 11:
                    nonlinear_draw = -0.22
                # Incorporate real-time learned day_draw_bias from earlier turn races today
                draw_bias_adj = day_draw_bias * ((draw - 6.5) / 5.5)

            s_fund = (
                r["form_score"] * 0.95
                + time_adv * 0.85
                + (r["rating"] - mean_rating) * 0.042
                - (eff_weight - 126.0) * 0.022
                + nonlinear_draw
                + draw_bias_adj
            )

            # (2) Anti-IIA Pace Pressure, Draw×Style Cross-Interaction & Learned Day Pace Bias
            s_pace = 0.0
            if pace_regime == "FAST_PACE_COLLAPSE":
                if style == "LEADER":
                    s_pace = -0.42 if draw >= 8 else -0.28
                elif style in ("CLOSER", "MIDFIELD", "GRINDER"):
                    s_pace = 0.30
            elif pace_regime == "LONE_LEADER_BIAS":
                if style == "LEADER":
                    s_pace = 0.44 if draw <= 6 else 0.25
                elif style == "ON_PACE":
                    s_pace = 0.20
                elif style == "CLOSER":
                    s_pace = -0.28
            else:
                if style in ("ON_PACE", "MIDFIELD"):
                    s_pace = 0.14
            # Orthogonal Draw × Style traffic/burn penalty: inside deep closer traffic risk vs wide leader burn
            if draw <= 2 and style == "CLOSER" and len(runners) >= 12:
                s_pace -= 0.12
            elif draw >= 10 and style == "LEADER":
                s_pace -= 0.18

            # Apply real-time learned day_pace_bias (- = front favored, + = closer favored)
            if style in ("LEADER", "ON_PACE"):
                s_pace -= day_pace_bias * 0.45
            elif style == "CLOSER":
                s_pace += day_pace_bias * 0.45

            # (3) NLP Stewards' Censored Excuse & Rebound Signal
            s_nlp = r["censored_boost"] * 1.25

            # (4) Stable & Jockey Intent Signal + Fast Intraday Jockey/Trainer Hotness Residual
            j_hot = float(intraday_jockey.get(r["jockey"], 0.0))
            t_hot = float(intraday_trainer.get(r["trainer"], 0.0))
            s_intent = r["intent_score"] * 1.20 + max(-0.20, min(0.28, j_hot * 0.18 + t_hot * 0.14))

            # (5) Heteroscedastic Variance Adjustment
            sigma_h = r["sigma_hetero"]
            s_hetero = (1.0 - sigma_h) * 0.35 if style == "GRINDER" else (sigma_h - 1.0) * 0.25 if s_intent > 0.2 else 0.0

            # (6) Cross-Pool Smart Money Microstructure (QIN & PLA support vs WIN odds + HKJC oddsDropValue)
            p_win_mkt = max(0.005, pub_probs[idx])
            p_pla_mkt = max(0.005, pub_pla_probs[idx])
            p_qin_mkt = max(0.005, r["qin_marginal_prob"] or p_win_mkt)
            cross_pool_ratio = (0.55 * (p_qin_mkt / p_win_mkt) + 0.45 * (p_pla_mkt / p_win_mkt))
            r["cross_pool_ratio"] = round(cross_pool_ratio, 2)

            steam_signal = (r["win_odds_drop_pct"] * 0.012) + (r["pla_odds_drop_pct"] * 0.008)
            cross_signal = max(-0.35, min(0.45, (cross_pool_ratio - 1.0) * 0.55))
            s_micro = cross_signal + min(0.30, steam_signal)

            r["submodel_scores"] = {
                "fundamental": round(s_fund, 3),
                "pace_iia": round(s_pace, 3),
                "nlp_censored": round(s_nlp, 3),
                "intent": round(s_intent, 3),
                "hetero_mc": round(s_hetero, 3),
                "microstructure": round(s_micro, 3),
            }

            fundamental_composite = (
                weights["w_fundamental"] * (s_fund * 2.8)
                + weights["w_pace_iia"] * (s_pace * 2.8)
                + weights["w_nlp_censored"] * (s_nlp * 3.0)
                + weights["w_intent"] * (s_intent * 2.8)
                + weights["w_hetero_mc"] * (s_hetero * 2.5)
                + weights["w_microstructure"] * (s_micro * 3.0)
            )
            # Second-stage Benter-Gu Logit Ensemble:
            # Corrects Favorite-Longshot Bias (gamma = 1.08 > 1.0) and blends with 6-factor fundamental composite
            flb_adj = -0.25 if r["market_win_odds"] >= 25.0 else (0.08 if r["market_win_odds"] <= 6.5 else 0.0)
            b2_u = 1.08 * math.log(max(1e-4, pub_probs[idx])) + 0.68 * fundamental_composite + flb_adj
            b2_logits.append(b2_u)

        max_b1 = max(b1_logits)
        b1_exps = [math.exp(u - max_b1) for u in b1_logits]
        sum_b1 = sum(b1_exps)
        b1_probs = [e / sum_b1 for e in b1_exps]

        max_b2 = max(b2_logits)
        b2_exps = [math.exp(u - max_b2) for u in b2_logits]
        sum_b2 = sum(b2_exps)
        raw_b2_probs = [e / sum_b2 for e in b2_exps]

        # Apply Bayesian Shrinkage only to high-odds longshots (> 15x) to prevent model hallucination
        shrunk_b2 = []
        for idx, p_raw in enumerate(raw_b2_probs):
            if runners[idx]["market_win_odds"] > 15.0:
                eff_alpha = min(0.45, shrinkage_alpha * runners[idx]["sigma_hetero"])
                shrunk_b2.append((1.0 - eff_alpha) * p_raw + eff_alpha * pub_probs[idx])
            else:
                shrunk_b2.append(p_raw)
        norm_b2 = sum(shrunk_b2)
        b2_probs = [p / norm_b2 for p in shrunk_b2]

        # 4. Discounted Harville (theta_2, theta_3) Top-3 Place, Quinella (QIN) & Place Quinella (QPL) Probabilities
        # Note HKJC official rule: When starter count <= 6, Place pays Top-2 only and QPL is not offered.
        n = len(runners)
        place_pays_three = n >= 7
        p_second = [0.0] * n
        p_third = [0.0] * n
        qin_prob_map: Dict[str, float] = {}
        qpl_prob_map: Dict[str, float] = {}

        place_affinity = []
        for idx, r in enumerate(runners):
            if r["style_code"] == "GRINDER" or r["sigma_hetero"] <= 0.80:
                place_affinity.append(1.18)
            elif pub_probs[idx] > 0.35 and r["sigma_hetero"] >= 1.15:
                place_affinity.append(0.86)
            else:
                place_affinity.append(1.0)

        for i in range(n):
            pi = b2_probs[i]
            denom2 = sum((b2_probs[k] ** theta_2) * place_affinity[k] for k in range(n) if k != i)
            if denom2 <= 0:
                continue
            for j in range(n):
                if j == i:
                    continue
                p_j_given_i = ((b2_probs[j] ** theta_2) * place_affinity[j]) / denom2
                p_ij = pi * p_j_given_i
                p_second[j] += p_ij

                hi, hj = runners[i]["horse_no"], runners[j]["horse_no"]
                pair_str = f"{min(hi, hj)}-{max(hi, hj)}"
                qin_prob_map[pair_str] = qin_prob_map.get(pair_str, 0.0) + p_ij

                if not place_pays_three:
                    continue

                denom3 = sum(
                    (b2_probs[m] ** theta_3) * place_affinity[m]
                    for m in range(n)
                    if m != i and m != j
                )
                if denom3 <= 0:
                    continue
                for m in range(n):
                    if m == i or m == j:
                        continue
                    p_m_given_ij = ((b2_probs[m] ** theta_3) * place_affinity[m]) / denom3
                    p_ijm = p_ij * p_m_given_ij
                    p_third[m] += p_ijm

                    hm = runners[m]["horse_no"]
                    for a_no, b_no in ((hi, hj), (hi, hm), (hj, hm)):
                        pq_str = f"{min(a_no, b_no)}-{max(a_no, b_no)}"
                        qpl_prob_map[pq_str] = qpl_prob_map.get(pq_str, 0.0) + p_ijm

        if place_pays_three:
            qpl_total = sum(qpl_prob_map.values())
            if qpl_total > 0:
                for k in list(qpl_prob_map.keys()):
                    qpl_prob_map[k] = qpl_prob_map[k] * (3.0 / qpl_total)

        # 5. Annotate each runner with EV, Closing Odds Estimate, Key Signals & Kelly Stake
        bankroll = self._get_current_bankroll_hkd(cfg)
        kelly_frac = float(cfg.get("kelly_fraction", 0.25))
        min_ev_win = float(cfg.get("min_ev_win_pct", 5.0))
        single_cap = float(cfg.get("single_bet_cap_hkd", 3000.0))
        dilution_damp = 1.0 / (1.0 + 0.15 * float(cfg.get("pool_dilution_sensitivity", 1.0)))

        for idx, r in enumerate(runners):
            p_pub = pub_probs[idx]
            p_b1 = b1_probs[idx]
            p_b2 = b2_probs[idx]
            p_top_place = min(
                0.96,
                (p_b2 + p_second[idx] + p_third[idx]) if place_pays_three else (p_b2 + p_second[idx]),
            )

            m_win_odds = r["market_win_odds"]
            m_pla_odds = r["market_place_odds"]

            pred_close_win = m_win_odds
            pred_close_pla = m_pla_odds

            win_ev_pct = round((p_b2 * pred_close_win - 1.0) * 100.0, 1)
            place_ev_pct = round((p_top_place * pred_close_pla - 1.0) * 100.0, 1)

            b_win = max(0.05, pred_close_win - 1.0)
            k_win = max(0.0, (p_b2 * (b_win + 1.0) - 1.0) / b_win) if win_ev_pct >= min_ev_win else 0.0
            rec_win_stake = round(min(single_cap, bankroll * k_win * kelly_frac * dilution_damp), -1)

            b_pla = max(0.05, pred_close_pla - 1.0)
            k_pla = max(0.0, (p_top_place * (b_pla + 1.0) - 1.0) / b_pla) if place_ev_pct >= min_ev_win else 0.0
            rec_pla_stake = round(min(single_cap, bankroll * k_pla * (kelly_frac * 0.75) * dilution_damp), -1)

            # Build concise, high-signal tags for the UI table
            key_signals: List[str] = []
            if r["speed_figure_edge"] >= 0.22:
                key_signals.append(f"⚡ 最佳時間速指領先 ({r['best_time_str']})")
            if r["censored_boost"] >= 0.15:
                key_signals.append(r["nlp_tag_label"])
            if r["intent_score"] >= 0.22:
                key_signals.append(r["intent_label"])
            if r["cross_pool_ratio"] >= 1.15:
                key_signals.append(f"💧 連贏/位置聰明錢支撐 ({r['cross_pool_ratio']}x)")
            if r["win_odds_drop_pct"] >= 10.0 or r["pla_odds_drop_pct"] >= 10.0:
                drop_v = max(r["win_odds_drop_pct"], r["pla_odds_drop_pct"])
                key_signals.append(f"📉 馬會落飛指標 -{drop_v:.0f}%")
            if not key_signals:
                key_signals.append(r["intent_label"])

            r["public_prob"] = round(p_pub, 4)
            r["benter1_prob"] = round(p_b1, 4)
            r["benter2_prob"] = round(p_b2, 4)
            r["benter2_place_prob"] = round(p_top_place, 4)
            r["prob_edge_vs_b1_pct"] = round((p_b2 - p_b1) * 100.0, 2)
            r["predicted_closing_odds"] = pred_close_win
            r["predicted_closing_place_odds"] = pred_close_pla
            r["win_ev_pct"] = win_ev_pct
            r["place_ev_pct"] = place_ev_pct
            r["rec_win_stake_hkd"] = max(0.0, rec_win_stake)
            r["rec_place_stake_hkd"] = max(0.0, rec_pla_stake)
            r["key_signals"] = key_signals[:3]

        # 6. Evaluate Real Quinella (QIN) & Place Quinella (QPL) Pools using Real HKJC Pool Odds!
        # Anchor candidate pairs around the top-4 Benter 2.0 ranked horses so we only bet high-hit-rate pairs.
        runner_by_no = {r["horse_no"]: r for r in runners}
        top4_nos = {
            r["horse_no"]
            for r in sorted(runners, key=lambda x: x["benter2_prob"], reverse=True)[:4]
        }
        qin_odds_map = race.get("_qin_odds_map") or {}
        qpl_odds_map = race.get("_qpl_odds_map") or {}
        min_ev_exotic = float(cfg.get("min_ev_exotic_pct", 12.0))
        exotic_recs: List[Dict[str, Any]] = []

        top_pairs = sorted(qin_prob_map.items(), key=lambda x: x[1], reverse=True)[:15]
        for pair_key, p_qin_b2 in top_pairs:
            h1, h2 = [int(x) for x in pair_key.split("-")]
            if h1 not in top4_nos or h2 not in top4_nos:
                continue
            r1, r2 = runner_by_no.get(h1), runner_by_no.get(h2)
            if not r1 or not r2:
                continue

            p1_pub, p2_pub = r1["public_prob"], r2["public_prob"]
            p_qin_h1 = (p1_pub * p2_pub / max(0.01, 1.0 - p1_pub)) + (p2_pub * p1_pub / max(0.01, 1.0 - p2_pub))

            real_qin_odds = qin_odds_map.get(pair_key)
            real_qpl_odds = qpl_odds_map.get(pair_key) if place_pays_three else None
            p_qpl_b2 = qpl_prob_map.get(pair_key, 0.0) if place_pays_three else 0.0

            qin_ev = round((p_qin_b2 * real_qin_odds - 1.0) * 100.0, 1) if real_qin_odds else -99.0
            qpl_ev = round((p_qpl_b2 * real_qpl_odds - 1.0) * 100.0, 1) if real_qpl_odds else -99.0

            # Prefer Place Quinella (位置Q) when both horses are Top-3/4 contenders and QPL EV >= 5%
            if place_pays_three and real_qpl_odds and qpl_ev >= max(4.0, min_ev_exotic * 0.5) and p_qpl_b2 >= 0.16:
                pool_type = "PLACE_QUINELLA"
                pool_label = "位置Q (QPL)"
                chosen_p = p_qpl_b2
                chosen_h1 = min(0.85, p_qin_h1 * 2.55)
                chosen_odds = real_qpl_odds
                chosen_ev = qpl_ev
            elif real_qin_odds and qin_ev >= max(6.0, min_ev_exotic * 0.6) and p_qin_b2 >= 0.06:
                pool_type = "QUINELLA"
                pool_label = "連贏 (QIN)"
                chosen_p = p_qin_b2
                chosen_h1 = p_qin_h1
                chosen_odds = real_qin_odds
                chosen_ev = qin_ev
            else:
                continue

            b_ex = max(0.4, chosen_odds - 1.0)
            k_ex = max(0.0, (chosen_p * (b_ex + 1.0) - 1.0) / b_ex)
            stake_ex = round(min(1000.0, max(200.0, bankroll * k_ex * (kelly_frac * 0.55))), -1)

            exotic_recs.append(
                {
                    "pool_type": pool_type,
                    "pool_label": pool_label,
                    "selection_key": pair_key,
                    "selection_label": f"#{h1} {r1['horse_name_ch']} × #{h2} {r2['horse_name_ch']}",
                    "benter2_prob": round(chosen_p, 4),
                    "harville1_prob": round(chosen_h1, 4),
                    "market_odds": chosen_odds,
                    "ev_pct": chosen_ev,
                    "rec_stake_hkd": stake_ex,
                    "rationale": f"馬會實時{pool_label}賠率 {chosen_odds}x · Top-4 主力互串 (命中率 {(chosen_p*100):.1f}%, EV +{chosen_ev}%)",
                }
            )

        exotic_recs.sort(key=lambda x: (x["benter2_prob"] * 0.70 + (x["ev_pct"] / 100.0) * 0.30), reverse=True)
        race["exotic_recommendations"] = exotic_recs[:3]
        race["recommended_bets_preview"] = self._build_strategy_preview_items(race, cfg, bankroll)

    def _build_strategy_preview_items(
        self, race: Dict[str, Any], cfg: Dict[str, Any], bankroll: float
    ) -> List[Dict[str, Any]]:
        """
        Builds the unified candidate list of +EV bets (Win, Place, Exotic) for a race,
        used both by the pre-window UI/Telegram preview and by the T-5m final lock.
        """
        max_race_budget = bankroll * (float(cfg.get("max_race_exposure_pct", 8.0)) / 100.0)
        min_ev = float(cfg.get("min_ev_win_pct", 5.0))
        ranked_runners = sorted(race.get("runners") or [], key=lambda x: x["benter2_prob"], reverse=True)
        preview_items: List[Dict[str, Any]] = []
        race_staked = 0.0
        win_bet_horse_no: Optional[int] = None
        single_bet_horses = set()

        win_candidates = sorted(
            [
                r
                for r in ranked_runners[:3]
                if r["win_ev_pct"] >= min_ev
                and r["rec_win_stake_hkd"] >= 100.0
                and 1.4 <= r["market_win_odds"] <= 18.0
                and r["benter2_prob"] >= 0.14
            ],
            key=lambda x: (x["benter2_prob"] * 0.70 + (x["win_ev_pct"] / 100.0) * 0.30),
            reverse=True,
        )
        for r in win_candidates[:1]:
            stake = min(r["rec_win_stake_hkd"], max(0.0, max_race_budget - race_staked))
            if stake >= 100.0:
                preview_items.append(
                    {
                        "pool_type": "WIN",
                        "pool_label": "獨贏",
                        "selection_key": str(r["horse_no"]),
                        "selection_label": f"#{r['horse_no']} {r['horse_name_ch']}",
                        "stake_hkd": stake,
                        "ev_pct": r["win_ev_pct"],
                    }
                )
                win_bet_horse_no = r["horse_no"]
                single_bet_horses.add(int(r["horse_no"]))
                race_staked += stake

        place_candidates = sorted(
            [
                r
                for r in ranked_runners[:4]
                if r["horse_no"] != win_bet_horse_no
                and r["place_ev_pct"] >= min_ev
                and r["rec_place_stake_hkd"] >= 100.0
                and r["benter2_place_prob"] >= 0.36
                and 1.2 <= r["market_place_odds"] <= 6.5
            ],
            key=lambda x: (x["benter2_place_prob"] * 0.75 + (x["place_ev_pct"] / 100.0) * 0.25),
            reverse=True,
        )
        for r in place_candidates[:1]:
            stake = min(r["rec_place_stake_hkd"], max(0.0, max_race_budget - race_staked))
            if stake >= 100.0:
                preview_items.append(
                    {
                        "pool_type": "PLACE",
                        "pool_label": "位置",
                        "selection_key": str(r["horse_no"]),
                        "selection_label": f"#{r['horse_no']} {r['horse_name_ch']}",
                        "stake_hkd": stake,
                        "ev_pct": r["place_ev_pct"],
                    }
                )
                single_bet_horses.add(int(r["horse_no"]))
                race_staked += stake

        if cfg.get("enable_exotic_bets", True) and race.get("exotic_recommendations"):
            for top_ex in race["exotic_recommendations"][:1]:
                pair_nos = {int(x) for x in top_ex["selection_key"].split("-") if x.isdigit()}
                overlap_cnt = len(pair_nos.intersection(single_bet_horses))
                corr_discount = 0.68 if overlap_cnt >= 1 else 1.0
                raw_ex_stake = round(top_ex["rec_stake_hkd"] * corr_discount, -1)
                stake = min(raw_ex_stake, max(0.0, max_race_budget - race_staked))
                if stake >= 100.0:
                    preview_items.append(
                        {
                            "pool_type": top_ex["pool_type"],
                            "pool_label": "連贏" if top_ex["pool_type"] == "QUINELLA" else "位置Q",
                            "selection_key": top_ex["selection_key"],
                            "selection_label": top_ex["selection_label"],
                            "stake_hkd": stake,
                            "ev_pct": top_ex["ev_pct"],
                        }
                    )
                    race_staked += stake

        return preview_items

    def _get_current_bankroll_hkd(self, cfg: Optional[Dict[str, Any]] = None) -> float:
        if cfg is None:
            cfg = self.get_config()
        initial_bk = float(cfg.get("initial_bankroll_hkd", 100000.0))
        with self.db._connect() as conn:
            row = conn.execute(
                """
                SELECT
                    COALESCE(SUM(CASE WHEN status = 'OPEN' THEN stake_hkd ELSE 0 END), 0.0) AS open_stake,
                    COALESCE(SUM(CASE WHEN status IN ('WON', 'LOST') THEN net_pnl_hkd ELSE 0 END), 0.0) AS realized_pnl
                FROM hkjc_paper_bets
                """
            ).fetchone()
            open_stake = float(row["open_stake"] or 0.0)
            realized_pnl = float(row["realized_pnl"] or 0.0)
        return max(2000.0, initial_bk + realized_pnl - open_stake)

    def auto_place_paper_bets_for_open_races(self, force_manual: bool = False) -> List[Dict[str, Any]]:
        """
        Places optimal pre-race Paper Bets ONLY when:
        1. The race is inside the 1–5 minute pre-post execution window (with live HKJC tote odds), OR
        2. The race already has official HKJC results (for walk-forward backtest verification), OR
        3. The user explicitly clicked the manual force-deploy button (`force_manual=True`).
        """
        cfg = self.get_config()
        if not force_manual and not cfg.get("auto_paper_bet_enabled", True):
            return []

        next_open = next((r for r in self.races if r["status"] != "SETTLED"), None)
        if not next_open:
            return []

        mins = _compute_minutes_to_post(next_open.get("post_time_iso", ""))
        next_open["minutes_to_post"] = mins
        has_official_result = any(ru.get("_actual_final_pos") == 1 for ru in next_open["runners"])
        late_win_mins = float(cfg.get("late_betting_window_minutes", 5.0))

        if not force_manual and not has_official_result:
            # Do NOT lock in paper bets before HKJC opens live pools or more than 5 minutes before post time!
            if not next_open.get("has_live_odds") or mins is None or mins > late_win_mins or mins < -3.0:
                return []

        return self._place_auto_bets_for_single_race(next_open, cfg)

    def _place_auto_bets_for_single_race(
        self, race: Dict[str, Any], cfg: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        if cfg is None:
            cfg = self.get_config()
        model_ver = self.get_model_state()["version"]
        race_id = race["race_id"]
        race_no = race["race_no"]
        race_name = race["race_name"]
        placed: List[Dict[str, Any]] = []

        with self.db._connect() as conn:
            existing_rows = conn.execute(
                "SELECT pool_type, selection_key FROM hkjc_paper_bets WHERE race_id = ?",
                (race_id,),
            ).fetchall()
            existing_keys = {(r["pool_type"], r["selection_key"]) for r in existing_rows}

        bankroll = self._get_current_bankroll_hkd(cfg)
        max_race_budget = bankroll * (float(cfg.get("max_race_exposure_pct", 8.0)) / 100.0)
        race_staked = 0.0
        min_ev = float(cfg.get("min_ev_win_pct", 5.0))

        # Identify the Top-3 ranked contenders by Benter 2.0 Win Probability
        ranked_runners = sorted(race["runners"], key=lambda x: x["benter2_prob"], reverse=True)
        top3_contender_nos = {r["horse_no"] for r in ranked_runners[:3]}
        win_bet_horse_no: Optional[int] = None

        # 1. Top Win Bet Candidate: Must be among Top-3 Benter 2.0 contenders with positive Win EV & sensible odds
        win_candidates = sorted(
            [
                r
                for r in ranked_runners[:3]
                if r["win_ev_pct"] >= min_ev
                and r["rec_win_stake_hkd"] >= 100.0
                and 1.4 <= r["market_win_odds"] <= 18.0
                and r["benter2_prob"] >= 0.14
            ],
            key=lambda x: (x["benter2_prob"] * 0.70 + (x["win_ev_pct"] / 100.0) * 0.30),
            reverse=True,
        )

        for r in win_candidates[:1]:
            sel_key = str(r["horse_no"])
            if ("WIN", sel_key) not in existing_keys:
                stake = min(r["rec_win_stake_hkd"], max(0.0, max_race_budget - race_staked))
                if stake >= 100.0:
                    bet = self._insert_paper_bet(
                        race_id=race_id,
                        race_no=race_no,
                        race_name=race_name,
                        pool_type="WIN",
                        selection_key=sel_key,
                        selection_label=f"#{r['horse_no']} {r['horse_name_ch']} (獨贏)",
                        trigger_source="AUTO_KELLY",
                        model_version=model_ver,
                        benter1_prob=r["benter1_prob"],
                        benter2_prob=r["benter2_prob"],
                        market_odds=r["market_win_odds"],
                        predicted_closing_odds=r["predicted_closing_odds"],
                        expected_value_pct=r["win_ev_pct"],
                        kelly_fraction_used=float(cfg.get("kelly_fraction", 0.25)),
                        stake_hkd=stake,
                        rationale=" ＋ ".join(r.get("key_signals") or ["Benter 2.0 主力正期望值"]),
                    )
                    existing_keys.add(("WIN", sel_key))
                    win_bet_horse_no = r["horse_no"]
                    race_staked += stake
                    placed.append(bet)

        # 2. Top Place (Top-3 / Top-2) Candidate: High place probability among Top-4 contenders, avoiding single-horse concentration
        place_candidates = sorted(
            [
                r
                for r in ranked_runners[:4]
                if r["horse_no"] != win_bet_horse_no
                and r["place_ev_pct"] >= min_ev
                and r["rec_place_stake_hkd"] >= 100.0
                and r["benter2_place_prob"] >= 0.36
                and 1.2 <= r["market_place_odds"] <= 6.5
            ],
            key=lambda x: (x["benter2_place_prob"] * 0.75 + (x["place_ev_pct"] / 100.0) * 0.25),
            reverse=True,
        )

        for r in place_candidates[:1]:
            sel_key = str(r["horse_no"])
            if ("PLACE", sel_key) not in existing_keys:
                stake = min(r["rec_place_stake_hkd"], max(0.0, max_race_budget - race_staked))
                if stake >= 100.0:
                    bet = self._insert_paper_bet(
                        race_id=race_id,
                        race_no=race_no,
                        race_name=race_name,
                        pool_type="PLACE",
                        selection_key=sel_key,
                        selection_label=f"#{r['horse_no']} {r['horse_name_ch']} (位置)",
                        trigger_source="AUTO_KELLY",
                        model_version=model_ver,
                        benter1_prob=min(0.85, r["benter1_prob"] * 2.65),
                        benter2_prob=r["benter2_place_prob"],
                        market_odds=r["market_place_odds"],
                        predicted_closing_odds=r["predicted_closing_place_odds"],
                        expected_value_pct=r["place_ev_pct"],
                        kelly_fraction_used=float(cfg.get("kelly_fraction", 0.25)) * 0.75,
                        stake_hkd=stake,
                        rationale=" ＋ ".join(r.get("key_signals") or ["高穩定度位置入位優勢"]),
                    )
                    existing_keys.add(("PLACE", sel_key))
                    race_staked += stake
                    placed.append(bet)

        # 3. Top Exotic Candidate (Quinella / Place Quinella anchored on Top-3/4 Benter 2.0 horses)
        # Audit Fix #4: Apply Multi-Pool Covariance Discount (rho_corr = 0.68) when exotic pair overlaps with single bet horse
        single_bet_horses = {int(k[1]) for k in existing_keys if k[0] in ("WIN", "PLACE") and str(k[1]).isdigit()}
        if cfg.get("enable_exotic_bets", True) and race.get("exotic_recommendations"):
            for top_ex in race["exotic_recommendations"][:1]:
                p_type = top_ex["pool_type"]
                s_key = top_ex["selection_key"]
                if (p_type, s_key) not in existing_keys:
                    pair_nos = {int(x) for x in s_key.split("-") if x.isdigit()}
                    overlap_cnt = len(pair_nos.intersection(single_bet_horses))
                    corr_discount = 0.68 if overlap_cnt >= 1 else 1.0
                    raw_ex_stake = round(top_ex["rec_stake_hkd"] * corr_discount, -1)
                    stake = min(raw_ex_stake, max(0.0, max_race_budget - race_staked))
                    if stake >= 100.0:
                        bet = self._insert_paper_bet(
                            race_id=race_id,
                            race_no=race_no,
                            race_name=race_name,
                            pool_type=p_type,
                            selection_key=s_key,
                            selection_label=f"{top_ex['selection_label']} ({top_ex['pool_label']})",
                            trigger_source="AUTO_KELLY",
                            model_version=model_ver,
                            benter1_prob=top_ex["harville1_prob"],
                            benter2_prob=top_ex["benter2_prob"],
                            market_odds=top_ex["market_odds"],
                            predicted_closing_odds=top_ex["market_odds"],
                            expected_value_pct=top_ex["ev_pct"],
                            kelly_fraction_used=round(float(cfg.get("kelly_fraction", 0.25)) * 0.55 * corr_discount, 3),
                            stake_hkd=stake,
                            rationale=top_ex["rationale"] + (" · 已套用跨彩池協方差折減風控" if overlap_cnt >= 1 else ""),
                        )
                        existing_keys.add((p_type, s_key))
                        race_staked += stake
                        placed.append(bet)

        return placed

    def _insert_paper_bet(
        self,
        race_id: str,
        race_no: int,
        race_name: str,
        pool_type: str,
        selection_key: str,
        selection_label: str,
        trigger_source: str,
        model_version: str,
        benter1_prob: float,
        benter2_prob: float,
        market_odds: float,
        predicted_closing_odds: float,
        expected_value_pct: float,
        kelly_fraction_used: float,
        stake_hkd: float,
        rationale: str,
    ) -> Dict[str, Any]:
        now = utc_now_iso()
        with self.db._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO hkjc_paper_bets (
                    race_id, race_no, race_name, pool_type, selection_key, selection_label,
                    trigger_source, model_version, benter1_prob, benter2_prob,
                    market_odds, predicted_closing_odds, expected_value_pct,
                    kelly_fraction_used, stake_hkd, status, rationale, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN', ?, ?)
                """,
                (
                    race_id,
                    race_no,
                    race_name,
                    pool_type,
                    selection_key,
                    selection_label,
                    trigger_source,
                    model_version,
                    benter1_prob,
                    benter2_prob,
                    market_odds,
                    predicted_closing_odds,
                    expected_value_pct,
                    kelly_fraction_used,
                    stake_hkd,
                    rationale,
                    now,
                ),
            )
            bet_id = cur.lastrowid
            row = conn.execute("SELECT * FROM hkjc_paper_bets WHERE id = ?", (bet_id,)).fetchone()
            return dict(row)

    def place_manual_paper_bet(
        self, race_id: str, pool_type: str, selection_key: str, stake_hkd: float
    ) -> Dict[str, Any]:
        race = next((r for r in self.races if r["race_id"] == race_id), None)
        if not race:
            raise ValueError(f"找不到賽事 {race_id}")
        if race["status"] == "SETTLED":
            raise ValueError(f"第 {race['race_no']} 場已完賽結算，無法再新增注單")

        cfg = self.get_config()
        single_cap = float(cfg.get("single_bet_cap_hkd", 3000.0))
        stake_hkd = min(single_cap, max(10.0, round(float(stake_hkd), 0)))
        bankroll = self._get_current_bankroll_hkd(cfg)
        if stake_hkd > bankroll:
            raise ValueError(f"可用資金不足 (目前可用 HK${bankroll:,.0f})")

        model_ver = self.get_model_state()["version"]
        pool_type = pool_type.upper()
        if pool_type == "PLACE_QUINELLA" and len(race["runners"]) < 7:
            raise ValueError("本場出賽馬匹少於 7 匹，香港馬會不設位置Q (QPL) 彩池")

        if pool_type in ("WIN", "PLACE"):
            horse_no = int(selection_key)
            selection_key = str(horse_no)
            runner = next((ru for ru in race["runners"] if ru["horse_no"] == horse_no), None)
            if not runner:
                raise ValueError(f"找不到馬匹 #{horse_no}")
            b1_p = runner["benter1_prob"] if pool_type == "WIN" else min(0.85, runner["benter1_prob"] * 2.65)
            b2_p = runner["benter2_prob"] if pool_type == "WIN" else runner["benter2_place_prob"]
            m_odds = runner["market_win_odds"] if pool_type == "WIN" else runner["market_place_odds"]
            c_odds = runner["predicted_closing_odds"] if pool_type == "WIN" else runner["predicted_closing_place_odds"]
            ev = runner["win_ev_pct"] if pool_type == "WIN" else runner["place_ev_pct"]
            label = f"#{runner['horse_no']} {runner['horse_name_ch']} ({'位置' if pool_type == 'PLACE' else '獨贏'})"
            rationale = "手動策略注單 — " + " ＋ ".join(runner.get("key_signals") or ["Benter 2.0 定價分析"])
        else:
            parts = [int(x.strip()) for x in selection_key.split("-") if x.strip().isdigit()]
            if len(parts) != 2 or parts[0] == parts[1]:
                raise ValueError("連贏/位置Q 請選擇兩匹不同馬號 (例如 6-12)")
            h1, h2 = min(parts[0], parts[1]), max(parts[0], parts[1])
            selection_key = f"{h1}-{h2}"
            r1 = next((ru for ru in race["runners"] if ru["horse_no"] == h1), None)
            r2 = next((ru for ru in race["runners"] if ru["horse_no"] == h2), None)
            if not r1 or not r2:
                raise ValueError("找不到指定馬號")
            ex_match = next(
                (ex for ex in (race.get("exotic_recommendations") or []) if ex["selection_key"] == selection_key and ex["pool_type"] == pool_type),
                None,
            )
            if ex_match:
                b2_p = ex_match["benter2_prob"]
                b1_p = ex_match["harville1_prob"]
                m_odds = ex_match["market_odds"]
                ev = ex_match["ev_pct"]
                rationale = ex_match["rationale"]
            else:
                odds_map = race.get("_qin_odds_map" if pool_type == "QUINELLA" else "_qpl_odds_map") or {}
                m_odds = odds_map.get(selection_key) or 15.0
                b2_p = round(
                    (r1["benter2_prob"] * r2["benter2_prob"] / max(0.05, 1.0 - r1["benter2_prob"])) * 2.0
                    if pool_type == "QUINELLA"
                    else min(0.75, (r1["benter2_place_prob"] * r2["benter2_place_prob"]) * 1.4),
                    4,
                )
                b1_p = round(b2_p * 0.85, 4)
                ev = round((b2_p * m_odds - 1.0) * 100.0, 1)
                rationale = f"手動複式注單 ({'連贏' if pool_type == 'QUINELLA' else '位置Q'})"
            c_odds = m_odds
            p_lbl = "連贏 QIN" if pool_type == "QUINELLA" else "位置Q QPL"
            label = f"#{h1} {r1['horse_name_ch']} × #{h2} {r2['horse_name_ch']} ({p_lbl})"

        with self.db._connect() as conn:
            dup = conn.execute(
                "SELECT id FROM hkjc_paper_bets WHERE race_id = ? AND pool_type = ? AND selection_key = ? AND status = 'OPEN' LIMIT 1",
                (race_id, pool_type, selection_key),
            ).fetchone()
            if dup:
                raise ValueError(f"已存在相同的未結算注單：{label}")

        return self._insert_paper_bet(
            race_id=race_id,
            race_no=race["race_no"],
            race_name=race["race_name"],
            pool_type=pool_type,
            selection_key=selection_key,
            selection_label=label,
            trigger_source="MANUAL",
            model_version=model_ver,
            benter1_prob=b1_p,
            benter2_prob=b2_p,
            market_odds=m_odds,
            predicted_closing_odds=c_odds,
            expected_value_pct=ev,
            kelly_fraction_used=float(self.get_config().get("kelly_fraction", 0.25)),
            stake_hkd=stake_hkd,
            rationale=rationale,
        )

    def settle_race_and_evolve(self, race_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Settles a completed HKJC race against REAL official HKJC results & dividends,
        compares pre-race predictions vs actual outcome, diagnoses residuals/biases,
        and automatically upgrades the model weights, Harville exponents & track biases!
        """
        if race_id:
            race = next((r for r in self.races if r["race_id"] == race_id), None)
        else:
            race = next((r for r in self.races if r["status"] != "SETTLED"), None)

        if not race:
            raise ValueError("目前賽日所有已公佈賽果之場次均已結算並完成模型進化！")
        if race["status"] == "SETTLED":
            raise ValueError(f"第 {race['race_no']} 場已經結算完畢。")

        # Verify that HKJC has published official results for this race
        has_official_result = any(r.get("_actual_final_pos") == 1 for r in race["runners"])
        if not has_official_result:
            raise ValueError(
                f"第 {race['race_no']} 場 ({race['scheduled_time']}) 尚未完賽或馬會尚未公佈正式賽果，系統將於完賽後自動對比並升級。"
            )

        # Ensure pre-race paper bets exist for this race before unlocking result
        with self.db._connect() as conn:
            cnt = conn.execute(
                "SELECT COUNT(*) AS c FROM hkjc_paper_bets WHERE race_id = ?", (race["race_id"],)
            ).fetchone()["c"]
        if cnt == 0 and self.get_config().get("auto_paper_bet_enabled", True):
            self._place_auto_bets_for_single_race(race)

        # 1. Extract Official HKJC Finishing Order, Dividends, Sectional Times & Stewards' Incidents
        outcome = self._extract_real_race_outcome(race)
        winner_no = outcome["winner_no"]
        second_no = outcome["second_no"]
        third_no = outcome["third_no"]
        fourth_no = outcome["fourth_no"]
        top3_set = {winner_no, second_no, third_no}
        top2_key = f"{min(winner_no, second_no)}-{max(winner_no, second_no)}"
        pq_keys = {
            f"{min(winner_no, second_no)}-{max(winner_no, second_no)}",
            f"{min(winner_no, third_no)}-{max(winner_no, third_no)}",
            f"{min(second_no, third_no)}-{max(second_no, third_no)}",
        }
        dividends = outcome["dividends"]

        # 2. Settle all Paper Bets for this race using HKJC's official dividends (per $10 unit)
        now = utc_now_iso()
        race_pnl_hkd = 0.0
        race_staked_hkd = 0.0
        settled_bets: List[Dict[str, Any]] = []
        active_horse_nos = {r["horse_no"] for r in race["runners"]}

        with self.db._connect() as conn:
            open_bets = conn.execute(
                "SELECT * FROM hkjc_paper_bets WHERE race_id = ? AND status = 'OPEN'",
                (race["race_id"],),
            ).fetchall()

            for b in open_bets:
                b_dict = dict(b)
                stake = float(b_dict["stake_hkd"])
                p_type = b_dict["pool_type"]
                s_key = b_dict["selection_key"]

                # Check if selection was scratched -> full refund
                sel_nos = [int(x) for x in s_key.split("-") if x.isdigit()]
                if any(h_no not in active_horse_nos for h_no in sel_nos):
                    conn.execute(
                        """
                        UPDATE hkjc_paper_bets
                        SET status = 'REFUNDED', settled_dividend_per_10 = 10.0, payout_hkd = ?, net_pnl_hkd = 0.0, settled_at = ?
                        WHERE id = ?
                        """,
                        (stake, now, b_dict["id"]),
                    )
                    b_dict.update({"status": "REFUNDED", "payout_hkd": stake, "net_pnl_hkd": 0.0, "settled_at": now})
                    settled_bets.append(b_dict)
                    continue

                race_staked_hkd += stake
                won = False
                div_per_10 = 0.0
                win_map = dividends.get("WIN_MAP") or {str(winner_no): dividends["WIN"]["dividend_per_10"]}
                qin_map = dividends.get("QUINELLA_MAP") or {top2_key: dividends["QUINELLA"]["dividend_per_10"]}

                if p_type == "WIN" and str(int(s_key)) in win_map:
                    won = True
                    div_per_10 = _parse_div_float(win_map.get(str(int(s_key))), 0.0)
                elif p_type == "PLACE" and str(int(s_key)) in dividends["PLACE"]:
                    won = True
                    div_per_10 = _parse_div_float(dividends["PLACE"].get(str(int(s_key))), 15.0)
                elif p_type == "QUINELLA" and s_key in qin_map:
                    won = True
                    div_per_10 = _parse_div_float(qin_map.get(s_key), 0.0)
                elif p_type == "PLACE_QUINELLA" and s_key in dividends["PLACE_QUINELLA"] and s_key in pq_keys:
                    won = True
                    div_per_10 = _parse_div_float(dividends["PLACE_QUINELLA"].get(s_key), 25.0)

                payout = round(stake * (div_per_10 / 10.0), 2) if won else 0.0
                net_pnl = round(payout - stake, 2)
                race_pnl_hkd += net_pnl
                new_status = "WON" if won else "LOST"

                conn.execute(
                    """
                    UPDATE hkjc_paper_bets
                    SET status = ?, settled_dividend_per_10 = ?, payout_hkd = ?, net_pnl_hkd = ?, settled_at = ?
                    WHERE id = ?
                    """,
                    (new_status, div_per_10, payout, net_pnl, now, b_dict["id"]),
                )
                b_dict.update(
                    {
                        "status": new_status,
                        "settled_dividend_per_10": div_per_10,
                        "payout_hkd": payout,
                        "net_pnl_hkd": net_pnl,
                        "settled_at": now,
                    }
                )
                settled_bets.append(b_dict)

        # 3. Post-Race Comparison, Empirical Residual Attribution & Self-Upgrade
        comparison, evolution_record = self._compare_diagnose_and_upgrade(
            race=race,
            outcome=outcome,
            settled_bets=settled_bets,
            race_staked_hkd=race_staked_hkd,
            race_pnl_hkd=race_pnl_hkd,
        )

        # 4. Save Race Result to SQLite
        settlement_payload = {
            "race_id": race["race_id"],
            "race_no": race["race_no"],
            "race_name": race["race_name"],
            "winner_no": winner_no,
            "second_no": second_no,
            "third_no": third_no,
            "fourth_no": fourth_no,
            "actual_pace_scenario": outcome["actual_pace_scenario"],
            "dividends": dividends,
            "full_order": outcome["full_order"],
            "comparison": comparison,
            "settled_at": now,
        }
        with self.db._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO hkjc_race_results (
                    race_id, race_no, race_name, winner_no, second_no, third_no, fourth_no,
                    actual_pace_scenario, dividends_json, full_order_json, comparison_json, settled_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    race["race_id"],
                    race["race_no"],
                    race["race_name"],
                    winner_no,
                    second_no,
                    third_no,
                    fourth_no,
                    outcome["actual_pace_scenario"],
                    json.dumps(dividends, ensure_ascii=False),
                    json.dumps(outcome["full_order"], ensure_ascii=False),
                    json.dumps(comparison, ensure_ascii=False),
                    now,
                ),
            )

        race["status"] = "SETTLED"
        race["settlement"] = settlement_payload

        # 5. Refresh stale open AUTO_KELLY bets on remaining races (that have NOT already been final-locked & pushed)
        # & recompute with the NEWLY UPGRADED model!
        with self.db._connect() as conn:
            for rem_r in self.races:
                if rem_r["status"] != "SETTLED" and not self._has_final_locked_push(rem_r["race_id"]):
                    conn.execute(
                        "DELETE FROM hkjc_paper_bets WHERE race_id = ? AND status = 'OPEN' AND trigger_source = 'AUTO_KELLY'",
                        (rem_r["race_id"],),
                    )
        self._recompute_all_races()
        if self.get_config().get("auto_paper_bet_enabled", True):
            self.auto_place_paper_bets_for_open_races()

        return {
            "settled_race": settlement_payload,
            "settled_bets": settled_bets,
            "evolution": evolution_record,
        }

    def _extract_real_race_outcome(self, race: Dict[str, Any]) -> Dict[str, Any]:
        """
        Extracts the 100% real official HKJC result, dividends, in-running positions,
        sectional times, and official Stewards' Reports for `race`, and saves each horse's
        Stewards' Report into `hkjc_horse_incidents` for future races.
        """
        runners = race["runners"]
        lr_cache = race.get("_lr_cache") or {}
        perf_by_no = lr_cache.get("perf_by_no") or {}
        incidents_by_no = lr_cache.get("incidents_by_no") or {}
        sec_times = lr_cache.get("sectional_times") or []
        divs_raw = race.get("_official_divs_raw") or {}

        sorted_runners = sorted(
            runners,
            key=lambda r: (
                r.get("_actual_final_pos")
                or (perf_by_no.get(str(r["horse_no"])) or {}).get("position")
                or 99,
                r["horse_no"],
            ),
        )

        full_order = []
        now = utc_now_iso()
        m_date = self.meeting_info.get("date", "2026-11-22")

        with self.db._connect() as conn:
            for idx, r in enumerate(sorted_runners):
                h_no = r["horse_no"]
                p_info = perf_by_no.get(str(h_no)) or {}
                pos = r.get("_actual_final_pos") or p_info.get("position") or (idx + 1)
                in_running = p_info.get("in_running") or "-"
                finish_time = p_info.get("finish_time") or "-"
                beaten_margin = p_info.get("beaten_margin") or ("---" if pos == 1 else "-")
                inc_text = incidents_by_no.get(str(h_no)) or "沿途走位正常，無特別事故報告。"

                # Infer actual running style from in_running string (e.g. '8 6 1' or '1 1 1')
                in_run_nums = [int(x) for x in re.findall(r"\d+", in_running)]
                actual_style = ""
                if in_run_nums:
                    first_call = in_run_nums[0]
                    if first_call <= 2:
                        actual_style = "LEADER"
                    elif first_call <= 5:
                        actual_style = "ON_PACE"
                    elif first_call >= 9:
                        actual_style = "CLOSER"
                    else:
                        actual_style = "MIDFIELD"

                # Save Stewards' Incident to SQLite knowledge base for future races
                if r.get("horse_code"):
                    parsed_nlp = _extract_nlp_steward_tags(inc_text)
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO hkjc_horse_incidents (
                            horse_code, horse_name, last_race_date, last_race_no,
                            incident_text, nlp_label, censored_boost, in_running_style, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            r["horse_code"],
                            r["horse_name_ch"],
                            m_date,
                            race["race_no"],
                            inc_text,
                            parsed_nlp["label"],
                            parsed_nlp["boost"],
                            actual_style,
                            now,
                        ),
                    )

                full_order.append(
                    {
                        "position": pos,
                        "horse_no": h_no,
                        "horse_name": r["horse_name"],
                        "horse_name_ch": r["horse_name_ch"],
                        "jockey": r["jockey"],
                        "trainer": r["trainer"],
                        "draw": r["draw"],
                        "style_label": r["style_label"],
                        "in_running": in_running,
                        "actual_style": actual_style,
                        "benter1_prob": r["benter1_prob"],
                        "benter2_prob": r["benter2_prob"],
                        "market_win_odds": r["market_win_odds"],
                        "beaten_lengths": beaten_margin,
                        "beaten_lengths_num": round(_parse_beaten_lengths(beaten_margin, pos), 2),
                        "finish_time_sec": finish_time,
                        "steward_incident": inc_text,
                    }
                )

        w_item = full_order[0]
        s_item = full_order[1] if len(full_order) > 1 else w_item
        t_item = full_order[2] if len(full_order) > 2 else s_item
        f_item = full_order[3] if len(full_order) > 3 else t_item

        w_no = int(w_item["horse_no"])
        s_no = int(s_item["horse_no"])
        t_no = int(t_item["horse_no"])
        f_no = int(f_item["horse_no"])

        # Parse official HKJC dividends from GraphQL `divs_raw` (including Dead-Heat support)
        place_pays_three = len(runners) >= 7
        win_div_10 = round(float(w_item["market_win_odds"]) * 10.0, 1)
        win_divs_map: Dict[str, float] = {}
        for d in divs_raw.get("WIN") or []:
            c_no = int(re.sub(r"\D", "", str(d.get("winComb", "0"))) or 0)
            if c_no > 0:
                parsed_w = _parse_div_float(d.get("div"), win_div_10)
                win_divs_map[str(c_no)] = parsed_w
                if c_no == w_no:
                    win_div_10 = parsed_w
        if str(w_no) not in win_divs_map:
            win_divs_map[str(w_no)] = win_div_10

        place_divs: Dict[str, float] = {}
        for d in divs_raw.get("PLA") or []:
            c_no = str(int(re.sub(r"\D", "", str(d.get("winComb", "0"))) or 0))
            place_divs[c_no] = _parse_div_float(d.get("div"), 15.0)
        place_items = (w_item, s_item, t_item) if place_pays_three else (w_item, s_item)
        for h_item in place_items:
            k_str = str(h_item["horse_no"])
            if k_str not in place_divs:
                r_match = next((ru for ru in runners if ru["horse_no"] == h_item["horse_no"]), None)
                place_divs[k_str] = round((r_match["market_place_odds"] if r_match else 1.8) * 10.0, 1)

        q_key = f"{min(w_no, s_no)}-{max(w_no, s_no)}"
        q_div_10 = round((race.get("_qin_odds_map", {}).get(q_key) or 25.0) * 10.0, 1)
        qin_divs_map: Dict[str, float] = {}
        for d in divs_raw.get("QIN") or []:
            parts = [int(x) for x in str(d.get("winComb", "")).split(",") if x.isdigit()]
            if len(parts) == 2:
                k_pair = f"{min(parts[0], parts[1])}-{max(parts[0], parts[1])}"
                parsed_q = _parse_div_float(d.get("div"), q_div_10)
                qin_divs_map[k_pair] = parsed_q
                if k_pair == q_key:
                    q_div_10 = parsed_q
        if q_key not in qin_divs_map:
            qin_divs_map[q_key] = q_div_10

        pq_divs: Dict[str, float] = {}
        if place_pays_three:
            for d in divs_raw.get("QPL") or []:
                parts = [int(x) for x in str(d.get("winComb", "")).split(",") if x.isdigit()]
                if len(parts) == 2:
                    k_pair = f"{min(parts[0], parts[1])}-{max(parts[0], parts[1])}"
                    pq_divs[k_pair] = _parse_div_float(d.get("div"), 20.0)
            for a_no, b_no in ((w_no, s_no), (w_no, t_no), (s_no, t_no)):
                k_pair = f"{min(a_no, b_no)}-{max(a_no, b_no)}"
                if k_pair not in pq_divs:
                    pq_divs[k_pair] = round((race.get("_qpl_odds_map", {}).get(k_pair) or 10.0) * 10.0, 1)

        # Describe actual pace & sectional scenario
        w_in_run = [int(x) for x in re.findall(r"\d+", w_item.get("in_running", ""))]
        sec_str = " / ".join(f"{s}s" for s in sec_times) if sec_times else "官方標準分段"
        if w_in_run and w_in_run[0] >= 7:
            actual_pace_desc = f"🚀 後上反先局 (分段: {sec_str}) — 頭馬 #{w_no} {w_item['horse_name_ch']} 沿途走位 [{w_item['in_running']}] 後勁凌厲奪冠"
        elif w_in_run and w_in_run[0] <= 2:
            actual_pace_desc = f"⚡ 前領放頭局 (分段: {sec_str}) — 頭馬 #{w_no} {w_item['horse_name_ch']} 沿途走位 [{w_item['in_running']}] 守好位一放到底"
        else:
            actual_pace_desc = f"⚖️ 居中突圍局 (分段: {sec_str}) — 頭馬 #{w_no} {w_item['horse_name_ch']} 沿途走位 [{w_item['in_running']}] 中段發力勝出"

        return {
            "winner_no": w_no,
            "second_no": s_no,
            "third_no": t_no,
            "fourth_no": f_no,
            "actual_pace_scenario": actual_pace_desc,
            "full_order": full_order,
            "dividends": {
                "WIN": {"horse_no": w_no, "horse_name": w_item["horse_name_ch"], "dividend_per_10": win_div_10},
                "WIN_MAP": win_divs_map,
                "PLACE": place_divs,
                "QUINELLA": {"combination": q_key, "dividend_per_10": q_div_10},
                "QUINELLA_MAP": qin_divs_map,
                "PLACE_QUINELLA": pq_divs,
            },
        }

    def _compare_diagnose_and_upgrade(
        self,
        race: Dict[str, Any],
        outcome: Dict[str, Any],
        settled_bets: List[Dict[str, Any]],
        race_staked_hkd: float,
        race_pnl_hkd: float,
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """
        Compares pre-race predictions against the real HKJC result, diagnoses empirical
        track/pace/Harville/submodel residuals, and executes an online model upgrade.
        """
        model_state = self.get_model_state()
        old_version = model_state["version"]
        runners = race["runners"]

        winner_no = outcome["winner_no"]
        second_no = outcome["second_no"]
        third_no = outcome["third_no"]
        top3_nos = {winner_no, second_no, third_no}

        winner_runner = next((r for r in runners if r["horse_no"] == winner_no), runners[0])
        second_runner = next((r for r in runners if r["horse_no"] == second_no), runners[0])
        third_runner = next((r for r in runners if r["horse_no"] == third_no), runners[0])

        b1_ranked = [r["horse_no"] for r in sorted(runners, key=lambda x: x["benter1_prob"], reverse=True)]
        b2_ranked = [r["horse_no"] for r in sorted(runners, key=lambda x: x["benter2_prob"], reverse=True)]

        w_rank_b1 = (b1_ranked.index(winner_no) + 1) if winner_no in b1_ranked else len(runners)
        w_rank_b2 = (b2_ranked.index(winner_no) + 1) if winner_no in b2_ranked else len(runners)

        p_win_b1 = max(1e-4, winner_runner["benter1_prob"])
        p_win_b2 = max(1e-4, winner_runner["benter2_prob"])
        log_loss_b1 = round(-math.log(p_win_b1), 4)
        log_loss_b2 = round(-math.log(p_win_b2), 4)

        brier_b1 = round(
            sum(((1.0 if r["horse_no"] == winner_no else 0.0) - r["benter1_prob"]) ** 2 for r in runners),
            4,
        )
        brier_b2 = round(
            sum(((1.0 if r["horse_no"] == winner_no else 0.0) - r["benter2_prob"]) ** 2 for r in runners),
            4,
        )

        # =====================================================================
        # 1. FULL-FIELD C(N,2) PLACKETT-LUCE PAIRWISE RANKING & BEATEN-LENGTHS GRADIENT
        # =====================================================================
        # Instead of only looking at Top-3 vs field average, extract all C(N,2) = N(N-1)/2
        # pairwise comparisons (e.g. 91 pairs for 14 runners), weighted by continuous
        # beaten lengths (L_j - L_i) and pairwise probability surprise (1 - P_i / (P_i + P_j)).
        submodel_keys = ["fundamental", "pace_iia", "nlp_censored", "intent", "hetero_mc", "microstructure"]
        weight_key_map = {
            "fundamental": "w_fundamental",
            "pace_iia": "w_pace_iia",
            "nlp_censored": "w_nlp_censored",
            "intent": "w_intent",
            "hetero_mc": "w_hetero_mc",
            "microstructure": "w_microstructure",
        }

        runner_by_no = {int(r["horse_no"]): r for r in runners}
        ordered_runners: List[Dict[str, Any]] = []
        for fo in outcome["full_order"]:
            r_obj = runner_by_no.get(int(fo["horse_no"]))
            if r_obj is not None:
                ordered_runners.append(
                    {
                        "runner": r_obj,
                        "pos": int(fo["position"]),
                        "beaten_len": float(fo.get("beaten_lengths_num", _parse_beaten_lengths(fo.get("beaten_lengths", ""), int(fo["position"])))),
                        "in_running": str(fo.get("in_running", "")),
                    }
                )

        n_ordered = len(ordered_runners)
        pairwise_samples = max(1, (n_ordered * (n_ordered - 1)) // 2)
        pair_grad_accum = {sk: 0.0 for sk in submodel_keys}
        pair_weight_sum = 0.0

        for idx_i in range(n_ordered):
            item_i = ordered_runners[idx_i]
            r_i = item_i["runner"]
            p_i = max(1e-4, float(r_i["benter2_prob"]))
            len_i = item_i["beaten_len"]
            for idx_j in range(idx_i + 1, n_ordered):
                item_j = ordered_runners[idx_j]
                r_j = item_j["runner"]
                p_j = max(1e-4, float(r_j["benter2_prob"]))
                len_j = max(len_i, item_j["beaten_len"])

                # Continuous beaten-lengths weight: larger margin between i and j carries clearer signal,
                # capped smoothly via tanh so a 20L tailed-off runner doesn't dominate
                delta_len = max(0.15, len_j - len_i)
                margin_w = math.tanh(delta_len / 2.5)
                # Focus bonus when at least one horse in the pair is in Top-5
                rank_focus = 1.35 if idx_i < 4 else (1.0 if idx_i < 8 else 0.65)
                w_ij = margin_w * rank_focus

                # Plackett-Luce pairwise surprise: e_ij = 1 - P(i beats j)
                p_i_beats_j = p_i / (p_i + p_j)
                surprise_ij = 1.0 - p_i_beats_j

                pair_weight_sum += w_ij
                for sk in submodel_keys:
                    diff_score = float(r_i["submodel_scores"][sk]) - float(r_j["submodel_scores"][sk])
                    pair_grad_accum[sk] += w_ij * surprise_ij * diff_score

        submodel_gradients: Dict[str, float] = {}
        for sk in submodel_keys:
            pl_grad = (pair_grad_accum[sk] / max(1e-6, pair_weight_sum)) * 1.65
            submodel_gradients[sk] = round(pl_grad, 4)

        # =====================================================================
        # 2. FAST INTRADAY LAYER: FULL-FIELD RESIDUALS (TRACK BIAS, PACE & JOCKEY/TRAINER FORM)
        # =====================================================================
        # Compute expected finishing rank & expected beaten lengths from pre-race probabilities
        # vs actual finishing position & actual beaten lengths for ALL N horses.
        b2_sorted_probs = sorted(runners, key=lambda x: x["benter2_prob"], reverse=True)
        expected_rank_map: Dict[int, float] = {}
        for r_obj in runners:
            h_no = int(r_obj["horse_no"])
            p_h = max(1e-4, float(r_obj["benter2_prob"]))
            # Expected rank E[R_i] = 1 + sum_{j != i} P(j beats i)
            exp_r = 1.0 + sum(
                max(1e-4, float(other["benter2_prob"])) / (p_h + max(1e-4, float(other["benter2_prob"])))
                for other in runners
                if int(other["horse_no"]) != h_no
            )
            expected_rank_map[h_no] = exp_r

        avg_field_draw = sum(float(r["draw"]) for r in runners) / max(1, len(runners))
        draw_cov_num = 0.0
        draw_cov_den = 0.0
        pace_cov_num = 0.0
        pace_cov_den = 0.0

        jockey_form_map: Dict[str, float] = dict(model_state.get("intraday_jockey_form") or {})
        trainer_form_map: Dict[str, float] = dict(model_state.get("intraday_trainer_form") or {})
        top_outperformers: List[Tuple[float, Dict[str, Any], float]] = []

        for item in ordered_runners:
            r_obj = item["runner"]
            h_no = int(r_obj["horse_no"])
            actual_pos = float(item["pos"])
            exp_pos = expected_rank_map.get(h_no, (n_ordered + 1) / 2.0)
            # Positive residual = horse outperformed pre-race expectation (finished better than expected rank)
            perf_residual = (exp_pos - actual_pos) / max(1.0, n_ordered / 2.0)
            top_outperformers.append((perf_residual, r_obj, item["beaten_len"]))

            # Full-field draw residual correlation (weighted by beaten-length relevance)
            rel_w = 1.0 / (1.0 + 0.12 * item["beaten_len"])
            centered_draw = (float(r_obj["draw"]) - avg_field_draw) / 6.0
            draw_cov_num += rel_w * perf_residual * centered_draw
            draw_cov_den += rel_w * (centered_draw ** 2 + 0.25)

            # Full-field in-running pace residual correlation
            in_run_nums = [int(x) for x in re.findall(r"\d+", item["in_running"])]
            first_call = float(in_run_nums[0]) if in_run_nums else float(r_obj["draw"])
            pos_gained = (first_call - actual_pos) if in_run_nums else 0.0
            centered_early = (first_call - (n_ordered + 1) / 2.0) / 5.0
            pace_cov_num += rel_w * (perf_residual * centered_early + 0.08 * (pos_gained / 5.0))
            pace_cov_den += rel_w * (centered_early ** 2 + 0.25)

            # Fast Intraday Jockey & Trainer Hotness EMA update
            j_key = str(r_obj.get("jockey") or "").strip()
            t_key = str(r_obj.get("trainer") or "").strip()
            if j_key:
                old_j = float(jockey_form_map.get(j_key, 0.0))
                jockey_form_map[j_key] = round(max(-0.45, min(0.45, 0.65 * old_j + 0.35 * perf_residual)), 3)
            if t_key:
                old_t = float(trainer_form_map.get(t_key, 0.0))
                trainer_form_map[t_key] = round(max(-0.45, min(0.45, 0.70 * old_t + 0.30 * perf_residual)), 3)

        # Update intraday turn-course draw bias (exclude Sha Tin 1000m Straight Course from turn bias!)
        is_st_straight_1000 = (race.get("venue_code") == "ST" and int(race.get("distance_m") or 0) == 1000)
        old_draw_bias = float(model_state.get("day_draw_bias", 0.0))
        if is_st_straight_1000:
            # Preserve turn-course draw bias when running on ST 1000m straight course
            new_draw_bias = old_draw_bias
        else:
            race_draw_signal = draw_cov_num / max(1e-4, draw_cov_den)
            new_draw_bias = round(max(-0.35, min(0.35, 0.65 * old_draw_bias + 0.35 * race_draw_signal)), 3)

        old_pace_bias = float(model_state.get("day_pace_bias", 0.0))
        race_pace_signal = pace_cov_num / max(1e-4, pace_cov_den)
        new_pace_bias = round(max(-0.35, min(0.35, 0.65 * old_pace_bias + 0.35 * race_pace_signal)), 3)

        # 3. Diagnose Specific Real-Race Insights & Two-Timescale Architecture Updates
        flaws_and_fixes: List[Dict[str, str]] = []
        w_order_info = outcome["full_order"][0]

        # Insight A: Full-Field C(N,2) Pairwise & Fast Intraday Track/Pace Residual Update
        draw_tendency = "內檔 (1–5檔) 慳位馬佔優" if new_draw_bias < -0.04 else "外檔衝刺馬不受阻佔優" if new_draw_bias > 0.04 else "內外檔均勢"
        pace_tendency = "後上/居中衝刺馬佔優" if new_pace_bias > 0.05 else "前領/跟前守好位馬佔優" if new_pace_bias < -0.05 else "步速正常均勢"
        top_outperformers.sort(key=lambda x: x[0], reverse=True)
        best_res_val, best_res_runner, best_res_len = top_outperformers[0] if top_outperformers else (0.0, winner_runner, 0.0)
        straight_note = "（本場為沙田 1000m 直路賽，獨立適用外檔靠欄先驗，不污染彎道檔位偏差）" if is_st_straight_1000 else ""
        flaws_and_fixes.append(
            {
                "category": f"全場 C({n_ordered},2)={pairwise_samples} 配對殘差與快速層狀態更新 (Fast Intraday Layer)",
                "finding": (
                    f"提取全場 {n_ordered} 駒完整名次與負頭馬距離建立 {pairwise_samples} 組 Plackett-Luce 成對比較；"
                    f"頭馬 #{winner_no} {winner_runner['horse_name_ch']} 沿途 [{w_order_info.get('in_running', '-')}]，"
                    f"超預期殘差最大馬匹為 #{best_res_runner['horse_no']} {best_res_runner['horse_name_ch']} (殘差 {best_res_val:+.2f}, 負 {best_res_len:.2f} 馬位)。"
                ),
                "action": (
                    f"快速層即時校準：檔位偏差 = {new_draw_bias:+.3f} ({draw_tendency}){straight_note}、"
                    f"步速偏差 = {new_pace_bias:+.3f} ({pace_tendency})，並同步更新騎練當日手風殘差 EMA。"
                ),
            }
        )

        # Insight B: Public Favorite vs Cross-Pool / Intent Winner & Slow Structural Layer
        fav_runner = min(runners, key=lambda x: x["market_win_odds"])
        theta2_delta = 0.0
        theta3_delta = 0.0
        if fav_runner["horse_no"] != winner_no:
            if fav_runner["horse_no"] not in top3_nos:
                theta2_delta = -0.010
                theta3_delta = -0.012
                flaws_and_fixes.append(
                    {
                        "category": "慢速結構層：大熱門失準與 Harville 複式指數微調 (Slow Structural Layer)",
                        "finding": f"公眾大熱門 #{fav_runner['horse_no']} {fav_runner['horse_name_ch']} ({fav_runner['market_win_odds']}倍) 未能跑入三甲，由 #{winner_no} {winner_runner['horse_name_ch']} ({winner_runner['market_win_odds']}倍) 勝出。",
                        "action": f"以預訓練先驗為錨點微調 Discounted Harville 次名指數 θ₂ ({theta2_delta:+.3f}) 與季軍指數 θ₃ ({theta3_delta:+.3f})，降低熱門馬失手時的連贏/位置Q高估誤差。",
                    }
                )
            else:
                theta2_delta = -0.004
                flaws_and_fixes.append(
                    {
                        "category": "慢速結構層：次熱門/冷門突圍全場配對歸因 (Slow Structural Layer)",
                        "finding": f"頭馬 #{winner_no} {winner_runner['horse_name_ch']} ({winner_runner['market_win_odds']}倍) 擊敗大熱門 #{fav_runner['horse_no']} {fav_runner['horse_name_ch']}，跨彩池聰明錢比值 {winner_runner['cross_pool_ratio']}x 且具備「{winner_runner['intent_label']}」。",
                        "action": f"透過 {pairwise_samples} 組全場馬位加權配對梯度與 L2 先驗正則化，平穩提升高表現子模型權重（防範單場過度擬合）。",
                    }
                )
        else:
            theta2_delta = 0.005
            theta3_delta = 0.004
            flaws_and_fixes.append(
                {
                    "category": "慢速結構層：實力熱門馬穩健兌現校準 (Slow Structural Layer)",
                    "finding": f"頭號熱門 #{winner_no} {winner_runner['horse_name_ch']} ({winner_runner['market_win_odds']}倍) 順利勝出，Benter 2.0 賦予 {(winner_runner['benter2_prob']*100):.1f}% 勝率 (排第 {w_rank_b2})。",
                    "action": f"結合離線多季先驗 ({model_state.get('offline_pretrained_races', 3850)} 場) 與本場 {pairwise_samples} 組配對梯度，平穩微調六大核心權重。",
                }
            )

        # Insight C: Stewards' Incident NLP extraction summary (Deterministic Cross-Race Memory)
        censored_in_race = [
            fo for fo in outcome["full_order"][1:10]
            if any(k in fo["steward_incident"] for k in ("難以望空", "未能望空", "受困", "擠迫", "碰撞", "收慢", "外疊"))
        ]
        if censored_in_race:
            ex_fo = censored_in_race[0]
            flaws_and_fixes.append(
                {
                    "category": "確定性事件記憶層：《競賽事件》NLP 受阻馬自動建檔",
                    "finding": f"第 {ex_fo['position']} 名 #{ex_fo['horse_no']} {ex_fo['horse_name_ch']} (負 {ex_fo.get('beaten_lengths', '-')} 馬位) 賽後報告記錄：「{ex_fo['steward_incident'][:65]}...」",
                    "action": f"已將 #{ex_fo['horse_no']} {ex_fo['horse_name_ch']} 等 {len(censored_in_race)} 駒寫入受阻免責資料庫 (`hkjc_horse_incidents`)，下次出賽時自動獲得右截斷能力補償。",
                }
            )

        # =====================================================================
        # 4. SLOW STRUCTURAL LAYER: HUBER-CLIPPED PLACKETT-LUCE GRADIENT + L2 PRIOR ANCHOR
        # =====================================================================
        # Two-Timescale Architecture: keep structural weights anchored to the multi-season
        # offline pre-trained base (lr_slow = 0.045, 8% L2 anchor to institutional prior)
        # while the Fast Intraday Layer absorbs single-day track/pace/jockey shifts.
        fluke_damp = 0.45 if winner_runner["market_win_odds"] >= 22.0 else 1.0
        lr_slow = 0.045 * fluke_damp
        old_weights = dict(model_state["weights"])
        prior_weights = DEFAULT_MODEL_STATE["weights"]
        raw_new_weights: Dict[str, float] = {}
        for sk, wk in weight_key_map.items():
            # Huber clip gradient to [-0.40, +0.40]
            grad = max(-0.40, min(0.40, submodel_gradients[sk]))
            updated_w = old_weights[wk] * math.exp(lr_slow * grad)
            # L2 anchor toward offline multi-season pre-trained prior (92% learned + 8% prior anchor)
            regularized_w = 0.92 * updated_w + 0.08 * prior_weights[wk]
            raw_new_weights[wk] = min(0.38, max(0.07, regularized_w))

        w_sum = sum(raw_new_weights.values())
        new_weights = {k: round(v / w_sum, 4) for k, v in raw_new_weights.items()}
        diff_w = round(1.0 - sum(new_weights.values()), 4)
        new_weights["w_fundamental"] = round(new_weights["w_fundamental"] + diff_w, 4)
        weight_deltas = {k: round(new_weights[k] - old_weights[k], 4) for k in new_weights}

        new_gen = int(model_state.get("generation", 0)) + 1
        new_races_learned = int(model_state.get("races_learned", 0)) + 1
        new_version = f"2.{new_gen}.0"

        model_state["version"] = new_version
        model_state["generation"] = new_gen
        model_state["races_learned"] = new_races_learned
        model_state["weights"] = new_weights
        model_state["theta_2"] = round(min(0.92, max(0.68, float(model_state["theta_2"]) + theta2_delta)), 3)
        model_state["theta_3"] = round(min(0.85, max(0.58, float(model_state["theta_3"]) + theta3_delta)), 3)
        model_state["day_draw_bias"] = new_draw_bias
        model_state["day_pace_bias"] = new_pace_bias
        model_state["intraday_jockey_form"] = jockey_form_map
        model_state["intraday_trainer_form"] = trainer_form_map

        cm = model_state["cumulative_metrics"]
        cm["benter2_log_loss_sum"] = round(cm["benter2_log_loss_sum"] + log_loss_b2, 4)
        cm["benter1_log_loss_sum"] = round(cm["benter1_log_loss_sum"] + log_loss_b1, 4)
        cm["benter2_brier_sum"] = round(cm["benter2_brier_sum"] + brier_b2, 4)
        cm["benter1_brier_sum"] = round(cm["benter1_brier_sum"] + brier_b1, 4)
        cm["total_pairwise_samples"] = int(cm.get("total_pairwise_samples", 0)) + pairwise_samples
        if w_rank_b2 == 1:
            cm["top1_hits_benter2"] += 1
        if w_rank_b1 == 1:
            cm["top1_hits_benter1"] += 1
        if w_rank_b2 <= 3:
            cm["top3_hits_benter2"] += 1
        if w_rank_b1 <= 3:
            cm["top3_hits_benter1"] += 1
        if second_no in b2_ranked[:3] and winner_no in b2_ranked[:3]:
            cm["quinella_hits_benter2"] += 1

        self._save_model_state(model_state)

        upgrade_summary = (
            f"第 {race['race_no']} 場雙時間尺度賽後學習完成 (v{old_version} → v{new_version})："
            f"真實頭馬 #{winner_no} {winner_runner['horse_name_ch']} (獨贏派彩 HK${outcome['dividends']['WIN']['dividend_per_10']})；"
            f"全場提取 C({n_ordered},2)={pairwise_samples} 組 Plackett-Luce 馬位加權配對殘差，"
            f"同步更新快速層 (跑道檔位/步速偏差、騎練當日手風) 與慢速層 (L2 先驗正則化六大權重)，本場損益 HK${race_pnl_hkd:+,.0f}。"
        )

        comparison = {
            "winner_no": winner_no,
            "winner_name": winner_runner["horse_name"],
            "winner_rank_benter1": w_rank_b1,
            "winner_rank_benter2": w_rank_b2,
            "winner_prob_benter1": winner_runner["benter1_prob"],
            "winner_prob_benter2": winner_runner["benter2_prob"],
            "log_loss_benter1": log_loss_b1,
            "log_loss_benter2": log_loss_b2,
            "log_loss_improvement_pct": round(((log_loss_b1 - log_loss_b2) / max(0.01, log_loss_b1)) * 100.0, 1),
            "brier_benter1": brier_b1,
            "brier_benter2": brier_b2,
            "pairwise_samples": pairwise_samples,
            "total_pairwise_samples": cm["total_pairwise_samples"],
            "race_staked_hkd": round(race_staked_hkd, 2),
            "race_pnl_hkd": round(race_pnl_hkd, 2),
            "bets_count": len(settled_bets),
            "bets_won": sum(1 for b in settled_bets if b["status"] == "WON"),
            "flaws_and_fixes": flaws_and_fixes,
            "old_version": old_version,
            "new_version": new_version,
            "weight_deltas": weight_deltas,
            "new_weights": new_weights,
            "theta_2": model_state["theta_2"],
            "theta_3": model_state["theta_3"],
            "day_draw_bias": new_draw_bias,
            "day_pace_bias": new_pace_bias,
            "upgrade_summary": upgrade_summary,
        }

        now = utc_now_iso()
        with self.db._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO hkjc_model_evolutions (
                    race_id, race_no, race_name, old_version, new_version,
                    benter1_log_loss, benter2_log_loss, race_pnl_hkd,
                    flaws_detected_json, weight_deltas_json, new_weights_json,
                    upgrade_summary, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    race["race_id"],
                    race["race_no"],
                    race["race_name"],
                    old_version,
                    new_version,
                    log_loss_b1,
                    log_loss_b2,
                    round(race_pnl_hkd, 2),
                    json.dumps(flaws_and_fixes, ensure_ascii=False),
                    json.dumps(weight_deltas, ensure_ascii=False),
                    json.dumps(new_weights, ensure_ascii=False),
                    upgrade_summary,
                    now,
                ),
            )
            evo_id = cur.lastrowid

        evolution_record = {
            "id": evo_id,
            "race_id": race["race_id"],
            "race_no": race["race_no"],
            "race_name": race["race_name"],
            "old_version": old_version,
            "new_version": new_version,
            "benter1_log_loss": log_loss_b1,
            "benter2_log_loss": log_loss_b2,
            "race_pnl_hkd": round(race_pnl_hkd, 2),
            "flaws_detected": flaws_and_fixes,
            "weight_deltas": weight_deltas,
            "new_weights": new_weights,
            "upgrade_summary": upgrade_summary,
            "created_at": now,
        }

        return comparison, evolution_record

    def reset_meeting(self, reset_model_weights: bool = False) -> Dict[str, Any]:
        with self.db._connect() as conn:
            conn.execute("DELETE FROM hkjc_paper_bets")
            conn.execute("DELETE FROM hkjc_race_results")
            if reset_model_weights:
                conn.execute("DELETE FROM hkjc_model_evolutions")
                init_state = {**DEFAULT_MODEL_STATE, "updated_at": utc_now_iso()}
                conn.execute(
                    "INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES (?, ?, ?)",
                    ("hkjc_model_state", json.dumps(init_state), utc_now_iso()),
                )

        for r in self.races:
            r["status"] = "BETTING_OPEN"
            r["settlement"] = None
        self._recompute_all_races()
        if self.get_config().get("auto_paper_bet_enabled", True):
            self.auto_place_paper_bets_for_open_races()
        return self.get_state()

    def _has_final_locked_push(self, race_id: str) -> bool:
        with self.db._connect() as conn:
            row = conn.execute(
                "SELECT id FROM hkjc_telegram_logs WHERE race_id = ? AND msg_type = 'PRE_RACE_FINAL_LOCK' AND status IN ('SENT', 'QUEUED_PREVIEW') LIMIT 1",
                (race_id,),
            ).fetchone()
            return bool(row)

    async def maybe_auto_progress(self) -> Optional[Dict[str, Any]]:
        """
        Periodically syncs live HKJC GraphQL data with adaptive frequency:
        1. Syncs every 18s when any race is within [-35m, +12m] of post time, else every 60s.
        2. When an upcoming race enters the 1–5 minute pre-post golden window (with live HKJC odds),
           forces an immediate live odds sync, locks the final +EV strategy bets, and pushes to Telegram!
        3. When an earlier race transitions to official results on HKJC, automatically settles it
           and upgrades the model weights/biases for subsequent races!
        """
        cfg = self.get_config()
        late_win_mins = float(cfg.get("late_betting_window_minutes", 5.0))

        for r in self.races:
            r["minutes_to_post"] = _compute_minutes_to_post(r.get("post_time_iso", ""))

        near_active_race = any(
            r["status"] != "SETTLED"
            and r.get("minutes_to_post") is not None
            and -35.0 <= float(r["minutes_to_post"]) <= 12.0
            for r in self.races
        )
        sync_interval = 18.0 if near_active_race else 60.0
        if time.monotonic() - self._last_gql_sync_mono >= sync_interval or not self.races:
            await self.sync_live_hkjc_data(force_refresh_cards=False)

        # 1. Check if any race has just entered the 1–5 minute pre-post Golden Window
        for r in list(self.races):
            if r["status"] == "SETTLED":
                continue
            has_res = any(ru.get("_actual_final_pos") == 1 for ru in r["runners"])
            mins = _compute_minutes_to_post(r.get("post_time_iso", ""))
            r["minutes_to_post"] = mins
            if not has_res and r.get("has_live_odds") and mins is not None and 0.0 <= mins <= late_win_mins:
                if not self._has_final_locked_push(r["race_id"]):
                    # Force one immediate fresh HKJC GraphQL sync right inside the T-5m window
                    await self.sync_live_hkjc_data(force_refresh_cards=False)
                    target_race = next((x for x in self.races if x["race_id"] == r["race_id"]), r)
                    self._place_auto_bets_for_single_race(target_race, cfg)
                    tg_cfg = self.get_telegram_config(mask_token=True)
                    if tg_cfg.get("enabled", True):
                        msg_text = self.format_race_strategy_telegram_message(target_race)
                        await self.send_telegram_message(
                            text=msg_text,
                            race_id=target_race["race_id"],
                            race_no=int(target_race["race_no"]),
                            msg_type="PRE_RACE_FINAL_LOCK",
                        )

        if not cfg.get("auto_live_settle_enabled", True):
            return None

        # 2. If the meeting is currently LIVE (at least one race is still OPEN/DEFINED on HKJC),
        # automatically settle any earlier race that has OFFICIAL DIVIDENDS & RESULTS on HKJC!
        has_future_open_on_hkjc = any(
            r.get("hkjc_status") in ("OPEN", "DEFINED", "CLOSED", "INTERIM", "DECLARED")
            and not any(ru.get("_actual_final_pos") == 1 for ru in r["runners"])
            for r in self.races
        )
        if has_future_open_on_hkjc:
            for r in self.races:
                has_win_div = bool((r.get("_official_divs_raw") or {}).get("WIN")) or r.get("hkjc_status") == "RESULT"
                if r["status"] != "SETTLED" and has_win_div and any(ru.get("_actual_final_pos") == 1 for ru in r["runners"]):
                    res = self.settle_race_and_evolve(r["race_id"])
                    await self.auto_push_after_settlement(res)
                    return res
        return None

    # =====================================================================
    # TELEGRAM STRATEGY & POST-RACE EVOLUTION PUSH NOTIFIER
    # =====================================================================
    def get_telegram_config(self, mask_token: bool = True) -> Dict[str, Any]:
        with self.db._connect() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key = 'hkjc_telegram_config'").fetchone()
            saved = json.loads(row["value"]) if row else {}
        bot_token = str(saved.get("bot_token") or os.getenv("TELEGRAM_BOT_TOKEN", "")).strip()
        chat_id = str(saved.get("chat_id") or os.getenv("TELEGRAM_CHAT_ID", "")).strip()
        has_token = len(bot_token) > 10
        masked = f"{bot_token[:6]}...{bot_token[-4:]}" if has_token else ""
        return {
            "enabled": bool(saved.get("enabled", True)),
            "has_token": has_token,
            "bot_token": masked if mask_token else bot_token,
            "bot_token_masked": masked,
            "chat_id": chat_id,
            "auto_push_pre_race": bool(saved.get("auto_push_pre_race", True)),
            "auto_push_post_race": bool(saved.get("auto_push_post_race", True)),
            "ready": has_token and bool(chat_id),
        }

    def update_telegram_config(self, updates: Dict[str, Any]) -> Dict[str, Any]:
        curr = self.get_telegram_config(mask_token=False)
        new_token = str(updates.get("bot_token", "")).strip()
        if new_token and "..." not in new_token:
            curr["bot_token"] = new_token
        if "chat_id" in updates:
            cand_chat = str(updates.get("chat_id") or "").strip()
            if cand_chat:
                curr["chat_id"] = cand_chat
        if "enabled" in updates:
            curr["enabled"] = bool(updates["enabled"])
        if "auto_push_pre_race" in updates:
            curr["auto_push_pre_race"] = bool(updates["auto_push_pre_race"])
        if "auto_push_post_race" in updates:
            curr["auto_push_post_race"] = bool(updates["auto_push_post_race"])

        to_save = {
            "enabled": curr["enabled"],
            "bot_token": curr["bot_token"],
            "chat_id": curr["chat_id"],
            "auto_push_pre_race": curr["auto_push_pre_race"],
            "auto_push_post_race": curr["auto_push_post_race"],
        }
        with self.db._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES (?, ?, ?)",
                ("hkjc_telegram_config", json.dumps(to_save), utc_now_iso()),
            )
        return self.get_telegram_config(mask_token=True)

    def format_race_strategy_telegram_message(self, race: Dict[str, Any]) -> str:
        m_date = self.meeting_info.get("date", "")
        pool_label_map = {
            "WIN": "獨贏",
            "PLACE": "位置",
            "QUINELLA": "連贏",
            "PLACE_QUINELLA": "位置Q",
        }

        with self.db._connect() as conn:
            race_bets = [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM hkjc_paper_bets WHERE race_id = ? ORDER BY id ASC",
                    (race["race_id"],),
                ).fetchall()
            ]

        lines = [
            f"📅 {m_date}｜第 {race['race_no']} 場｜{race['race_name']}",
            "🎯 建議投注策略及注碼：",
        ]

        if race_bets:
            for b in race_bets:
                p_name = pool_label_map.get(b["pool_type"], b["pool_type"])
                clean_sel = str(b["selection_label"]).split(" (")[0].strip()
                lines.append(f"• {p_name}：{clean_sel} — 注碼 HK${b['stake_hkd']:,.0f}")
        else:
            preview_items = race.get("recommended_bets_preview") or []
            if preview_items:
                for item in preview_items:
                    p_name = pool_label_map.get(item["pool_type"], item.get("pool_label", item["pool_type"]))
                    lines.append(f"• {p_name}：{item['selection_label']} — 注碼 HK${item['stake_hkd']:,.0f}")
            else:
                lines.append("• 本場無達標正期望值注項（建議觀望）")

        return "\n".join(lines)

    async def auto_push_after_settlement(self, settle_result: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        tg_cfg = self.get_telegram_config(mask_token=True)
        if not tg_cfg.get("enabled", True):
            return None
        next_open = next((r for r in self.races if r["status"] != "SETTLED"), None)
        if not next_open:
            return None
        has_res = any(ru.get("_actual_final_pos") == 1 for ru in next_open["runners"])
        mins = _compute_minutes_to_post(next_open.get("post_time_iso", ""))
        late_win_mins = float(self.get_config().get("late_betting_window_minutes", 5.0))
        # For live upcoming races, wait until the 1–5 minute pre-post window rather than pushing 25–30m early!
        if not has_res:
            if not next_open.get("has_live_odds") or mins is None or mins > late_win_mins or mins < 0.0:
                return None
            if self._has_final_locked_push(next_open["race_id"]):
                return None
            self._place_auto_bets_for_single_race(next_open, self.get_config())
            msg_text = self.format_race_strategy_telegram_message(next_open)
            return await self.send_telegram_message(
                text=msg_text,
                race_id=next_open.get("race_id", ""),
                race_no=int(next_open.get("race_no") or 0),
                msg_type="PRE_RACE_FINAL_LOCK",
            )
        msg_text = self.format_race_strategy_telegram_message(next_open)
        return await self.send_telegram_message(
            text=msg_text,
            race_id=next_open.get("race_id", ""),
            race_no=int(next_open.get("race_no") or 0),
            msg_type="PRE_RACE_STRATEGY",
        )

    async def send_telegram_message(
        self, text: str, race_id: str = "", race_no: int = 0, msg_type: str = "PRE_RACE_STRATEGY"
    ) -> Dict[str, Any]:
        tg_cfg = self.get_telegram_config(mask_token=False)
        token = tg_cfg["bot_token"]
        chat_id = tg_cfg["chat_id"]
        now = utc_now_iso()

        if not token:
            with self.db._connect() as conn:
                cur = conn.execute(
                    """
                    INSERT INTO hkjc_telegram_logs (
                        race_id, race_no, msg_type, status, message_text, error_detail, created_at
                    ) VALUES (?, ?, ?, 'QUEUED_PREVIEW', ?, '尚未填入 Telegram Bot Token（已生成完整推播內容預覽）', ?)
                    """,
                    (race_id, race_no, msg_type, text, now),
                )
                log_id = cur.lastrowid
            return {
                "sent": False,
                "status": "QUEUED_PREVIEW",
                "log_id": log_id,
                "message": "已生成完整 Telegram 策略訊息預覽！設定 Bot Token 後即可自動發送至手機。",
                "preview": text,
            }

        send_url = f"https://api.telegram.org/bot{token}/sendMessage"
        err_desc = ""
        try:
            async with aiohttp.ClientSession() as session:
                # If chat_id is missing, try auto-discovering from getUpdates first
                if not chat_id:
                    chat_id = await self._auto_discover_telegram_chat_id(session, token) or ""

                if chat_id:
                    async with session.post(
                        send_url,
                        json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
                        timeout=10,
                    ) as resp:
                        resp_json = await resp.json()
                        if resp.status == 200 and resp_json.get("ok"):
                            return self._record_telegram_success(race_id, race_no, msg_type, text, now)
                        err_desc = str(resp_json.get("description") or f"HTTP {resp.status}")

                # If 'chat not found' or chat_id was empty, check getUpdates in case user just clicked /start
                if not chat_id or "chat not found" in err_desc.lower():
                    discovered_id = await self._auto_discover_telegram_chat_id(session, token)
                    if discovered_id and discovered_id != chat_id:
                        chat_id = discovered_id
                        async with session.post(
                            send_url,
                            json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
                            timeout=10,
                        ) as resp2:
                            resp2_json = await resp2.json()
                            if resp2.status == 200 and resp2_json.get("ok"):
                                return self._record_telegram_success(race_id, race_no, msg_type, text, now)
                            err_desc = str(resp2_json.get("description") or f"HTTP {resp2.status}")

                    # Fetch bot username via getMe to give exact activation instructions
                    bot_username = "您的Bot"
                    try:
                        async with session.get(f"https://api.telegram.org/bot{token}/getMe", timeout=6) as r_me:
                            me_json = await r_me.json()
                            if me_json.get("ok") and me_json.get("result", {}).get("username"):
                                bot_username = "@" + me_json["result"]["username"]
                    except Exception:
                        pass

                    err_desc = (
                        f"Bad Request: chat not found — 您的機器人 ({bot_username}) 尚未收到您的啟動指令！"
                        f"請先在 Telegram 搜尋並打開 {bot_username} ( https://t.me/{bot_username.lstrip('@')} )，"
                        f"點擊底部的「Start / 開始」(或傳送 /start 給它)，之後系統就能自動推播給您！"
                    )
        except Exception as exc:
            err_desc = str(exc)

        with self.db._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO hkjc_telegram_logs (
                    race_id, race_no, msg_type, status, message_text, error_detail, created_at
                ) VALUES (?, ?, ?, 'ERROR', ?, ?, ?)
                """,
                (race_id, race_no, msg_type, text, err_desc, now),
            )
            log_id = cur.lastrowid
        return {
            "sent": False,
            "status": "ERROR",
            "log_id": log_id,
            "message": f"⚠️ {err_desc}",
            "preview": text,
        }

    async def _auto_discover_telegram_chat_id(
        self, session: aiohttp.ClientSession, token: str
    ) -> Optional[str]:
        try:
            async with session.get(f"https://api.telegram.org/bot{token}/getUpdates", timeout=8) as r:
                d = await r.json()
                results = d.get("result") or []
                for upd in reversed(results):
                    msg = upd.get("message") or upd.get("channel_post") or upd.get("my_chat_member") or {}
                    chat = msg.get("chat") or {}
                    if chat.get("id"):
                        found_id = str(chat["id"])
                        self.update_telegram_config({"chat_id": found_id})
                        return found_id
        except Exception:
            pass
        return None

    def _record_telegram_success(
        self, race_id: str, race_no: int, msg_type: str, text: str, now: str
    ) -> Dict[str, Any]:
        with self.db._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO hkjc_telegram_logs (
                    race_id, race_no, msg_type, status, message_text, error_detail, created_at
                ) VALUES (?, ?, ?, 'SENT', ?, '', ?)
                """,
                (race_id, race_no, msg_type, text, now),
            )
            log_id = cur.lastrowid
        return {
            "sent": True,
            "status": "SENT",
            "log_id": log_id,
            "message": "✅ 已成功透過 Telegram Bot (@JL_Horsing_Bot) 發送至您的手機！",
            "preview": text,
        }

    async def push_race_strategy_to_telegram(self, race_id: Optional[str] = None) -> Dict[str, Any]:
        if race_id:
            race = next((r for r in self.races if r["race_id"] == race_id), None)
        else:
            race = next((r for r in self.races if r["status"] != "SETTLED"), None) or (
                self.races[-1] if self.races else None
            )
        if not race:
            raise ValueError("找不到指定賽事")

        msg_text = self.format_race_strategy_telegram_message(race)
        return await self.send_telegram_message(
            text=msg_text,
            race_id=race["race_id"],
            race_no=race["race_no"],
            msg_type="PRE_RACE_STRATEGY",
        )

    def get_state(self) -> Dict[str, Any]:
        cfg = self.get_config()
        model_state = self.get_model_state()
        initial_bk = float(cfg.get("initial_bankroll_hkd", 100000.0))

        with self.db._connect() as conn:
            bet_rows = conn.execute(
                "SELECT * FROM hkjc_paper_bets ORDER BY id DESC LIMIT 150"
            ).fetchall()
            bets = [dict(r) for r in bet_rows]

            evo_rows = conn.execute(
                "SELECT * FROM hkjc_model_evolutions ORDER BY id DESC LIMIT 40"
            ).fetchall()
            evolutions = []
            for er in evo_rows:
                d = dict(er)
                d["flaws_detected"] = json.loads(d.pop("flaws_detected_json", "[]"))
                d["weight_deltas"] = json.loads(d.pop("weight_deltas_json", "{}"))
                d["new_weights"] = json.loads(d.pop("new_weights_json", "{}"))
                evolutions.append(d)

            agg = conn.execute(
                """
                SELECT
                    COUNT(*) AS total_bets,
                    COALESCE(SUM(CASE WHEN status = 'OPEN' THEN 1 ELSE 0 END), 0) AS open_bets,
                    COALESCE(SUM(CASE WHEN status = 'WON' THEN 1 ELSE 0 END), 0) AS won_bets,
                    COALESCE(SUM(CASE WHEN status = 'LOST' THEN 1 ELSE 0 END), 0) AS lost_bets,
                    COALESCE(SUM(CASE WHEN status = 'OPEN' THEN stake_hkd ELSE 0.0 END), 0.0) AS open_stake_hkd,
                    COALESCE(SUM(CASE WHEN status IN ('WON', 'LOST') THEN stake_hkd ELSE 0.0 END), 0.0) AS settled_stake_hkd,
                    COALESCE(SUM(CASE WHEN status IN ('WON', 'LOST') THEN payout_hkd ELSE 0.0 END), 0.0) AS total_payout_hkd,
                    COALESCE(SUM(CASE WHEN status IN ('WON', 'LOST') THEN net_pnl_hkd ELSE 0.0 END), 0.0) AS realized_pnl_hkd
                FROM hkjc_paper_bets
                """
            ).fetchone()

            tg_rows = conn.execute(
                "SELECT * FROM hkjc_telegram_logs ORDER BY id DESC LIMIT 25"
            ).fetchall()
            telegram_logs = [dict(r) for r in tg_rows]

        open_stake = float(agg["open_stake_hkd"] or 0.0)
        settled_stake = float(agg["settled_stake_hkd"] or 0.0)
        realized_pnl = float(agg["realized_pnl_hkd"] or 0.0)
        won_bets = int(agg["won_bets"] or 0)
        lost_bets = int(agg["lost_bets"] or 0)
        settled_count = won_bets + lost_bets

        equity_hkd = round(initial_bk + realized_pnl, 2)
        cash_hkd = round(max(0.0, equity_hkd - open_stake), 2)
        roi_pct = round((realized_pnl / settled_stake) * 100.0, 2) if settled_stake > 0 else 0.0
        win_rate_pct = round((won_bets / settled_count) * 100.0, 1) if settled_count > 0 else 0.0

        cm = model_state["cumulative_metrics"]
        races_learned = max(1, int(model_state.get("races_learned", 0)))
        avg_ll_b2 = round(cm["benter2_log_loss_sum"] / races_learned, 4) if model_state.get("races_learned", 0) > 0 else 0.0
        avg_ll_b1 = round(cm["benter1_log_loss_sum"] / races_learned, 4) if model_state.get("races_learned", 0) > 0 else 0.0

        settled_races_count = sum(1 for r in self.races if r["status"] == "SETTLED")
        next_open_race = next((r for r in self.races if r["status"] != "SETTLED"), None)

        # Strip private internal keys (starting with '_') before serializing races to frontend
        open_bet_race_ids = {b["race_id"] for b in bets if b.get("status") == "OPEN"}
        late_win_mins = float(cfg.get("late_betting_window_minutes", 5.0))
        clean_races = []
        for r in self.races:
            mins = _compute_minutes_to_post(r.get("post_time_iso", ""))
            r["minutes_to_post"] = mins
            if r["status"] == "SETTLED":
                win_status = "SETTLED"
                win_label = "🏁 已對比官方賽果並完成雙時間尺度自進化"
            elif r["race_id"] in open_bet_race_ids or self._has_final_locked_push(r["race_id"]):
                win_status = "LOCKED_IN_WINDOW"
                win_label = "🔒 臨場 1–5 分鐘黃金窗口已鎖定最終策略（並已推播 TG）"
            elif not r.get("has_live_odds"):
                win_status = "PRE_POOL_OBSERVING"
                win_label = f"⏳ 待馬會開售彩池（距開跑 {_format_minutes_countdown(mins)} · 將於開跑前 1–5 分鐘自動鎖單並推播 TG）"
            elif mins is not None and mins > late_win_mins:
                win_status = "OBSERVING_UNTIL_T5"
                win_label = f"⏱️ 距開跑 {_format_minutes_countdown(mins)} · 暫定心水動態運算中（開跑前 1–5 分鐘自動鎖單並推播 TG）"
            else:
                win_status = "IN_T5_WINDOW"
                win_label = "🎯 已進入開跑前 1–5 分鐘黃金決策窗口（即時鎖定聰明錢與落飛）"

            rc = {k: v for k, v in r.items() if not k.startswith("_")}
            rc["has_official_result"] = bool(
                any(ru.get("_actual_final_pos") == 1 for ru in r.get("runners", []))
                and (r.get("hkjc_status") == "RESULT" or bool((r.get("_official_divs_raw") or {}).get("WIN")))
            )
            rc["execution_window_status"] = win_status
            rc["execution_window_label"] = win_label
            rc["runners"] = [
                {rk: rv for rk, rv in ru.items() if not rk.startswith("_")}
                for ru in r.get("runners", [])
            ]
            clean_races.append(rc)

        return {
            "meeting_info": self.meeting_info,
            "sync_status": {
                "source": self.sync_source,
                "last_sync_at": self.last_sync_at,
                "last_sync_ms": self.last_sync_ms,
            },
            "config": cfg,
            "model_state": model_state,
            "portfolio": {
                "initial_bankroll_hkd": initial_bk,
                "equity_hkd": equity_hkd,
                "cash_hkd": cash_hkd,
                "open_stake_hkd": round(open_stake, 2),
                "settled_stake_hkd": round(settled_stake, 2),
                "realized_pnl_hkd": round(realized_pnl, 2),
                "roi_pct": roi_pct,
                "win_rate_pct": win_rate_pct,
                "total_bets": int(agg["total_bets"] or 0),
                "open_bets": int(agg["open_bets"] or 0),
                "won_bets": won_bets,
                "lost_bets": lost_bets,
                "settled_races_count": settled_races_count,
                "total_races_count": len(clean_races),
                "avg_log_loss_benter2": avg_ll_b2,
                "avg_log_loss_benter1": avg_ll_b1,
                "top1_hits_benter2": cm.get("top1_hits_benter2", 0),
                "top1_hits_benter1": cm.get("top1_hits_benter1", 0),
                "top3_hits_benter2": cm.get("top3_hits_benter2", 0),
                "top3_hits_benter1": cm.get("top3_hits_benter1", 0),
            },
            "next_open_race_id": next_open_race["race_id"] if next_open_race else None,
            "races": clean_races,
            "paper_bets": bets,
            "evolutions": evolutions,
            "telegram": self.get_telegram_config(mask_token=True),
            "telegram_logs": telegram_logs,
        }
