from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
import html,json,os,re,uuid,unicodedata
from pathlib import Path
from urllib.parse import quote
import pandas as pd
import streamlit as st
from data import demo_history,negative_ranking,period
from storage import spreadsheet,read_history,load_shortlist,save_shortlist,validate_shortlist
from site7 import machine_link,CONFIG,HALL_URL
from island import POSITIONS,render_map,focus_crop,encode
st.set_page_config(page_title='SLOTDASH | HYPER ARROW 美原',page_icon='🎰',layout='wide')
st.markdown('''<style>
.block-container{max-width:1320px;padding-top:1.6rem;padding-bottom:4rem}
h1{font-size:2rem!important;letter-spacing:.08em}h2{font-size:1.35rem!important}
[data-testid="stMetricValue"]{font-size:1.7rem}[data-testid="stMetric"]{background:#141e32;border:1px solid #28344b;border-radius:12px;padding:12px}
[data-testid="stCaptionContainer"]{color:#aab9d2}button{min-height:42px}.tag{color:#ffcd72;font-size:.82rem}.seat{font-size:1.55rem;font-weight:750;color:#e9f1ff}.muted{color:#aab9d2;font-size:.85rem}
@media(max-width:640px){.block-container{padding:1rem .8rem}h1{font-size:1.6rem!important}}
</style>''',unsafe_allow_html=True)

def config():
    try:s=dict(st.secrets)
    except FileNotFoundError:s={}
    sheet=s.get('spreadsheet_id') or os.getenv('SPREADSHEET_ID','')
    credentials=s.get('gcp_service_account')
    if not credentials:
        raw=os.getenv('GCP_CREDENTIALS') or os.getenv('GCP_SERVICE_ACCOUNT')
        if raw:credentials=json.loads(raw)
    if credentials and not sheet:sheet='1Q0JDxFavUDeLtyrd0pCkpiYslR2eMlL1Gozi9r2kuUc'
    return sheet,credentials
@st.cache_resource
def get_book(sheet,credentials_json):return spreadsheet(json.loads(credentials_json),sheet)
@st.cache_data(ttl=300,show_spinner='データを読み込んでいます…')
def cached_history(sheet,credentials_json):return read_history(get_book(sheet,credentials_json))

def fmt(v,signed=False,suffix=''):
    if pd.isna(v):return '—'
    return (f'{v:+,.0f}' if signed else f'{v:,.0f}')+suffix

def group_filter(frame,group):
    if frame.empty:return frame
    mask=frame['機種名'].str.contains('ジャグラー',na=False)
    return frame if group=='全機種' else frame[mask if group=='ジャグラー' else ~mask]

def add_seat(seat):
    if any(r['台番']==seat for r in st.session_state.picks):st.toast('登録済みやで');return
    if len(st.session_state.picks)>=100:st.warning('狙い台は100台までです');return
    st.session_state.picks.append({'台番':int(seat),'状態':'未確認','メモ':''});st.session_state.dirty=True;st.toast(f'{seat}番台を狙い台に追加')

def jump_map(seat):st.session_state.pending_focus=int(seat)

def parse_bulk_seats(text,valid_seats,existing_seats):
    text=unicodedata.normalize('NFKC',text).strip()
    tokens=[part for part in re.split(r'[\s,、，;；]+',text) if part]
    if not tokens:raise ValueError('台番号を入力してください。')
    requested=[]
    for token in tokens:
        match=re.fullmatch(r'([0-9]{1,6})(?:番台|番)?',token)
        if not match:raise ValueError('台番号をカンマ・改行・スペースで区切って入力してください。')
        seat=int(match.group(1))
        if seat not in valid_seats:raise ValueError(f'{seat}番台は現在の台一覧・島図にありません。入力内容を確認してください。')
        if seat not in requested:requested.append(seat)
    additions=[seat for seat in requested if seat not in existing_seats]
    if len(existing_seats)+len(additions)>100:
        raise ValueError(f'狙い台は100台までです。あと{max(0,100-len(existing_seats))}台追加できます。')
    return additions,len(tokens)-len(additions)

def recommendations_for_rules(history,rules):
    """Keep the editable rules in the app, independent of data.py's old API."""
    days=int(rules['days'])
    daily_max=int(rules['daily_max'])
    min_spins=int(rules['min_spins'])
    if not 1<=days<=14 or not -20000<=daily_max<=20000 or not 0<=min_spins<=20000:
        raise ValueError('おすすめ条件が範囲外です')
    frame,dates=period(history,days)
    if frame.empty:return frame,dates
    matched=frame['全日あり'] & frame['最大差枚'].le(daily_max) & frame['平均回転数'].ge(min_spins)
    return frame.loc[matched].sort_values(['差枚合計','台番']),dates

