"""
scraper_actions.py - HYPER ARROW美原 みんレポスクレイパー
Playwright使用でJS描画後のデータ（マイナス差枚含む）を取得
Google Sheetsに日付別シートで蓄積保存
"""

import os, re, json, time
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime, timedelta
from playwright.sync_api import sync_playwright

# ─── 設定 ───
TAG_URL = "https://min-repo.com/tag/hyper-arrow美原店/"
SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID", "")

# ─── Google Sheets接続 ───
def connect_sheets():
    creds_dict = json.loads(os.environ.get("GCP_CREDENTIALS", "{}"))
    scopes = ["https://www.googleapis.com/auth/spreadsheets",
              "https://www.googleapis.com/auth/drive"]
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    client = gspread.authorize(creds)
    return client.open_by_key(SPREADSHEET_ID)

def get_or_create_sheet(spreadsheet, name):
    try:
        return spreadsheet.worksheet(name)
    except gspread.WorksheetNotFound:
        return spreadsheet.add_worksheet(title=name, rows=1000, cols=10)

# ─── 数値パース ───
def to_num(text):
    if not text: return ""
    s = str(text).strip()
    if s in ["-", "−", "ー", ""]: return ""
    s = s.replace(",", "").replace("＋", "+").replace("－", "-").replace("−", "-")
    m = re.search(r"[+-]?\d+\.?\d*", s)
    return float(m.group()) if m else ""

