"""User-supplied outgoing links. No access to paid data is performed."""
import json,re,unicodedata
from pathlib import Path
from urllib.parse import urlencode
CONFIG=json.loads(Path(__file__).with_name('site7_links.json').read_text(encoding='utf-8'))
HALL_URL='https://m.site777.jp/f/D0100.do?pmc=27090025'
def normal(name):
    return re.sub(r'[\s・･\-‐ー~〜～]', '', unicodedata.normalize('NFKC',str(name))).lower()
ALIASES={
 '120279':['L東京喰種','東京喰種','東京グール'],
 '120358':['スマスロ ミリオンゴッド-神々の軌跡-','Lミリオンゴッド 神々の軌跡'],
 '120373':['L戦国乙女5 業火を穿つ宿焔の双刃','L戦国乙女5'],
 '120354':['スマスロ 甲鉄城のカバネリ 海門決戦','L甲鉄城のカバネリ 海門決戦'],
 '119996':['ファンキージャグラー２ＫＴ','ファンキージャグラー2'],
 '120312':['ネオアイムジャグラーEX'],
 '120181':['スマスロモンキーターンV','LモンキーターンV'],
 '120122':['Lスマスロ北斗の拳','スマスロ北斗の拳'],
 '120349':['Lパチスロ炎炎ノ消防隊2','スマスロ炎炎ノ消防隊2'],
 '120010':['マイジャグラーV','マイジャグラー5'],
 '120391':['スマスロ リコリス・リコイル','Lリコリス・リコイル']}
def machine_link(name,seat):
    for item in CONFIG['machines']:
        aliases=[item['name']]+ALIASES.get(item['params']['mdc'],[])
        if normal(name) in {normal(a) for a in aliases}:
            return CONFIG['base_url']+'?'+urlencode({**item['params'],'dn':str(int(seat))}),True
    return HALL_URL,False