HALL_ID='hyper-arrow-mihara'
RULE_FIELDS={'days':('判定する営業日数',1,14,1),'daily_max':('各日の差枚上限（枚）',-20000,20000,100),'min_spins':('平均回転数の下限（G）',0,20000,100)}
DEFAULT_RULES={'days':3,'daily_max':1000,'min_spins':6000}
rules_path=Path(__file__).parent/'recommendation_rules.json'
if rules_path.exists():
    try:
        configured=json.loads(rules_path.read_text(encoding='utf-8')).get(HALL_ID,{})
        for field,(_,lower,upper,_) in RULE_FIELDS.items():
            value=int(configured.get(field,DEFAULT_RULES[field]))
            if not lower<=value<=upper:raise ValueError(field)
            DEFAULT_RULES[field]=value
    except (ValueError,TypeError,AttributeError,OSError):
        DEFAULT_RULES={'days':3,'daily_max':1000,'min_spins':6000}
        st.warning('店舗の初期条件を読み込めなかったため、標準の条件を使用しています。')

def reset_recommendation_rules():
    for field in RULE_FIELDS:
        st.session_state[f'{HALL_ID}_{field}']=DEFAULT_RULES[field]
        st.query_params.pop(field,None)

for field,(_,lower,upper,_) in RULE_FIELDS.items():
    key=f'{HALL_ID}_{field}'
    if key not in st.session_state:
        raw=st.query_params.get(field,'')
        try:value=int(raw)
        except (TypeError,ValueError):value=DEFAULT_RULES[field]
        st.session_state[key]=value if lower<=value<=upper else DEFAULT_RULES[field]

with st.expander('⚙️ この店のおすすめ条件'):
    st.caption('設定はこのページのURLに残ります。URLをブックマークすると次回も同じ条件で開けます。')
    cols=st.columns(3)
    for col,(field,(label,lower,upper,step)) in zip(cols,RULE_FIELDS.items()):
        col.number_input(label,min_value=lower,max_value=upper,step=step,key=f'{HALL_ID}_{field}')
    st.button('この店の初期条件に戻す',on_click=reset_recommendation_rules)
rules={field:int(st.session_state[f'{HALL_ID}_{field}']) for field in RULE_FIELDS}
for field,value in rules.items():
    if value==DEFAULT_RULES[field]:st.query_params.pop(field,None)
    else:st.query_params[field]=str(value)
rule_description=f"{rules['days']}営業日すべて差枚＋{rules['daily_max']:,}枚以下、平均{rules['min_spins']:,}G以上".replace('＋-','−')

st.title('SLOTDASH')
st.caption('HYPER ARROW 美原  •  狙う台を、ひと目で。')
sheet,credentials=config();demo=not (sheet and credentials)
if bool(sheet)!=bool(credentials):
    st.error('接続設定が片方だけ入っています。spreadsheet_id と gcp_service_account の両方を設定してください。');st.stop()
if demo:
    history=demo_history();notes=[];book=None
    st.info('デモ表示中：数値と機種配置は架空です。Google Sheetsを接続すると実データに切り替わります。')
else:
    credentials_json=json.dumps(dict(credentials),sort_keys=True)
    try:
        book=get_book(sheet,credentials_json);history,notes=cached_history(sheet,credentials_json)
    except Exception:
        st.error('Google Sheetsを読み込めませんでした。シートID、サービスアカウントへの共有、APIの有効化を確認してください。')
        if st.button('もう一度読み込む'):cached_history.clear();st.rerun()
        st.stop()
if not history:
    st.warning('営業日のデータがまだありません。GitHub Actionsの「みんレポ自動スクレイピング」をbackfillモードで実行してください。');st.stop()
all_dates=sorted(history,reverse=True)
head=st.columns([3,1])
with head[0]:anchor=st.selectbox('基準の営業日',all_dates,index=0)
with head[1]:
    st.write('')
    if st.button('↻ 再読込',width='stretch'):cached_history.clear();st.rerun()