# ─── メイン処理 ───
def scrape_and_save(target_date=None):
    jst = datetime.utcnow() + timedelta(hours=9)
    target = datetime.strptime(target_date, "%Y-%m-%d") if target_date else jst - timedelta(days=1)
    date_str = target.strftime("%Y-%m-%d")
    date_disp = target.strftime("%Y/%m/%d")

    print(f"=== 開始: {jst.strftime('%Y-%m-%d %H:%M:%S')} JST ===")
    print(f"対象日付: {date_str}")

    spreadsheet = connect_sheets()

    data_rows = []

    with sync_playwright() as p:
        print("ブラウザ起動中...")
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
        )
        page = browser.new_page(
            user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1",
            locale="ja-JP"
        )

        # ① タグページ→レポート一覧から対象日付のリンクを選ぶ
        # ※美原は毎日掲載ではないため、リンクテキストの日付（例: 7/5(日)）を解析して選択する
        print(f"タグページアクセス中...")
        page.goto(TAG_URL, wait_until="domcontentloaded", timeout=90000)
        time.sleep(5)

        # JS遅延読み込みに備えて待機
        try:
            page.wait_for_selector("div.table_wrap a", timeout=20000)
        except Exception:
            pass

        # 複数セレクタでリンクを探す（サイト構造変更に対応）
        links = []
        for sel in ["div.table_wrap a", "table a", "article a", "main a", "a"]:
            found = page.query_selector_all(sel)
            # 日付らしきテキストを含むリンクだけに絞る
            cand = []
            for l in found:
                t = (l.inner_text() or "").strip()
                if re.search(r"\d{1,2}/\d{1,2}\s*\(", t):
                    cand.append(l)
            if cand:
                links = cand
                if sel != "div.table_wrap a":
                    print(f"⚠️ セレクタ変更を検知: {sel} で{len(cand)}件取得")
                break

        if not links:
            # 診断情報を出力
            try:
                title = page.title()
                body_text = (page.inner_text("body") or "")[:300]
                print(f"❌ レポートリンクが見つかりません")
                print(f"[診断] ページタイトル: {title}")
                print(f"[診断] 本文冒頭: {body_text}")
            except Exception as de:
                print(f"❌ レポートリンクが見つかりません（診断も失敗: {de}）")
            browser.close()
            return False

        reports = []  # [(date_str, url)]
        for l in links:
            text = (l.inner_text() or "").strip()
            m = re.search(r"(?:(\d{4})/)?(\d{1,2})/(\d{1,2})\s*\(", text)
            if not m:
                continue
            yy = int(m.group(1)) if m.group(1) else jst.year
            mm, dd = int(m.group(2)), int(m.group(3))
            try:
                d = datetime(yy, mm, dd)
            except ValueError:
                continue
            # 年表記なしで未来日付になる場合は前年（年跨ぎ対応）
            if not m.group(1) and d > jst + timedelta(days=2):
                d = datetime(yy - 1, mm, dd)
            href = l.get_attribute("href") or ""
            if href.startswith("/"):
                href = "https://min-repo.com" + href
            if href:
                reports.append((d.strftime("%Y-%m-%d"), href))

        if not reports:
            print("❌ 日付付きレポートが見つかりません")
            browser.close()
            return False

        print(f"掲載レポート（新しい順）: {[r[0] for r in reports[:6]]}")

        if target_date:
            hit = [r for r in reports if r[0] == date_str]
            if not hit:
                print(f"⏭ {date_str} のレポートは掲載されていません（美原は毎日掲載ではありません）")
                browser.close()
                return False
            report_url = hit[0][1]
        else:
            # 最新レポートの実際の日付を採用（「昨日」とは限らない）
            date_str = reports[0][0]
            date_disp = datetime.strptime(date_str, "%Y-%m-%d").strftime("%Y/%m/%d")
            report_url = reports[0][1]
            print(f"最新レポート日付: {date_str}")

        # 既に取得済み（データ入り）ならスキップ
        try:
            existing_titles = [ws.title for ws in spreadsheet.worksheets()]
            if date_str in existing_titles:
                ws0 = spreadsheet.worksheet(date_str)
                if len(ws0.get_all_values()) >= 2:
                    print(f"✅ {date_str} は取得済みのためスキップ")
                    browser.close()
                    return True
        except Exception:
            pass

        print(f"レポートURL: {report_url}")

        # ② 全機種ページ
        base = report_url.rstrip("/").split("?")[0]
        kishu_url = base + "/?kishu=all"
        print(f"全機種URL: {kishu_url}")

        page.goto(kishu_url, wait_until="domcontentloaded", timeout=90000)
        
        # 差枚セルに数値が入るまで最大30秒待つ
        print("差枚データの読み込み待ち...")
        try:
            # samai_cellに数値が入るまで待機
            page.wait_for_function(
                """() => {
                    const cells = document.querySelectorAll('td.samai_cell');
                    if (cells.length < 10) return false;
                    // 数値が入っているセルが1つでもあればOK
                    for (let c of cells) {
                        const t = c.innerText.trim();
                        if (t && t !== '-' && /[0-9]/.test(t)) return true;
                    }
                    return false;
                }""",
                timeout=30000
            )
            print("差枚データ読み込み完了")
        except Exception as e:
            print(f"待機タイムアウト（続行）: {e}")
        
        time.sleep(2)

        # デバッグ：差枚セルの中身を確認
        sample_cells = page.query_selector_all("td.samai_cell")
        print(f"samai_cellの数: {len(sample_cells)}")
        if sample_cells:
            samples = [c.inner_text().strip() for c in sample_cells[:10]]
            print(f"最初の10個: {samples}")
            # マイナスらしいものを探す
            for c in sample_cells:
                t = c.inner_text().strip()
                if t and t != "-" and t != "0":
                    html = c.inner_html()
                    if "-" in t or "-" in html:
                        print(f"マイナス候補: text={repr(t)} html={html[:200]}")
                        break

        # ③ テーブル取得
        table = page.query_selector("div.table_wrap table")
        if not table:
            print("❌ テーブルが見つかりません")
            browser.close()
            return False

        rows = table.query_selector_all("tr")
        print(f"行数: {len(rows)}")

        # ④ ヘッダー解析
        headers = [c.inner_text().strip() for c in rows[0].query_selector_all("th, td")]
        print(f"ヘッダー: {headers}")

        col = {}
        for i, h in enumerate(headers):
            hl = h.lower()
            if "機種" in hl or "name" in hl: col["機種"] = i
            elif "台番" in hl or "台no" in hl: col["台番"] = i
            elif "差枚" in hl or "diff" in hl: col["差枚"] = i
            elif "g数" in hl or "回転" in hl or "game" in hl: col["G数"] = i
            elif "出率" in hl or "rate" in hl: col["出率"] = i
        print(f"列マッピング: {col}")

        # ⑤ データ行取得
        for row in rows[1:]:
            cells = row.query_selector_all("td")
            if not cells: continue
            texts = [c.inner_text().strip() for c in cells]

            row_data = {}
            for key, idx in col.items():
                if idx < len(texts):
                    val = texts[idx]
                    row_data[key] = to_num(val) if key in ["差枚", "G数", "台番"] else val
                else:
                    row_data[key] = ""
            data_rows.append(row_data)

        # ⑥ 不明台（差枚が空）を個別ページで補完
        unknown_rows = [r for r in data_rows if r.get("差枚") == ""]
        print(f"不明台数: {len(unknown_rows)}台 → 個別ページで補完します")

        if unknown_rows:
            base_url = report_url.rstrip("/").split("?")[0]
            补完_count = 0
            error_count = 0

            for r in unknown_rows:
                台番 = r.get("台番", "")
                if not 台番:
                    continue
                num = int(台番) if isinstance(台番, float) else int(str(台番).strip())
                indiv_url = f"{base_url}/?num={num}"

                try:
                    page.goto(indiv_url, wait_until="domcontentloaded", timeout=30000)
                    time.sleep(1)

                    # 差枚を取得（個別ページのHTML構造に合わせて取得）
                    # まずテーブルから探す
                    diff_val = None

                    # 方法1: samai_cellクラスから数値を探す
                    cells = page.query_selector_all("td.samai_cell")
                    for cell in cells:
                        txt = cell.inner_text().strip()
                        if txt and txt != "-" and re.search(r"[0-9]", txt):
                            parsed = to_num(txt)
                            if isinstance(parsed, float):
                                diff_val = parsed
                                break

                    # 方法2: 差枚というラベルの隣のセルを探す
                    if diff_val is None:
                        all_cells = page.query_selector_all("td, th")
                        for i, cell in enumerate(all_cells):
                            txt = cell.inner_text().strip()
                            if "差枚" in txt:
                                # 次のセルを取得
                                try:
                                    next_cells = page.query_selector_all("td, th")
                                    if i + 1 < len(next_cells):
                                        next_txt = next_cells[i+1].inner_text().strip()
                                        parsed = to_num(next_txt)
                                        if isinstance(parsed, float):
                                            diff_val = parsed
                                            break
                                except:
                                    pass

                    if diff_val is not None:
                        r["差枚"] = diff_val
                        补完_count += 1
                        if 补完_count <= 5:  # 最初の5台をログ表示
                            print(f"  補完: 台番{num} 差枚={diff_val}")
                    else:
                        error_count += 1
                        if error_count <= 3:
                            # ページ内容を確認
                            page_text = page.inner_text("body")[:300]
                            print(f"  取得失敗: 台番{num} ページ内容={repr(page_text[:100])}")

                except Exception as e:
                    error_count += 1
                    if error_count <= 3:
                        print(f"  エラー: 台番{num} {e}")

            print(f"補完完了: {补完_count}台成功 / {error_count}台失敗")

        browser.close()

    # ⑦ 集計ログ
    print(f"取得行数: {len(data_rows)}")
    plus_n = sum(1 for r in data_rows if isinstance(r.get("差枚"), float) and r["差枚"] > 0)
    minus_n = sum(1 for r in data_rows if isinstance(r.get("差枚"), float) and r["差枚"] < 0)
    zero_n = sum(1 for r in data_rows if r.get("差枚") == "")
    print(f"差枚: プラス{plus_n}台 / マイナス{minus_n}台 / 不明{zero_n}台")

    if not data_rows:
        print("❌ データが空です")
        return False

    # ⑨ Google Sheetsへ書き込み
    sheet = get_or_create_sheet(spreadsheet, date_str)
    sheet.clear()

    now_str = jst.strftime("%Y-%m-%d %H:%M")
    sheet.append_row(["取得日時", "対象日付", "機種名", "台番", "差枚", "G数", "出率"])

    batch = []
    for r in data_rows:
        batch.append([
            now_str, date_disp,
            str(r.get("機種", "")),
            r.get("台番", ""),
            r.get("差枚", ""),
            r.get("G数", ""),
            r.get("出率", ""),
        ])

    for i in range(0, len(batch), 100):
        sheet.append_rows(batch[i:i+100])
        print(f"書き込み中... {min(i+100, len(batch))}/{len(batch)}")
        time.sleep(1)

    print(f"✅ {date_str} シートに {len(batch)} 行を保存しました")

    all_date_sheets = sorted([
        ws.title for ws in spreadsheet.worksheets()
        if re.match(r"\d{4}-\d{2}-\d{2}", ws.title)
    ])
    print(f"蓄積: {all_date_sheets} ({len(all_date_sheets)}日分)")
    return True


