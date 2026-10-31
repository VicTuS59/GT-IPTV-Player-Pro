from pathlib import Path
from PIL import Image,ImageDraw
ROOT=Path(__file__).resolve().parents[2]
A=ROOT/'GTIPTVPlayerPro/skin/images/utility-r97'
A.mkdir(exist_ok=True)
for name,wh,fill,edge in (
    ('card',(1200,180),'#0B1C32','#203C58'),('focus',(1200,180),None,'#20D5ED'),
    ('panel',(600,780),'#08192D','#203C58'),('field',(600,84),'#102844','#29435E'),
    ('button',(400,68),'#158442','#24BA65'),('button-disabled',(400,68),'#25364D','#47607A'),
    ('shade',(650,174),'#8004101F',None)):
    im=Image.new('RGBA',(wh[0]*2,wh[1]*2));d=ImageDraw.Draw(im)
    color=(4,16,31,110) if name=='shade' else fill
    d.rounded_rectangle((2,2,wh[0]*2-3,wh[1]*2-3),radius=24,fill=color,outline=edge,width=5)
    im.resize(wh,Image.Resampling.LANCZOS).save(A/(name+'.png'),optimize=True)
for name in ('account','settings','favorite','history','subtitles','file','globe','location','browser','disk','shield','youtube'):
    S=3;im=Image.new('RGBA',(160*S,160*S));d=ImageDraw.Draw(im);c={'favorite':'#FFD239','settings':'#C36CF1','subtitles':'#C36CF1','history':'#00CC78','file':'#9AAEC8','disk':'#9AAEC8','youtube':'#FF152E'}.get(name,'#20D5ED');lw=4*S
    xy=lambda p:tuple(round(v*S) for v in p)
    def line(p):d.line([xy(q) for q in p],fill=c,width=lw,joint='curve')
    def rect(b,r=8):d.rounded_rectangle(xy(b),radius=r*S,outline=c,width=lw)
    def ellipse(b):d.ellipse(xy(b),outline=c,width=lw)
    if name=='account':ellipse((54,14,106,66));rect((30,84,130,144),20)
    elif name=='settings':
        import math
        points=[(80+(64 if i%2==0 else 49)*math.cos(i*math.pi/12),80+(64 if i%2==0 else 49)*math.sin(i*math.pi/12)) for i in range(24)]
        line(points+[points[0]]);ellipse((56,56,104,104))
    elif name=='favorite':
        import math
        p=[(80+(66 if i%2==0 else 28)*math.cos(-math.pi/2+i*math.pi/5),80+(66 if i%2==0 else 28)*math.sin(-math.pi/2+i*math.pi/5)) for i in range(10)];line(p+[p[0]])
    elif name=='history':ellipse((22,22,142,142));line([(80,45),(80,83),(112,83)]);line([(16,21),(16,54),(48,54)])
    elif name=='subtitles':rect((20,30,140,130));line([(40,58),(120,58)]);line([(40,82),(120,82)]);line([(40,106),(88,106)])
    elif name=='file':line([(37,17),(94,17),(127,51),(127,143),(37,143),(37,17)]);line([(94,17),(94,51),(127,51)])
    elif name=='globe':ellipse((15,15,145,145));ellipse((50,15,110,145));line([(16,80),(144,80)]);line([(25,48),(135,48)]);line([(25,112),(135,112)])
    elif name=='location':ellipse((62,33,98,69));d.arc(xy((35,10,125,104)),165,375,fill=c,width=lw);line([(36,68),(80,138),(124,68)])
    elif name=='browser':rect((12,20,148,120));rect((25,36,135,104),4);line([(80,120),(80,145)]);line([(52,145),(108,145)])
    elif name=='disk':line([(32,20),(128,20),(146,104),(146,141),(14,141),(14,104),(32,20)]);line([(14,104),(146,104)]);ellipse((118,118,127,127))
    elif name=='shield':line([(80,10),(139,33),(132,92),(109,124),(80,148),(51,124),(28,92),(21,33),(80,10)]);line([(50,79),(72,100),(114,55)])
    elif name=='youtube':d.rounded_rectangle(xy((8,32,152,128)),radius=22*S,fill=c);d.polygon([xy(q) for q in [(65,55),(65,106),(110,80)]],fill='#FFFFFF')
    im.resize((160,160),Image.Resampling.LANCZOS).save(A/(name+'.png'),optimize=True)
for state in ('checked','unchecked','disabled'):
    im=Image.new('RGBA',(120,120));d=ImageDraw.Draw(im);c='#20D5ED' if state!='disabled' else '#63728C'
    d.rounded_rectangle((8,8,112,112),radius=12,outline=c,width=6,fill=c if state=='checked' else '#0B1C32')
    if state=='checked':d.line([(30,57),(52,79),(90,34)],fill='#04101F',width=10,joint='curve')
    im.resize((40,40),Image.Resampling.LANCZOS).save(A/(state+'.png'),optimize=True)
print('R97 prepared from sealed R96; 22 original PNG primitives.')