history={d:f for d,f in history.items() if d<=anchor};dates=sorted(history,reverse=True);latest=history[anchor]
if latest.empty:st.warning('この日のシートは空です。日付を変えるか、自動取得を再実行してください。');st.stop()
st.caption(f'{anchor} / 読み込み {len(latest)}台 / 島図 551台')
yesterday=datetime.now(ZoneInfo('Asia/Tokyo')).date()-timedelta(days=1)
if anchor!=yesterday.isoformat():st.caption(f'表示対象は {anchor}。前日分ではありません。')
missing_days=[(datetime.fromisoformat(anchor).date()-timedelta(days=i)).isoformat() for i in range(7) if (datetime.fromisoformat(anchor).date()-timedelta(days=i)).isoformat() not in history]
if missing_days:st.warning('期間集計は保存済みの直近営業日を使用します。未保存の日付：'+', '.join(missing_days))
if notes:
    with st.expander(f'データ確認のお知らせ {len(notes)}件'):st.write('\n\n'.join(notes[:100]))
unknown=sorted(set(latest['台番'])-set(POSITIONS))
if unknown:st.warning('島図に座標のない台：'+', '.join(map(str,unknown)))
names=dict(zip(latest['台番'],latest['機種名']))
recommended,rec_dates=recommendations_for_rules(history,rules)
rec_seats=set(recommended.get('台番',[]))

# A private random bookmark identifies one shortlist. No shared global shortlist.
if 'token' not in st.session_state:
    token=st.query_params.get('list','')
    st.session_state.token=token if re.fullmatch('[a-f0-9]{32}',token) else uuid.uuid4().hex
st.query_params['list']=st.session_state.token
if 'picks' not in st.session_state:
    try:st.session_state.picks=[] if demo else load_shortlist(book,st.session_state.token)
    except Exception:st.error('狙い台を読み込めませんでした。保存済みの内容を保護するため再読込してください。');st.stop()
    st.session_state.dirty=False
if 'screen' not in st.session_state:st.session_state.screen='✨ おすすめ'

def open_detail(seat):st.session_state.detail_seat=int(seat)
def close_detail():st.session_state.detail_seat=None

@st.dialog('台の詳細',width='large',on_dismiss=close_detail)
def detail(seat):
    name=names.get(seat,'この日の機種データなし')
    st.subheader(f'{seat}番台  {name}')
    if seat in rec_seats:st.success('おすすめ条件に該当：'+rule_description)
    rows=[]
    for date in dates:
        f=history[date];r=f[f['台番']==seat]
        rows.append({'営業日':date,'機種名':r.iloc[0]['機種名'] if len(r) else '', '差枚':r.iloc[0]['差枚'] if len(r) else None,'回転数':r.iloc[0]['回転数'] if len(r) else None})
    df=pd.DataFrame(rows)
    st.line_chart(df.sort_values('営業日').set_index('営業日')[['差枚']],height=190)
    st.dataframe(df,hide_index=True,width='stretch')
    c=st.columns(2)
    if c[0].button('＋ 狙い台に追加',width='stretch'):add_seat(seat)
    if c[1].button('🗺 島図で位置を見る',width='stretch'):jump_map(seat);close_detail();st.rerun()
    url,direct=machine_link(name,seat)
    if demo and seat not in {m['example_seat'] for m in CONFIG['machines']}:url,direct=HALL_URL,False
    st.link_button('サイトセブンでこの台を見る' if direct else 'サイトセブンの店舗ページ',url,width='stretch')
    st.caption('サイトセブン側でログイン・契約が必要な場合があります。'+('' if direct else 'この機種の個別リンクは未登録です。'))
    st.link_button('機種の攻略情報を探す','https://chonborista.com/?s='+quote(name),width='stretch')