def get_recent_report_dates(max_n=8):
    """タグページから最近の掲載レポート日付リストを取得（新しい順）"""
    dates = []
    jst = datetime.utcnow() + timedelta(hours=9)
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
        )
        page = browser.new_page(
            user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1",
            locale="ja-JP"
        )
        try:
            page.goto(TAG_URL, wait_until="domcontentloaded", timeout=90000)
            time.sleep(5)
            links = page.query_selector_all("div.table_wrap a")
            for l in links:
                text = (l.inner_text() or "").strip()
                m = re.search(r"(?:(\d{4})/)?(\d{1,2})/(\d{1,2})\s*\(", text)
                if not m:
                    continue
                yy = int(m.group(1)) if m.group(1) else jst.year
                mm, dd = int(m.group(2)), int(m.group(3))
                try:
                    d = datetime(yy, mm, dd)
                except ValueError:
                    continue
                if not m.group(1) and d > jst + timedelta(days=2):
                    d = datetime(yy - 1, mm, dd)
                ds = d.strftime("%Y-%m-%d")
                if ds not in dates:
                    dates.append(ds)
                if len(dates) >= max_n:
                    break
        except Exception as e:
            print(f"レポート一覧取得エラー: {e}")
        finally:
            browser.close()
    return dates


def get_existing_dates(spreadsheet, only_dates=None):
    """データが入っている日付シートの一覧（only_dates指定時はその日付だけ確認しquota節約）"""
    existing = set()
    for ws in spreadsheet.worksheets():
        title = ws.title
        if not re.match(r"\d{4}-\d{2}-\d{2}", title):
            continue
        if only_dates is not None and title not in only_dates:
            continue
        try:
            if len(ws.get_all_values()) >= 2:
                existing.add(title)
            time.sleep(1)  # API制限対策
        except Exception:
            pass
    return existing


