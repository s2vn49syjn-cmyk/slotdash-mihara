import io,json,re,math
from functools import lru_cache
from pathlib import Path
from PIL import Image,ImageDraw,ImageFont
ROOT=Path(__file__).parent
POSITIONS={int(k):v for k,v in json.loads((ROOT/'positions.json').read_text()).items()}
@lru_cache(maxsize=16)
def font(size):return ImageFont.truetype(str(ROOT/'assets'/'NotoSansJP.ttf'),size)
def short_name(name):
    pairs=[('東京喰種','東京喰種'),('ミリオンゴッド','ミリゴ'),('戦国乙女5','乙女5'),('海門決戦','カバネリ'),('ファンキー','ファンキー'),('ネオアイム','ネオアイム'),('モンキーターン','モンキーV'),('北斗の拳','北斗'),('炎炎ノ消防隊2','炎炎2'),('マイジャグラー','マイジャグ'),('リコリス','リコリコ'),('ソードアート','SAO'),('ゴーゴー','ゴージャグ')]
    for part,short in pairs:
        if part in name:return short
    return re.sub(r'^(スマスロ\s*|Lパチスロ\s*|パチスロ\s*|Lスマスロ\s*|スロット\s*|L)','',name)[:10]

def cell_colors(value,mode):
    if mode=='台番のみ':return '#ffffff','#111827'
    if value is None or not math.isfinite(value):return '#e5e7eb','#374151'
    if mode=='回転数':
        if value>=8000:return '#1e40af','#ffffff'
        if value>=6000:return '#60a5fa','#0b1120'
        if value>=3000:return '#bfdbfe','#0b1120'
        return '#e2e8f0','#0b1120'
    if value>=4000:return '#c00000','#ffffff'
    if value>=3000:return '#b449b5','#ffffff'
    if value>=2000:return '#70ad47','#152b0c'
    if value>=1000:return '#b4bc24','#252800'
    if value>0:return '#79c1dc','#123643'
    if value==0:return '#dae8f6','#111827'
    return '#ffffff','#b3434b'

def render_map(names,values,recommended=(),shortlist=(),focus=None,mode='差枚',caption='',demo=False):
    scale=2;im=Image.new('RGB',(2205*scale,2070*scale),'#ffffff');d=ImageDraw.Draw(im)
    def text(x,y,s,size=22,color='#111827'):d.text((x*scale,y*scale),s,font=font(size*scale),fill=color)
    text(35,4,'SLOTDASH  /  HYPER ARROW 美原',30)
    text(35,48,caption,18,'#475569')
    for seat,(x,y,w,h) in POSITIONS.items():
        y+=35;val=values.get(seat)
        if val is not None and not math.isfinite(val):val=None
        color,ink=cell_colors(val,mode)
        border='#9ca3af';bw=1
        if seat in recommended:border='#ffc45b';bw=3
        if seat in shortlist:border='#f277b5';bw=3
        if seat==focus:border='#72c9ff';bw=4
        box=(x*scale,y*scale,(x+w)*scale,(y+h)*scale)
        d.rectangle(box,fill='#ffffff',outline=border,width=bw*scale)
        label=str(seat)+('★' if seat in recommended else '')+('●' if seat in shortlist else '')
        label_font=font(8*scale)
        while d.textlength(label,font=label_font)>(w-10)*scale and label_font.size>10:label_font=font(label_font.size-1)
        d.text(((x+5)*scale,(y+3)*scale),label,font=label_font,fill='#111827')
        value='未取得' if val is None else (f'{val:+,.0f}' if mode=='差枚' else f'{val:,.0f}')
        if mode=='台番のみ':value=''
        if value:
            negative=mode=='差枚' and val is not None and val<0
            d.rectangle(((x+4)*scale,(y+15)*scale,(x+w-4)*scale,(y+27)*scale),fill=color,outline='#b3434b' if negative else color,width=scale)
        vf=font(10*scale)
        while d.textlength(value,font=vf)>(w-10)*scale and vf.size>12:vf=font(vf.size-1)
        d.text(((x+5)*scale,(y+14)*scale),value,font=vf,fill=ink)
        name=short_name(names.get(seat,'データなし'))
        text(x+5,y+27,name[:5],7,'#111827')
        if h>=43:text(x+5,y+34,name[5:10],7,'#111827')
    if mode=='差枚':
        legends=[(-1,'マイナス'),(0,'0枚'),(1,'＋1〜999'),(1000,'＋1,000〜1,999'),(2000,'＋2,000〜2,999'),(3000,'＋3,000〜3,999'),(4000,'＋4,000以上'),(None,'未取得')]
    elif mode=='回転数':
        legends=[(0,'3,000G未満'),(3000,'3,000〜6,000G未満'),(6000,'6,000〜8,000G未満'),(8000,'8,000G以上'),(None,'未取得')]
    else:legends=[]
    for i,(value,label) in enumerate(legends):
        x=35+i*267;fill,_=cell_colors(value,mode)
        d.rectangle((x*scale,1910*scale,(x+24)*scale,1934*scale),fill=fill,outline='#b3434b' if mode=='差枚' and value is not None and value<0 else '#9ca3af',width=scale)
        text(x+32,1909,label,16)
    text(35,1960,'★ 黄枠：おすすめ    ● 桃枠：狙い台    青枠：選択台    未取得：データ不足',21)
    text(35,2000,('デモ：差枚・回転数・機種配置は架空です。' if demo else '左下40台は確認済みの補完配置。機種名は選択した最新日のデータ。'),18,'#475569')
    return im

def focus_crop(im,seat):
    if seat not in POSITIONS:return im
    x,y,w,h=POSITIONS[seat];cx=(x+w/2)*2;cy=(y+35+h/2)*2
    halfx=650;halfy=440
    left=max(0,min(im.width-2*halfx,int(cx-halfx)));top=max(0,min(im.height-2*halfy,int(cy-halfy)))
    return im.crop((left,top,left+2*halfx,top+2*halfy))

def encode(im,kind='PNG'):
    buff=io.BytesIO();im.save(buff,format=kind,resolution=150);return buff.getvalue()
