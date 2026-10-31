"""Build original PNG utility primitives; Pillow is only a build-host dependency."""
import math
from pathlib import Path
from PIL import Image,ImageDraw
images=Path(__file__).resolve().parents[2]/'GTIPTVPlayerPro/skin/images/utility-r96'
images.mkdir(parents=True,exist_ok=True)
S=4
for name in ('folder','portal','settings-tv','chevron','info','ball'):
    im=Image.new('RGBA',(160*S,160*S));d=ImageDraw.Draw(im);c='#20D5ED';lw=5*S
    pts=lambda seq:[(round(x*S),round(y*S)) for x,y in seq]
    def line(seq,width=lw):d.line(pts(seq),fill=c,width=width,joint='curve')
    def rect(box,r=8):d.rounded_rectangle(tuple(round(n*S) for n in box),radius=r*S,outline=c,width=lw)
    def ellipse(box):d.ellipse(tuple(round(n*S) for n in box),outline=c,width=lw)
    if name=='folder':
        line([(19,49),(19,34),(56,34),(70,49),(141,49),(141,126),(19,126),(19,49)])
        line([(68,68),(101,87),(68,108),(68,68)])
    elif name=='portal':
        rect((14,25,146,116));line([(80,117),(80,140)]);line([(49,141),(111,141)])
        d.arc((49*S,46*S,95*S,92*S),110,320,fill=c,width=lw)
        d.arc((68*S,38*S,114*S,84*S),290,140,fill=c,width=lw);line([(70,77),(94,57)])
    elif name=='settings-tv':
        rect((17,34,143,126));line([(57,13),(80,34),(104,13)]);line([(57,146),(105,146)])
        d.polygon(pts([(64,57),(104,80),(64,104)]),fill=c)
    elif name=='chevron':line([(59,35),(106,80),(59,125)])
    elif name=='info':ellipse((16,16,144,144));line([(80,70),(80,115)]);d.ellipse((75*S,43*S,85*S,53*S),fill=c)
    else:
        c='#6D8BA7';ellipse((16,16,144,144))
        points=[(80+25*math.cos(-math.pi/2+i*2*math.pi/5),80+25*math.sin(-math.pi/2+i*2*math.pi/5)) for i in range(5)]
        line(points+[points[0]],width=3*S)
        for a,b in zip(points,[(80+64*math.cos(-math.pi/2+i*2*math.pi/5),80+64*math.sin(-math.pi/2+i*2*math.pi/5)) for i in range(5)]):line([a,b],width=3*S)
    im.resize((160,160),Image.Resampling.LANCZOS).save(images/(name+'.png'),optimize=True)
shapes=(('source-row',1450,184,'#061427','#29435E'),('info-panel',1450,78,'#061427','#29435E'),
        ('detail-panel',590,780,'#061427','#29435E'),('count-badge',280,64,'#083247','#165E74'),
        ('state-on',180,52,'#236D42','#368759'),('state-off',180,52,'#2C3E59','#3E5673'),
        ('state-error',180,52,'#6B283C','#A34059'),('value-panel',510,80,'#102844','#29435E'),
        ('tab-active',450,72,'#12395E','#20D5ED'),('tab-idle',450,72,'#061427','#29435E'),
        ('row-outline',1180,66,None,'#29435E'),('row-selected',1180,66,None,'#20D5ED'))
for name,w,h,fill,outline in shapes:
    im=Image.new('RGBA',(w*2,h*2));d=ImageDraw.Draw(im)
    d.rounded_rectangle((2,2,w*2-3,h*2-3),radius=24,fill=fill,outline=outline,width=4)
    im.resize((w,h),Image.Resampling.LANCZOS).save(images/(name+'.png'),optimize=True)
for name,color in (('red','#FF334B'),('green','#00CC78'),('yellow','#FFD239'),('blue','#008EFF')):
    im=Image.new('RGBA',(128,128));ImageDraw.Draw(im).ellipse((4,4,124,124),fill=color)
    im.resize((32,32),Image.Resampling.LANCZOS).save(images/('dot-'+name+'.png'),optimize=True)

