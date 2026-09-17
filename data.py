"""Normalization and fixed-period analytics; blank data is never zero."""
import re,unicodedata
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
import numpy as np
import pandas as pd
COLS=['台番','機種名','差枚','回転数']

def number(value):
    s=unicodedata.normalize('NFKC',str(value)).strip().replace(',','').replace('−','-').replace('▲','-').replace('△','-')
    s=re.sub(r'(枚|G|g|回)$','',s).strip()
    if not re.fullmatch(r'[+-]?\d+(?:\.\d+)?',s):return np.nan
    return float(s)

def name_key(value):return re.sub(r'\s+','',unicodedata.normalize('NFKC',str(value)))

def normalize(raw):
    df=pd.DataFrame(raw).copy();lookup={str(c).strip():c for c in df.columns};out=pd.DataFrame()
    aliases={'台番':['台番','台番号','台No','台No.'],'機種名':['機種名','機種','name'],'差枚':['差枚','前日差枚'],'回転数':['回転数','G数','ゲーム数','回転']}
    for key,opts in aliases.items():
        col=next((lookup[x] for x in opts if x in lookup),None)
        if col is None:raise ValueError(f'必要な列がありません: {key}')
        out[key]=df[col].fillna('').astype(str).str.strip() if key=='機種名' else df[col].map(number)
    out=out[out['台番'].notna() & out['台番'].gt(0) & out['台番'].mod(1).eq(0)].copy()
    out['台番']=out['台番'].astype(int);out.loc[out['回転数']<0,'回転数']=np.nan
    records=[];notes=[]
    for seat,g in out.groupby('台番',sort=True):
        if len(g)>1:notes.append(f'{seat}番台: 重複{len(g)}行を統合')
        row={'台番':seat}
        names=g.loc[g['機種名'].ne(''),'機種名']
        row['機種名']=names.iloc[0] if len(names) else '機種不明'
        conflict=names.map(name_key).nunique()>1
        for col in ['差枚','回転数']:
            vals=g[col].dropna().unique()
            row[col]=float(vals[0]) if len(vals)==1 and not conflict else np.nan
            if len(vals)>1:notes.append(f'{seat}番台: {col}が重複行で不一致のため欠損扱い')
        if conflict:notes.append(f'{seat}番台: 重複行の機種が不一致のため欠損扱い')
        records.append(row)
    return pd.DataFrame(records,columns=COLS),notes

def period(history,n):
    dates=sorted(history,reverse=True)[:n]
    if not dates:return pd.DataFrame(),dates
    latest=history[dates[0]].set_index('台番')
    result=latest[['機種名']].copy()
    diff=pd.DataFrame({d:history[d].set_index('台番')['差枚'] for d in dates}).reindex(latest.index)
    spins=pd.DataFrame({d:history[d].set_index('台番')['回転数'] for d in dates}).reindex(latest.index)
    names=pd.DataFrame({d:history[d].set_index('台番')['機種名'].map(name_key) for d in dates}).reindex(latest.index)
    same=names.eq(latest['機種名'].map(name_key),axis=0).all(axis=1)
    complete=diff.notna().sum(axis=1).eq(n)&same
    result['差枚合計']=diff.sum(axis=1,min_count=n).where(same)
    result['平均回転数']=spins.mean(axis=1).where(spins.notna().sum(axis=1).eq(n)&same)
    result['最大差枚']=diff.max(axis=1).where(complete)
    result['日数']=diff.notna().sum(axis=1)
    result['全日あり']=complete & (len(dates)==n)
    return result.reset_index(),dates

def recommendations(history):
    p,dates=period(history,3)
    if p.empty:return p,dates
    return p[p['全日あり'] & p['最大差枚'].le(1000) & p['平均回転数'].ge(6000)].sort_values(['差枚合計','台番']),dates

def negative_ranking(history,n):
    p,dates=period(history,n)
    if p.empty:return p,dates
    return p[p['全日あり'] & p['差枚合計'].lt(0)].sort_values(['差枚合計','台番']),dates

def demo_history():
    """Synthetic data, deliberately not a claim about real machine locations."""
    from site7 import CONFIG
    rng=np.random.default_rng(741);names=[x['name'] for x in CONFIG['machines']];data={}
    yesterday=datetime.now(ZoneInfo('Asia/Tokyo')).date()-timedelta(days=1)
    for i in range(7):
        rows=[]
        for seat in range(561,1112):
            name=names[((seat-561)//20)%len(names)]
            for item in CONFIG['machines']:
                if seat==item['example_seat']:name=item['name'];break
            diff=int(rng.normal(-200,2000)//10*10);spins=int(rng.integers(200,9000))
            if seat%37==0:diff=-500-i*120;spins=7100+i*50
            rows.append([seat,name,diff,spins])
        data[(yesterday-timedelta(days=i)).isoformat()]=pd.DataFrame(rows,columns=COLS)
    return data
