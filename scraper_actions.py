"""
scraper_actions.py - HYPER ARROW美原 みんレポスクレイパー

方針
- マイナス差枚欠損対策のため、全台一覧で差枚が空の台は個別ページで補完する。
- 既存シートに取得済みの差枚がある台は再取得せず、不足分だけ補完する。
- 途中で一部取得に失敗しても保存し、次回の自動実行で未取得分を再挑戦する。
- 最近の掲載レポートも毎回確認し、遅れて公開された日付を自動補完する。
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timedelta
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import gspread
from google.oauth2.service_account import Credentials
from playwright.sync_api import sync_playwright


# ─────────────────────────────────────────────
# 設定
# ─────────────────────────────────────────────
TAG_URL = "https://min-repo.com/tag/hyper-arrow美原店/"
SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID", "").strip()
JST = ZoneInfo("Asia/Tokyo")

# 現在の美原は551台。台数変更時はGitHub Actionsのenvだけ変更可能。
EXPECTED_MACHINE_COUNT = int(os.environ.get("EXPECTED_MACHINE_COUNT", "551"))
BACKFILL_REPORTS = int(os.environ.get("BACKFILL_REPORTS", "6"))
INDIVIDUAL_RETRIES = int(os.environ.get("INDIVIDUAL_RETRIES", "2"))
INDIVIDUAL_WAIT_SECONDS = float(os.environ.get("INDIVIDUAL_WAIT_SECONDS", "0.7"))

USER_AGENT = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 "
    "Mobile/15E148 Safari/604.1"
)


# ─────────────────────────────────────────────
# 共通
# ─────────────────────────────────────────────
def now_jst():
    return datetime.now(JST)


def connect_sheets():
    if not SPREADSHEET_ID:
        raise RuntimeError("SPREADSHEET_ID が設定されていません")

    raw = os.environ.get("GCP_CREDENTIALS", "").strip()
    if not raw:
        raise RuntimeError("GCP_CREDENTIALS が設定されていません")

    try:
        creds_dict = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("GCP_CREDENTIALS が正しいJSONではありません") from exc

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    return gspread.authorize(creds).open_by_key(SPREADSHEET_ID)


def get_or_create_sheet(spreadsheet, name):
    try:
        return spreadsheet.worksheet(name)
    except gspread.WorksheetNotFound:
        return spreadsheet.add_worksheet(title=name, rows=1000, cols=10)


def to_num(text):
    """数値化できない値は空文字。0は0.0として保持する。"""
    if text is None:
        return ""
    s = str(text).strip()
    if s in {"", "-", "−", "ー", "—"}:
        return ""
    s = (
        s.replace(",", "")
        .replace("＋", "+")
        .replace("－", "-")
        .replace("−", "-")
        .replace("▲", "-")
        .replace("△", "-")
    )
    m = re.search(r"[+-]?\d+(?:\.\d+)?", s)
    return float(m.group()) if m else ""


def seat_number(value):
    n = to_num(value)
    if n == "":
        return None
    try:
        return int(n)
    except (TypeError, ValueError):
        return None


def goto_with_retry(page, url, timeout=90000, attempts=2):
    last_error = None
    for attempt in range(1, attempts + 1):
        try:
            return page.goto(url, wait_until="domcontentloaded", timeout=timeout)
        except Exception as exc:  # Playwright例外をまとめて再試行
            last_error = exc
            print(f"  ページ読込失敗 {attempt}/{attempts}: {url} -> {exc}")
            if attempt < attempts:
                time.sleep(3 * attempt)
    raise last_error


def new_page(browser):
    return browser.new_page(user_agent=USER_AGENT, locale="ja-JP")


# ─────────────────────────────────────────────
# レポート一覧
# ─────────────────────────────────────────────
def _report_links_from_page(page):
    """サイト構造変更に少し耐えられるよう複数セレクタを試す。"""
    for sel in ["div.table_wrap a", "table a", "article a", "main a", "a"]:
        found = page.query_selector_all(sel)
        candidates = []
        for link in found:
            try:
                text = (link.inner_text() or "").strip()
            except Exception:
                continue
            if re.search(r"\d{1,2}/\d{1,2}\s*\(", text):
                candidates.append(link)
        if candidates:
            if sel != "div.table_wrap a":
                print(f"⚠️ レポート一覧のセレクタ変更を検知: {sel}")
            return candidates
    return []


def _parse_report_links(links, reference_time):
    reports = []
    seen = set()

    for link in links:
        try:
            text = (link.inner_text() or "").strip()
            href = (link.get_attribute("href") or "").strip()
        except Exception:
            continue

        m = re.search(r"(?:(\d{4})/)?(\d{1,2})/(\d{1,2})\s*\(", text)
        if not m or not href:
            continue

        year = int(m.group(1)) if m.group(1) else reference_time.year
        month = int(m.group(2))
        day = int(m.group(3))

        try:
            date_obj = datetime(year, month, day)
        except ValueError:
            continue

        # 年表記がなく、現在より大きく未来なら前年扱い。
        if not m.group(1) and date_obj > reference_time.replace(tzinfo=None) + timedelta(days=2):
            try:
                date_obj = datetime(year - 1, month, day)
            except ValueError:
                continue

        date_str = date_obj.strftime("%Y-%m-%d")
        url = urljoin(TAG_URL, href)
        key = (date_str, url)
        if key in seen:
            continue
        seen.add(key)
        reports.append((date_str, url))

    # HTML順に依存せず新しい順にする。同日複数URLなら最初の1つだけ採用。
    reports.sort(key=lambda x: x[0], reverse=True)
    unique_dates = []
    used_dates = set()
    for item in reports:
        if item[0] in used_dates:
            continue
        used_dates.add(item[0])
        unique_dates.append(item)
    return unique_dates


def fetch_recent_reports(max_n=10):
    """タグページから最近の掲載レポートを [(日付, URL), ...] で返す。"""
    reference_time = now_jst()
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"],
        )
        page = new_page(browser)
        try:
            print("タグページから掲載レポートを確認中...")
            goto_with_retry(page, TAG_URL, timeout=90000, attempts=2)
            try:
                page.wait_for_selector("div.table_wrap a", timeout=15000)
            except Exception:
                pass
            time.sleep(2)

            links = _report_links_from_page(page)
            if not links:
                try:
                    print(f"❌ レポートリンクなし / title={page.title()!r}")
                    print(f"[本文冒頭] {(page.inner_text('body') or '')[:250]!r}")
                except Exception:
                    pass
                return []

            reports = _parse_report_links(links, reference_time)
            reports = reports[:max_n]
            print(f"掲載レポート: {[d for d, _ in reports]}")
            return reports
        finally:
            browser.close()


# ─────────────────────────────────────────────
# 既存Google Sheetsの状態
# ─────────────────────────────────────────────
def load_existing_sheet(spreadsheet, date_str):
    """
    既存日付シートを内部形式に変換。
    戻り値: (worksheet or None, {台番: row}, summary)
    """
    try:
        ws = spreadsheet.worksheet(date_str)
    except gspread.WorksheetNotFound:
        return None, {}, {"exists": False, "rows": 0, "missing_diff": 0, "complete": False}

    try:
        values = ws.get_all_values()
    except Exception as exc:
        print(f"⚠️ {date_str} 既存シート読込失敗: {exc}")
        return ws, {}, {"exists": True, "rows": 0, "missing_diff": 0, "complete": False}

    if len(values) < 2:
        return ws, {}, {"exists": True, "rows": 0, "missing_diff": 0, "complete": False}

    headers = [str(x).strip() for x in values[0]]
    index = {name: i for i, name in enumerate(headers)}
    seat_idx = index.get("台番")
    if seat_idx is None:
        return ws, {}, {"exists": True, "rows": 0, "missing_diff": 0, "complete": False}

    def cell(row, key):
        idx = index.get(key)
        return row[idx] if idx is not None and idx < len(row) else ""

    rows_by_seat = {}
    for row in values[1:]:
        num = seat_number(row[seat_idx] if seat_idx < len(row) else "")
        if not num:
            continue
        rows_by_seat[num] = {
            "機種": cell(row, "機種名"),
            "台番": float(num),
            "差枚": to_num(cell(row, "差枚")),
            "G数": to_num(cell(row, "G数")),
            "出率": cell(row, "出率"),
        }

    missing_diff = sum(1 for r in rows_by_seat.values() if r.get("差枚") == "")
    row_count = len(rows_by_seat)
    complete = row_count >= EXPECTED_MACHINE_COUNT and missing_diff == 0
    summary = {
        "exists": True,
        "rows": row_count,
        "missing_diff": missing_diff,
        "complete": complete,
    }
    return ws, rows_by_seat, summary


def merge_existing(data_rows, existing_by_seat):
    """同じ日付の既存値を使い、再取得不要な差枚を保持する。"""
    for row in data_rows:
        num = seat_number(row.get("台番"))
        if not num:
            continue
        old = existing_by_seat.get(num)
        if not old:
            continue
        for key in ["機種", "差枚", "G数", "出率"]:
            if row.get(key, "") in ("", None) and old.get(key, "") not in ("", None):
                row[key] = old[key]
    return data_rows


# ─────────────────────────────────────────────
# レポート本体
# ─────────────────────────────────────────────
def parse_all_machine_table(page):
    table = page.query_selector("div.table_wrap table")
    if not table:
        print("❌ 全機種テーブルが見つかりません")
        return []

    rows = table.query_selector_all("tr")
    if not rows:
        return []

    headers = [c.inner_text().strip() for c in rows[0].query_selector_all("th, td")]
    print(f"ヘッダー: {headers}")

    col = {}
    for i, header in enumerate(headers):
        h = header.lower()
        if "機種" in h or "name" in h:
            col["機種"] = i
        elif "台番" in h or "台no" in h:
            col["台番"] = i
        elif "差枚" in h or "diff" in h:
            col["差枚"] = i
        elif "g数" in h or "回転" in h or "game" in h:
            col["G数"] = i
        elif "出率" in h or "rate" in h:
            col["出率"] = i

    if "台番" not in col:
        print(f"❌ 台番列が判定できません: {col}")
        return []

    print(f"列マッピング: {col}")
    result = []
    seen_seats = set()

    for tr in rows[1:]:
        cells = tr.query_selector_all("td")
        if not cells:
            continue
        texts = [c.inner_text().strip() for c in cells]

        item = {"機種": "", "台番": "", "差枚": "", "G数": "", "出率": ""}
        for key, idx in col.items():
            if idx >= len(texts):
                continue
            value = texts[idx]
            item[key] = to_num(value) if key in {"台番", "差枚", "G数"} else value

        num = seat_number(item.get("台番"))
        if not num or num in seen_seats:
            continue
        seen_seats.add(num)
        item["台番"] = float(num)
        result.append(item)

    return result


def extract_individual_diff(page):
    """現在動いている個別取得方式を維持し、差枚だけ安全に抜く。"""
    # 方法1: samai_cell
    for cell in page.query_selector_all("td.samai_cell"):
        try:
            text = cell.inner_text().strip()
        except Exception:
            continue
        value = to_num(text)
        if value != "":
            return value

    # 方法2: 「差枚」ラベルの直後セル
    cells = page.query_selector_all("td, th")
    for i, cell in enumerate(cells):
        try:
            text = cell.inner_text().strip()
        except Exception:
            continue
        if "差枚" not in text:
            continue
        if i + 1 < len(cells):
            try:
                value = to_num(cells[i + 1].inner_text().strip())
            except Exception:
                value = ""
            if value != "":
                return value

    return ""


def fetch_individual_diff(page, base_url, num):
    url = f"{base_url}/?num={num}"
    last_error = None

    for attempt in range(1, INDIVIDUAL_RETRIES + 1):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=35000)
            try:
                page.wait_for_function(
                    """() => {
                        const cells = document.querySelectorAll('td.samai_cell');
                        return [...cells].some(c => /[0-9]/.test((c.innerText || '').trim()));
                    }""",
                    timeout=5000,
                )
            except Exception:
                # 数値0や構造差でも下の抽出処理で拾える可能性があるため続行
                pass

            value = extract_individual_diff(page)
            if value != "":
                return value

            last_error = "差枚セルを取得できませんでした"
        except Exception as exc:
            last_error = exc

        if attempt < INDIVIDUAL_RETRIES:
            time.sleep(1.5 * attempt)

    print(f"  ⚠️ 台番{num}: 個別取得失敗 ({last_error})")
    return ""


def save_rows(spreadsheet, date_str, date_disp, rows):
    ws = get_or_create_sheet(spreadsheet, date_str)
    now_str = now_jst().strftime("%Y-%m-%d %H:%M")

    values = [["取得日時", "対象日付", "機種名", "台番", "差枚", "G数", "出率"]]
    for row in sorted(rows, key=lambda r: seat_number(r.get("台番")) or 0):
        values.append(
            [
                now_str,
                date_disp,
                str(row.get("機種", "")),
                row.get("台番", ""),
                row.get("差枚", ""),
                row.get("G数", ""),
                row.get("出率", ""),
            ]
        )

    # 先に新データを書き、その後に古い余剰行だけ消す。
    # clear()→失敗でシートが空になる事故を避ける。
    old_row_count = max(len(ws.get_all_values()), 1)
    ws.update(values=values, range_name=f"A1:G{len(values)}", value_input_option="RAW")
    if old_row_count > len(values):
        ws.batch_clear([f"A{len(values) + 1}:G{old_row_count}"])

    print(f"✅ {date_str} シートに {len(rows)} 台を保存")


def scrape_report(spreadsheet, date_str, report_url, force=False):
    """
    1レポートを取得。
    戻り値: complete / partial / failed
    """
    date_disp = datetime.strptime(date_str, "%Y-%m-%d").strftime("%Y/%m/%d")

    _, existing_by_seat, summary = load_existing_sheet(spreadsheet, date_str)
    if summary["exists"]:
        print(
            f"既存 {date_str}: {summary['rows']}台 / "
            f"差枚未取得 {summary['missing_diff']}台"
        )
    if summary["complete"] and not force:
        print(f"✅ {date_str} は完全取得済みのためスキップ")
        return "complete"

    print(f"\n=== {date_str} 取得開始 ===")
    print(f"レポートURL: {report_url}")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"],
        )
        page = new_page(browser)
        try:
            base_url = report_url.rstrip("/").split("?")[0]
            all_url = base_url + "/?kishu=all"
            print(f"全機種URL: {all_url}")
            goto_with_retry(page, all_url, timeout=90000, attempts=2)

            print("差枚データの読み込み待ち...")
            try:
                page.wait_for_function(
                    """() => {
                        const cells = document.querySelectorAll('td.samai_cell');
                        if (cells.length < 10) return false;
                        return [...cells].some(c => /[0-9]/.test((c.innerText || '').trim()));
                    }""",
                    timeout=30000,
                )
            except Exception as exc:
                print(f"⚠️ 差枚待機タイムアウト（テーブル解析は続行）: {exc}")
            time.sleep(1.5)

            data_rows = parse_all_machine_table(page)
            print(f"一覧から取得: {len(data_rows)}台")

            if len(data_rows) < EXPECTED_MACHINE_COUNT:
                print(
                    f"❌ 台数不足のため既存シートを上書きしません: "
                    f"{len(data_rows)}/{EXPECTED_MACHINE_COUNT}台"
                )
                return "failed"

            # 既存の同日データをマージ。すでに個別取得できたマイナス差枚などを保持する。
            merge_existing(data_rows, existing_by_seat)

            unknown_rows = [r for r in data_rows if r.get("差枚") == ""]
            print(f"個別補完対象: {len(unknown_rows)}台")

            success_count = 0
            fail_count = 0
            for index, row in enumerate(unknown_rows, start=1):
                num = seat_number(row.get("台番"))
                if not num:
                    fail_count += 1
                    continue

                value = fetch_individual_diff(page, base_url, num)
                if value != "":
                    row["差枚"] = value
                    success_count += 1
                    if success_count <= 10 or index % 50 == 0:
                        print(f"  補完 {index}/{len(unknown_rows)}: 台番{num} 差枚={value}")
                else:
                    fail_count += 1

                # 連続アクセスを少し抑える。取得済み台には待機しない。
                if INDIVIDUAL_WAIT_SECONDS > 0:
                    time.sleep(INDIVIDUAL_WAIT_SECONDS)

            if unknown_rows:
                print(f"個別補完: 成功{success_count}台 / 未取得{fail_count}台")

        finally:
            browser.close()

    plus_n = sum(1 for r in data_rows if isinstance(r.get("差枚"), float) and r["差枚"] > 0)
    minus_n = sum(1 for r in data_rows if isinstance(r.get("差枚"), float) and r["差枚"] < 0)
    missing_n = sum(1 for r in data_rows if r.get("差枚") == "")
    print(f"差枚集計: プラス{plus_n} / マイナス{minus_n} / 未取得{missing_n}")

    save_rows(spreadsheet, date_str, date_disp, data_rows)
    return "complete" if missing_n == 0 else "partial"


def scrape_with_retry(spreadsheet, date_str, report_url, max_retries=2):
    """失敗/部分取得時に再試行。既存値を残すので2回目は不足台中心になる。"""
    last = "failed"
    for attempt in range(1, max_retries + 1):
        print(f"\n=== 試行 {attempt}/{max_retries}: {date_str} ===")
        try:
            last = scrape_report(spreadsheet, date_str, report_url)
        except Exception as exc:
            print(f"⚠️ {date_str} 試行{attempt}エラー: {exc}")
            last = "failed"

        if last == "complete":
            return last

        if attempt < max_retries:
            wait = 15 * attempt
            print(f"{date_str} は {last}。{wait}秒後に再試行します")
            time.sleep(wait)

    return last


# ─────────────────────────────────────────────
# 自動補完
# ─────────────────────────────────────────────
def backfill_recent(spreadsheet, reports, max_reports=None, exclude_dates=None):
    """
    最近の掲載済みレポートを確認。
    - シート自体がない日
    - 台数が不足している日
    - 差枚が空の台が残っている日
    だけ再取得する。
    """
    max_reports = max_reports or BACKFILL_REPORTS
    exclude_dates = set(exclude_dates or [])
    candidates = reports[:max_reports]

    checked = 0
    completed = 0
    partial = 0
    failed = 0

    print("\n=== 最近の不足データ自動チェック ===")
    for date_str, report_url in candidates:
        if date_str in exclude_dates:
            continue

        _, _, summary = load_existing_sheet(spreadsheet, date_str)
        if summary["complete"]:
            print(f"✅ {date_str}: 完全取得済み")
            continue

        reason = (
            "シートなし"
            if not summary["exists"]
            else f"{summary['rows']}台 / 差枚未取得{summary['missing_diff']}台"
        )
        print(f"🔄 {date_str}: 補完対象 ({reason})")
        checked += 1

        result = scrape_with_retry(spreadsheet, date_str, report_url, max_retries=2)
        if result == "complete":
            completed += 1
        elif result == "partial":
            partial += 1
        else:
            failed += 1

        time.sleep(2)

    print(
        f"補完チェック完了: 対象{checked}日 / "
        f"完全化{completed} / 部分取得{partial} / 失敗{failed}"
    )
    return {"checked": checked, "complete": completed, "partial": partial, "failed": failed}


# ─────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────
def find_report(reports, date_str):
    return next(((d, u) for d, u in reports if d == date_str), None)


def main():
    arg = sys.argv[1].strip() if len(sys.argv) > 1 else "normal"
    print(f"=== 起動: {now_jst().strftime('%Y-%m-%d %H:%M:%S')} JST ===")
    print(
        f"設定: 期待台数={EXPECTED_MACHINE_COUNT}, "
        f"補完対象={BACKFILL_REPORTS}レポート, 個別再試行={INDIVIDUAL_RETRIES}回"
    )

    spreadsheet = connect_sheets()

    # 手動の日付指定も従来どおり `python scraper_actions.py 2026-09-17` で使える。
    is_date = bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", arg))
    report_limit = 30 if is_date else max(BACKFILL_REPORTS + 4, 10)
    reports = fetch_recent_reports(max_n=report_limit)
    if not reports:
        print("❌ 掲載レポート一覧を取得できませんでした")
        return 1

    if arg == "backfill":
        result = backfill_recent(spreadsheet, reports, max_reports=BACKFILL_REPORTS)
        # 対象が全部失敗した場合だけActionsを失敗扱いにする。
        if result["checked"] > 0 and result["failed"] == result["checked"]:
            return 1
        return 0

    if is_date:
        hit = find_report(reports, arg)
        if not hit:
            print(f"⏭ {arg} のレポートは現在のタグページに見つかりません")
            return 0
        result = scrape_with_retry(spreadsheet, hit[0], hit[1], max_retries=3)
        return 0 if result in {"complete", "partial"} else 1

    # normal: 最新掲載レポートを取得/補完し、その後に最近の不足も確認。
    latest_date, latest_url = reports[0]
    print(f"最新掲載レポート: {latest_date}")
    latest_result = scrape_with_retry(spreadsheet, latest_date, latest_url, max_retries=2)

    backfill_recent(
        spreadsheet,
        reports,
        max_reports=BACKFILL_REPORTS,
        exclude_dates={latest_date},
    )

    # partialはデータを保存できており、次回に不足分を再取得するので正常終了とする。
    return 0 if latest_result in {"complete", "partial"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
