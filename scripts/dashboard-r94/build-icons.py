"""Build original native dashboard icons; requires Pillow on the build host only."""
import math
from pathlib import Path
from PIL import Image,ImageDraw
images=Path(__file__).resolve().parents[2]/'GTIPTVPlayerPro/skin/images/dashboard-r94'
images.mkdir(parents=True,exist_ok=True)
S=4
for name in ('live','catchup','movie','series','discover','continue','favorites','settings'):
    im=Image.new('RGBA',(96*S,96*S),(0,0,0,0));d=ImageDraw.Draw(im)
    c='#DBE6F3';lw=5*S
    pts=lambda seq:[(round(x*S),round(y*S)) for x,y in seq]
    def line(seq):d.line(pts(seq),fill=c,width=lw,joint='curve')
    def ellipse(box):d.ellipse(tuple(round(n*S) for n in box),outline=c,width=lw)
    def rectangle(box,r=8):d.rounded_rectangle(tuple(round(n*S) for n in box),radius=r*S,outline=c,width=lw)
    if name=='live':
        rectangle((12,26,84,78));line([(32,12),(48,26),(64,12)])
    elif name=='catchup':
        d.arc((15*S,15*S,81*S,81*S),20,330,fill=c,width=lw)
        line([(48,27),(48,48),(63,57)]);line([(17,13),(17,34),(36,33)])
    elif name=='movie':
        ellipse((12,12,84,84))
        for a in (0,90,180,270):
            x=48+21*math.cos(math.radians(a));y=48+21*math.sin(math.radians(a));ellipse((x-7,y-7,x+7,y+7))
    elif name=='series':
        rectangle((14,34,82,82),r=4);line([(14,33),(10,18),(77,7),(81,25),(14,37)])
        line([(28,16),(41,31)]);line([(51,12),(63,27)])
    elif name=='discover':ellipse((12,10,65,63));line([(59,59),(84,84)])
    elif name=='continue':ellipse((10,10,86,86));line([(39,28),(66,48),(39,68),(39,28)])
    elif name=='favorites':
        points=[(48+(37 if i%2==0 else 17)*math.cos(-math.pi/2+i*math.pi/5),48+(37 if i%2==0 else 17)*math.sin(-math.pi/2+i*math.pi/5)) for i in range(10)]
        line(points+[points[0]])
    else:
        points=[(48+(37 if i%4 in (0,1) else 29)*math.cos(i*math.pi/16),48+(37 if i%4 in (0,1) else 29)*math.sin(i*math.pi/16)) for i in range(32)]
        line(points+[points[0]]);ellipse((35,35,61,61))
    im.resize((96,96),Image.Resampling.LANCZOS).save(images/('icon-'+name+'.png'),optimize=True)
im=Image.new('RGBA',(450*S,94*S),(0,0,0,0))
ImageDraw.Draw(im).rounded_rectangle((2*S,2*S,448*S,92*S),radius=17*S,outline='#23354B',width=2*S)
im.resize((450,94),Image.Resampling.LANCZOS).save(images/'row-outline.png',optimize=True)
