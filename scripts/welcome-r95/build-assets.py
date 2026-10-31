"""Build original welcome PNG primitives; Pillow is needed on the build host only."""
import math
from pathlib import Path
from PIL import Image,ImageDraw
images=Path(__file__).resolve().parents[2]/'GTIPTVPlayerPro/skin/images/welcome-r95'
images.mkdir(parents=True,exist_ok=True)
S=4
for name in ('shield','server','manage','add','youtube','matches'):
    im=Image.new('RGBA',(160*S,160*S));d=ImageDraw.Draw(im)
    c='#20D5ED' if name in ('shield','server') else '#EDF2F8';lw=5*S
    pts=lambda seq:[(round(x*S),round(y*S)) for x,y in seq]
    def line(seq,color=None,width=lw):d.line(pts(seq),fill=color or c,width=width,joint='curve')
    def ellipse(box):d.ellipse(tuple(round(n*S) for n in box),outline=c,width=lw)
    def rect(box,r=8,fill=None,color=None):d.rounded_rectangle(tuple(round(n*S) for n in box),radius=r*S,fill=fill,outline=color or c,width=lw)
    if name=='shield':
        line([(80,12),(133,35),(130,91),(115,121),(80,146),(45,121),(30,91),(27,35),(80,12)])
        line([(53,77),(73,97),(108,59)])
    elif name=='server':
        ellipse((17,16,125,124));d.arc((40*S,16*S,102*S,124*S),0,360,fill=c,width=lw)
        line([(71,17),(71,123)]);line([(20,52),(122,52)]);line([(20,87),(124,87)])
        for y in (74,101,128):
            rect((85,y,148,y+23),r=5,fill='#071C34');line([(104,y+11),(135,y+11)],width=3*S)
    elif name=='manage':
        for y in (34,67,100):
            d.ellipse((20*S,(y-4)*S,28*S,(y+4)*S),fill=c);line([(41,y),(122,y)])
        points=[(112+(26 if i%4 in (0,1) else 19)*math.cos(i*math.pi/16),115+(26 if i%4 in (0,1) else 19)*math.sin(i*math.pi/16)) for i in range(32)]
        d.ellipse((80*S,83*S,145*S,148*S),fill='#071C34');line(points+[points[0]]);ellipse((104,107,120,123))
    elif name=='add':
        ellipse((50,17,100,67));d.arc((25*S,77*S,126*S,155*S),180,360,fill=c,width=lw)
        line([(25,116),(25,130),(112,130)]);ellipse((99,93,147,141))
        line([(123,105),(123,129)]);line([(111,117),(135,117)])
    elif name=='youtube':
        rect((15,39,145,123),r=23,fill='#FF0033',color='#FF0033')
        d.polygon(pts([(66,58),(104,81),(66,104)]),fill='#FFFFFF')
    else:
        ellipse((16,16,144,144))
        p=[(80+25*math.cos(-math.pi/2+i*2*math.pi/5),80+25*math.sin(-math.pi/2+i*2*math.pi/5)) for i in range(5)]
        d.polygon(pts(p),fill=c)
        for a,b in zip(p,[(80+64*math.cos(-math.pi/2+i*2*math.pi/5),80+64*math.sin(-math.pi/2+i*2*math.pi/5)) for i in range(5)]):line([a,b],width=3*S)
    im.resize((160,160),Image.Resampling.LANCZOS).save(images/(name+'.png'),optimize=True)
for name,w,h,fill,outline in (('source-card',1214,325,'#071C34','#18CBE4'),('action-card',228,275,'#04101F','#344B66')):
    im=Image.new('RGBA',(w*2,h*2));d=ImageDraw.Draw(im)
    d.rounded_rectangle((2,2,w*2-3,h*2-3),radius=34,fill=fill,outline=outline,width=4)
    im.resize((w,h),Image.Resampling.LANCZOS).save(images/(name+'.png'),optimize=True)