def scrape_with_retry(target_date=None, max_retries=3):
    """リトライ付きスクレイピング"""
    for attempt in range(1, max_retries + 1):
        print(f"=== 試行 {attempt}/{max_retries} ===")
        try:
            ok = scrape_and_save(target_date=target_date)
            if ok:
                return True
            print(f"⚠️ 試行{attempt}失敗")
        except Exception as e:
            print(f"⚠️ 試行{attempt}エラー: {e}")

        if attempt < max_retries:
            wait = 30 * attempt
            print(f"{wait}秒待機してリトライ...")
            time.sleep(wait)

    print(f"❌ {max_retries}回試行しても失敗")
    return False


def backfill_recent(spreadsheet, max_reports=8):
    """掲載済みレポートのうち未取得のものを補完"""
    report_dates = get_recent_report_dates(max_n=max_reports)
    print(f"掲載レポート日付: {report_dates}")
    if not report_dates:
        print("レポート一覧が取得できませんでした")
        return 0
    existing = get_existing_dates(spreadsheet, only_dates=set(report_dates))
    missing = [d for d in report_dates if d not in existing]
    print(f"未取得: {missing}")
    if not missing:
        print("✅ 掲載済みレポートはすべて取得済みです")
        return 0
    success = 0
    for d in missing:
        print(f"\n--- {d} を取得 ---")
        if scrape_with_retry(target_date=d, max_retries=2):
            success += 1
        time.sleep(5)
    print(f"\n補完完了: {success}/{len(missing)}件")
    return success


if __name__ == "__main__":
    import sys

    mode = sys.argv[1] if len(sys.argv) > 1 else "normal"
    spreadsheet = connect_sheets()

    if mode == "backfill":
        # 欠損補完モード：掲載済みレポートのうち未取得を取得
        print("=== 欠損補完モード ===")
        backfill_recent(spreadsheet, max_reports=8)
        exit(0)
    else:
        # 通常モード：最新レポート取得 → 未取得レポートを補完
        target = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] != "normal" else None
        if target:
            print(f"日付指定: {target}")
            ok = scrape_with_retry(target_date=target, max_retries=3)
        else:
            ok = scrape_with_retry(target_date=None, max_retries=3)
            print("\n=== 未取得レポートの自動チェック ===")
            backfill_recent(spreadsheet, max_reports=6)
        exit(0 if ok else 1)
