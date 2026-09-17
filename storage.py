import hashlib,json,re
from datetime import datetime
from zoneinfo import ZoneInfo
import gspread
import pandas as pd
from data import normalize,COLS
SCOPES=['https://www.googleapis.com/auth/spreadsheets']

def spreadsheet(credentials,sheet_id):
    return gspread.service_account_from_dict(dict(credentials),scopes=SCOPES).open_by_key(sheet_id)

def read_history(book,days=14):
    today=datetime.now(ZoneInfo('Asia/Tokyo')).date().isoformat()
    dates=sorted([w.title for w in book.worksheets() if re.fullmatch(r'\d{4}-\d{2}-\d{2}',w.title) and w.title<today],reverse=True)[:days]
    if not dates:return {},[]
    ranges=[f"'{d}'!A1:Z3000" for d in dates]
    payload=book.values_batch_get(ranges,params={'valueRenderOption':'UNFORMATTED_VALUE'})
    history={};notes=[]
    for date,item in zip(dates,payload.get('valueRanges',[])):
        vals=item.get('values',[])
        if len(vals)<2:notes.append(f'{date}: 空のシート');history[date]=pd.DataFrame(columns=COLS);continue
        width=len(vals[0]);raw=pd.DataFrame([(r+['']*width)[:width] for r in vals[1:]],columns=vals[0])
        daily,warnings=normalize(raw);history[date]=daily;notes.extend(f'{date} {s}' for s in warnings)
    if len(history)!=len(dates):raise ValueError('一部の日付を読み込めませんでした。再読込してください。')
    return history,notes

def list_title(token):
    if not re.fullmatch(r'[a-f0-9]{32}',token):raise ValueError('保存キーの形式が違います')
    return '候補_'+hashlib.sha256(token.encode()).hexdigest()[:24]

def validate_shortlist(items):
    if not isinstance(items,list) or len(items)>100:raise ValueError('狙い台は100台以内です')
    out=[];seen=set()
    for i in items:
        n=int(i['台番'])
        if n<=0 or n in seen:raise ValueError('台番が重複、または不正です')
        status=str(i.get('状態','未確認'))
        if status not in ['未確認','確保','空いてない','見送り']:raise ValueError('状態が不正です')
        out.append({'台番':n,'状態':status,'メモ':str(i.get('メモ',''))[:300]});seen.add(n)
    return out

def load_shortlist(book,token):
    try:ws=book.worksheet(list_title(token))
    except gspread.WorksheetNotFound:return []
    rows=ws.get_all_values()
    return validate_shortlist([{'台番':r[0],'状態':r[1] if len(r)>1 else '未確認','メモ':r[2] if len(r)>2 else ''} for r in rows[1:] if r and r[0]])

def save_shortlist(book,token,items):
    items=validate_shortlist(items)
    try:ws=book.worksheet(list_title(token))
    except gspread.WorksheetNotFound:ws=book.add_worksheet(title=list_title(token),rows=101,cols=3)
    values=[['台番','状態','メモ']]+[[r['台番'],r['状態'],r['メモ']] for r in items]
    values += [['','','']]*(101-len(values))
    ws.update(values=values,range_name='A1:C101',value_input_option='RAW')