def card_list(frame,prefix,limit=12):
    if frame.empty:st.info('この条件に該当する台はありません。');return
    pages=max(1,(len(frame)+limit-1)//limit)
    page=st.selectbox('ページ',range(1,pages+1),key=prefix+'_page') if pages>1 else 1
    chunk=frame.iloc[(page-1)*limit:page*limit]
    for start in range(0,len(chunk),3):
        cols=st.columns(3)
        for col,(_,r) in zip(cols,chunk.iloc[start:start+3].iterrows()):
            n=int(r['台番'])
            with col,st.container(border=True):
                st.markdown(f'<span class="seat">{n}</span> <span class="tag">'+('★ おすすめ' if n in rec_seats else '')+'</span>',unsafe_allow_html=True)
                st.write(r['機種名'])
                st.caption('差枚合計  '+fmt(r['差枚合計'],True,'枚')+'  /  平均 '+fmt(r['平均回転数'],suffix='G'))
                a,b=st.columns(2)
                if a.button('詳細',key=f'{prefix}_detail_{n}',width='stretch'):open_detail(n)
                if b.button('＋ 狙い台',key=f'{prefix}_add_{n}',width='stretch'):add_seat(n)

if 'pending_focus' in st.session_state:
    st.session_state.focus=st.session_state.pop('pending_focus')
    st.session_state.map_seat=st.session_state.focus
    st.session_state.screen='🗺 島図'

screen=st.radio('画面',['✨ おすすめ','🎯 狙い台','🗺 島図','📋 全台'],key='screen',horizontal=True,label_visibility='collapsed')
if st.session_state.dirty:st.caption('狙い台に未保存の変更があります。「狙い台」画面から保存できます。')

if screen=='✨ おすすめ':
    group=st.segmented_control('機種タイプ',['全機種','ジャグラー','ジャグラー以外'],default='全機種',selection_mode='single') or '全機種'
    rec=group_filter(recommended,group)
    c=st.columns(3);c[0].metric('おすすめ',f'{len(rec)}台');c[1].metric('狙い台',f'{len(st.session_state.picks)}台');c[2].metric('最新日の平均差枚',fmt(latest['差枚'].mean(),True,'枚'))
    st.subheader('高回転 × 出ていない台')
    st.caption(rule_description+'。差枚合計が低い順。')
    st.caption('対象日：'+' / '.join(rec_dates))
    if len(rec_dates)<rules['days']:st.info(f"おすすめの判定には{rules['days']}営業日分のデータが必要です。")
    card_list(rec,'rec')
    st.divider();st.subheader('マイナス差枚ランキング')
    n=st.radio('集計期間',[3,7],format_func=lambda x:f'直近{x}日',horizontal=True)
    ranking,rdates=negative_ranking(history,n);ranking=group_filter(ranking,group)
    st.caption('対象日：'+' / '.join(rdates)+'。全日分が揃い、機種が同じ台の合計差枚で比較。')
    if len(rdates)<n:st.info(f'{n}営業日分が揃うと表示されます。')
    card_list(ranking,'negative_'+str(n))
    st.caption('おすすめは指定した過去データ条件への一致を示します。')

elif screen=='🎯 狙い台':
    st.subheader('当日の狙い台リスト')
    st.caption('上から優先順。変更後に保存してください。このページのURLをブックマークすると、同じリストを開けます。')
    st.caption('URLを知っている人はこのリストを開けます。URLの共有に注意してください。')
    if 'bulk_notice' in st.session_state:st.success(st.session_state.pop('bulk_notice'))
    with st.form('bulk_seats_form'):
        bulk_text=st.text_area('台番号をまとめて入力',placeholder='561、562、570\n580 581 582',max_chars=3000)
        st.caption('カンマ・読点・改行・スペースで区切れます。全角数字もOK。入力順に末尾へ追加し、重複は除きます。')
        bulk_submit=st.form_submit_button('まとめて狙い台に追加',width='stretch')
    if bulk_submit:
        try:
            additions,skipped=parse_bulk_seats(bulk_text,set(POSITIONS)|set(names),{r['台番'] for r in st.session_state.picks})
            if additions:
                st.session_state.picks.extend({'台番':n,'状態':'未確認','メモ':''} for n in additions)
                st.session_state.dirty=True
                st.session_state.bulk_notice=f'{len(additions)}台追加しました。'+(f'重複{skipped}件はスキップしました。' if skipped else '')+'下の「狙い台を保存」で保存してください。'
                st.rerun()
            else:st.info('入力した台はすべて登録済みです。')
        except ValueError as exc:st.error(str(exc))
    if not st.session_state.picks:st.info('おすすめや台の詳細から、狙い台を追加できます。')
    for i,r in enumerate(st.session_state.picks):
        n=r['台番']
        with st.container(border=True):
            st.write(f'**{i+1}.  {n}番台　{names.get(n,"当日のデータなし")}**')
            a,b=st.columns([1,2]);status=a.selectbox('状態',['未確認','確保','空いてない','見送り'],index=['未確認','確保','空いてない','見送り'].index(r['状態']),key=f'status_{n}')
            memo=b.text_input('メモ',r['メモ'],key=f'note_{n}',max_chars=300)
            if status!=r['状態'] or memo!=r['メモ']:r.update(状態=status,メモ=memo);st.session_state.dirty=True
            c=st.columns(4)
            if c[0].button('詳細',key=f'pick_detail_{n}',width='stretch'):open_detail(n)
            if c[1].button('↑',key=f'up_{n}',disabled=i==0,width='stretch'):
                st.session_state.picks[i-1],st.session_state.picks[i]=st.session_state.picks[i],st.session_state.picks[i-1];st.session_state.dirty=True;st.rerun()
            if c[2].button('↓',key=f'down_{n}',disabled=i==len(st.session_state.picks)-1,width='stretch'):
                st.session_state.picks[i+1],st.session_state.picks[i]=st.session_state.picks[i],st.session_state.picks[i+1];st.session_state.dirty=True;st.rerun()
            if c[3].button('削除',key=f'del_{n}',width='stretch'):st.session_state.picks.pop(i);st.session_state.dirty=True;st.rerun()
    if st.button('狙い台を保存',type='primary',width='stretch',disabled=demo):
        try:save_shortlist(book,st.session_state.token,st.session_state.picks);st.session_state.dirty=False;st.success('保存しました')
        except Exception:st.error('保存できませんでした。JSONバックアップをダウンロードしてから再試行してください。')
    if demo:st.caption('デモ中はJSONバックアップで保存できます。')
    st.download_button('JSONバックアップ',json.dumps(st.session_state.picks,ensure_ascii=False,indent=2),'mihara_shortlist.json','application/json')
    with st.expander('バックアップから復元'):
        up=st.file_uploader('JSONファイル',type='json')
        if up and st.button('この内容に置き換える'):
            try:
                if up.size>100000:raise ValueError('ファイルが大きすぎます')
                st.session_state.picks=validate_shortlist(json.load(up));st.session_state.dirty=True
                for k in list(st.session_state):
                    if k.startswith(('status_','note_')):del st.session_state[k]
                st.rerun()
            except (ValueError,TypeError,KeyError):st.error('バックアップの形式が違います。')

elif screen=='🗺 島図':
    st.subheader('島図で場所を確認')
    c=st.columns(3)
    mode=c[0].selectbox('表示',['差枚','回転数','台番のみ'])
    n=c[1].selectbox('期間',[1,3,7],format_func=lambda x:'基準日' if x==1 else f'直近{x}日')
    mark=c[2].toggle('おすすめを表示',value=True)
    seats=sorted(POSITIONS)
    focus=st.selectbox('台番号で探す',[None]+seats,index=seats.index(st.session_state.focus)+1 if st.session_state.get('focus') in seats else 0,format_func=lambda x:'全体を表示' if x is None else f'{x}  {names.get(x,"データなし")}',key='map_seat')
    st.session_state.focus=focus
    p,used_dates=period(history,n);col='差枚合計' if mode=='差枚' else '平均回転数'
    vals={int(r['台番']):float(r[col]) for _,r in p.iterrows() if pd.notna(r[col]) and r['全日あり']}
    caption=('デモ / ' if demo else '')+f'{anchor} 基準・{n}営業日 / '+mode+('（平均）' if mode=='回転数' and n>1 else '')
    picks={r['台番'] for r in st.session_state.picks}
    im=render_map(names,vals,rec_seats if mark else set(),picks,focus,mode,caption,demo)
    if focus:
        st.image(focus_crop(im,focus),width='stretch')
        if st.button(f'{focus}番台の詳細を開く',width='stretch'):open_detail(focus)
        with st.expander('全体の島図'):st.image(im,width='stretch')
    else:st.image(im,width='stretch')
    st.caption('黄枠★＝おすすめ（'+rule_description+'）／桃枠●＝狙い台／青枠＝選択台。台番号を選ぶと周辺を拡大。')
    st.caption('数値の対象日：'+' / '.join(used_dates))
    a,b=st.columns(2)
    a.download_button('島図をPNG保存',encode(im),f'mihara_{anchor}.png','image/png',width='stretch')
    b.download_button('島図をPDF保存',encode(im,'PDF'),f'mihara_{anchor}.pdf','application/pdf',width='stretch')

else:
    st.subheader('全台データ')
    search=st.text_input('機種名・台番号で検索',placeholder='例：ジャグラー / 882')
    df=latest.copy()
    if search:df=df[df['機種名'].str.contains(search,regex=False,na=False)|df['台番'].astype(str).str.contains(search,regex=False)]
    df['おすすめ']=df['台番'].map(lambda n:'★' if n in rec_seats else '')
    st.caption(f'{len(df)}台。行を選択して、詳細ボタンを押してください。')
    event=st.dataframe(df,hide_index=True,width='stretch',on_select='rerun',selection_mode='single-row',key='all_table')
    if event.selection.rows:
        selected=int(df.iloc[event.selection.rows[0]]['台番'])
        if st.button(f'{selected}番台の詳細を開く'):open_detail(selected)
    st.download_button('表示中のCSVを保存',df.to_csv(index=False).encode('utf-8-sig'),f'mihara_{anchor}.csv','text/csv')

if st.session_state.get('detail_seat') is not None:
    detail(st.session_state.detail_seat)
