import io,json,re
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

def render_map(names,values,recommended=(),shortlist=(),focus=None,mode='差枚',caption='',demo=False):
    scale=2;im=Image.new('RGB',(2205*scale,2020*scale),'#0b1120');d=ImageDraw.Draw(im)
    def text(x,y,s,size=22,color='#e8efff'):d.text((x*scale,y*scale),s,font=font(size*scale),fill=color)
    text(35,4,'SLOTDASH  /  HYPER ARROW 美原',30)
    text(35,48,caption,18,'#a8b7ce')
    for seat,(x,y,w,h) in POSITIONS.items():
        y+=35;val=values.get(seat);color='#162239'
        if val is not None:
            if mode=='差枚':color='#1a4a47' if val>0 else '#493045' if val<0 else '#25334b'
            elif mode=='回転数':color='#214c70' if val>=6000 else '#20334e'
        border='#42536d';bw=1
        if seat in recommended:border='#ffc45b';bw=3
        if seat in shortlist:border='#f277b5';bw=3
        if seat==focus:border='#72c9ff';bw=4
        box=(x*scale,y*scale,(x+w)*scale,(y+h)*scale)
        d.rectangle(box,fill=color,outline=border,width=bw*scale)
        label=str(seat)+('★' if seat in recommended else '')
        if seat in shortlist:label=str(seat)+'●'
        text(x+5,y+3,label,8,'#ffdb95' if seat in recommended else '#edf3ff')
        value='—' if val is None else (f'{val:+,.0f}' if mode=='差枚' else f'{val:,.0f}')
        if mode=='台番のみ':value=''
        vf=font(10*scale)
        while d.textlength(value,font=vf)>(w-10)*scale and vf.size>12:vf=font(vf.size-1)
        d.text(((x+5)*scale,(y+15)*scale),value,font=vf,fill='#ffffff')
        name=short_name(names.get(seat,'データなし'))
        text(x+5,y+28,name[:5],7,'#cbd7ee');text(x+5,y+35,name[5:10],7,'#cbd7ee')
    text(35,1930,'★ 黄枠：おすすめ    ● 桃枠：狙い台    青枠：選択台    —：データ不足',21)
    text(35,1963,('デモ：差枚・回転数・機種配置は架空です。' if demo else '左下40台は確認済みの補完配置。機種名は選択した最新日のデータ。'),18,'#a8b7ce')
    return im

def focus_crop(im,seat):
    if seat not in POSITIONS:return im
    x,y,w,h=POSITIONS[seat];cx=(x+w/2)*2;cy=(y+35+h/2)*2
    halfx=650;halfy=440
    left=max(0,min(im.width-2*halfx,int(cx-halfx)));top=max(0,min(im.height-2*halfy,int(cy-halfy)))
    return im.crop((left,top,left+2*halfx,top+2*halfy))

def encode(im,kind='PNG'):
    buff=io.BytesIO();im.save(buff,format=kind,resolution=150);return buff.getvalue()
