import io,json,re,math
from functools import lru_cache
from pathlib import Path
from PIL import Image,ImageDraw,ImageFont
ROOT=Path(__file__).parent
POSITIONS={int(k):v for k,v in json.loads((ROOT/'positions.json').read_text()).items()}

def compact_positions(positions):
    """Shorten only completely empty horizontal aisles; preserve seat order."""
    intervals=sorted((y,y+h) for x,y,w,h in positions.values())
    edge=intervals[0][0];gaps=[]
    for start,end in intervals:
        if start-edge>24:gaps.append((edge,start,start-edge-24))
        edge=max(edge,end)
    min_x=min(p[0] for p in positions.values());min_y=intervals[0][0]
    return {seat:(x-min_x+12,y-min_y+62-sum(amount for _,end,amount in gaps if y>=end),w,h)
            for seat,(x,y,w,h) in positions.items()}

MAP_POSITIONS=compact_positions(POSITIONS)
MAP_WIDTH=math.ceil(max(x+w for x,y,w,h in MAP_POSITIONS.values())+12)
MAP_BOTTOM=math.ceil(max(y+h for x,y,w,h in MAP_POSITIONS.values()))
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
    scale=2;im=Image.new('RGB',(MAP_WIDTH*scale,(MAP_BOTTOM+132)*scale),'#ffffff');d=ImageDraw.Draw(im)
    def text(x,y,s,size=22,color='#111827'):d.text((x*scale,y*scale),s,font=font(size*scale),fill=color)
    text(12,0,'SLOTDASH  /  HYPER ARROW 美原',26)
    text(12,35,caption,16,'#475569')
    for seat,(x,y,w,h) in MAP_POSITIONS.items():
        val=values.get(seat)
        if val is not None and not math.isfinite(val):val=None
        color,ink=cell_colors(val,mode)
        border='#9ca3af';bw=1
        if seat in recommended:border='#e5a000';bw=3
        if seat in shortlist:border='#be185d';bw=3
        if seat==focus:border='#72c9ff';bw=4
        box=(x*scale,y*scale,(x+w)*scale,(y+h)*scale)
        background='#fce7f3' if seat in shortlist else '#fff8d6' if seat in recommended else '#ffffff'
        d.rectangle(box,fill=background,outline=border,width=bw*scale)
        label=str(seat)+('★' if seat in recommended else '')+('●' if seat in shortlist else '')
        header_ink='#111827'
        if seat in recommended or seat in shortlist:
            header_fill='#be185d' if seat in shortlist else '#fbbf24'
            header_ink='#ffffff' if seat in shortlist else '#111827'
            d.rectangle(((x+2)*scale,(y+2)*scale,(x+w-2)*scale,(y+13)*scale),fill=header_fill)
        label_font=font((9 if seat in recommended or seat in shortlist else 8)*scale)
        while d.textlength(label,font=label_font)>(w-10)*scale and label_font.size>10:label_font=font(label_font.size-1)
        d.text(((x+w/2)*scale,(y+7.5)*scale),label,font=label_font,fill=header_ink,anchor='mm')
        value='未取得' if val is None else (f'{val:+.0f}' if mode=='差枚' else f'{val:.0f}')
        if mode=='台番のみ':value=''
        if value:
            negative=mode=='差枚' and val is not None and val<0
            d.rectangle(((x+3)*scale,(y+14)*scale,(x+w-3)*scale,(y+29)*scale),fill=color,outline='#b3434b' if negative else color,width=scale)
        vf=font(13*scale)
        while d.textlength(value,font=vf)>(w-8)*scale and vf.size>12:vf=font(vf.size-1)
        d.text(((x+w/2)*scale,(y+21.5)*scale),value,font=vf,fill=ink,anchor='mm')
        name=short_name(names.get(seat,'データなし'))
        text(x+4,y+29,name[:5],7,'#111827')
        if h>=43:text(x+4,y+36,name[5:10],6,'#111827')
    if mode=='差枚':
        legends=[(-1,'マイナス'),(0,'0枚'),(1,'＋1〜999'),(1000,'＋1,000〜1,999'),(2000,'＋2,000〜2,999'),(3000,'＋3,000〜3,999'),(4000,'＋4,000以上'),(None,'未取得')]
    elif mode=='回転数':
        legends=[(0,'3,000G未満'),(3000,'3,000〜6,000G未満'),(6000,'6,000〜8,000G未満'),(8000,'8,000G以上'),(None,'未取得')]
    else:legends=[]
    for i,(value,label) in enumerate(legends):
        x=12+i*(MAP_WIDTH-24)/max(1,len(legends));fill,_=cell_colors(value,mode)
        legend_y=MAP_BOTTOM+16
        d.rectangle((x*scale,legend_y*scale,(x+22)*scale,(legend_y+22)*scale),fill=fill,outline='#b3434b' if mode=='差枚' and value is not None and value<0 else '#9ca3af',width=scale)
        text(x+28,legend_y-1,label,15)
    for x,fill,label,ink in [(12,'#fbbf24','★ おすすめ','#111827'),(260,'#be185d','● 自分の狙い台','#ffffff')]:
        d.rectangle((x*scale,(MAP_BOTTOM+50)*scale,(x+232)*scale,(MAP_BOTTOM+80)*scale),fill=fill)
        text(x+8,MAP_BOTTOM+49,label,20,ink)
    text(520,MAP_BOTTOM+51,'青枠：選択台    未取得：データ不足',20)
    text(12,MAP_BOTTOM+85,('デモ：差枚・回転数・機種配置は架空です。' if demo else '左下40台は確認済みの補完配置。機種名は選択した最新日のデータ。'),17,'#475569')
    return im

def focus_crop(im,seat):
    if seat not in MAP_POSITIONS:return im
    x,y,w,h=MAP_POSITIONS[seat];cx=(x+w/2)*2;cy=(y+h/2)*2
    halfx=650;halfy=440
    left=max(0,min(im.width-2*halfx,int(cx-halfx)));top=max(0,min(im.height-2*halfy,int(cy-halfy)))
    return im.crop((left,top,left+2*halfx,top+2*halfy))

def encode(im,kind='PNG'):
    buff=io.BytesIO();im.save(buff,format=kind,resolution=150);return buff.getvalue()
