from pathlib import Path
from PIL import Image,ImageDraw
ROOT=Path(__file__).resolve().parents[2]
A=ROOT/'GTIPTVPlayerPro/skin/images/seven-r100'
A.mkdir(exist_ok=True)
def panel(name,w,h,focus=False):
 im=Image.new('RGBA',(w*2,h*2));d=ImageDraw.Draw(im)
 top=(10,38,68) if focus else (7,27,51);bottom=(6,26,49) if focus else (4,20,40)
 for y in range(h*2):
  t=y/max(1,h*2-1);d.line((0,y,w*2,y),fill=tuple(round(v+(bottom[i]-v)*t) for i,v in enumerate(top))+(255,))
 mask=Image.new('L',im.size);ImageDraw.Draw(mask).rounded_rectangle((1,1,w*2-2,h*2-2),radius=22,fill=255);im.putalpha(mask)
 d.rounded_rectangle((2,2,w*2-3,h*2-3),radius=22,outline='#20D5ED' if focus else '#244E76',width=7 if focus else 4)
 im.resize((w,h),Image.Resampling.LANCZOS).save(A/(name+'.png'),optimize=True)
for name,w,h in (('left-panel',1276,800),('right-panel',548,800),('row',1250,100),('media-card',432,734),('favorite-card',414,302),('health-status',408,620),('health-info',720,520),('strip',1360,80),('badge',250,56)):
 panel(name,w,h)
for name,w,h in (('row-focus',1250,100),('media-focus',432,734),('favorite-focus',414,302),('tab-focus',310,72)):
 panel(name,w,h,True)
for name in ('server-check','guide','mapping','clock','play','lock','ok','error','busy','neutral'):
 S=4;im=Image.new('RGBA',(160*S,160*S));d=ImageDraw.Draw(im);c={'ok':'#2AE67C','error':'#FF5F7B','busy':'#FFD239'}.get(name,'#20D5ED');lw=5*S
 xy=lambda p:tuple(round(v*S) for v in p)
 def line(points):d.line([xy(p) for p in points],fill=c,width=lw,joint='curve')
 def rect(b,r=8):d.rounded_rectangle(xy(b),radius=r*S,outline=c,width=lw)
 def ellipse(b):d.ellipse(xy(b),outline=c,width=lw)
 if name in ('ok','error','busy','play'):
  ellipse((12,12,148,148))
  if name=='ok':line([(43,82),(68,108),(119,52)])
  elif name=='error':line([(50,50),(110,110)]);line([(110,50),(50,110)])
  elif name=='busy':line([(80,39),(80,82),(111,96)])
  else:line([(62,41),(62,119),(121,80),(62,41)])
 elif name in ('server-check','neutral'):
  for y in (22,63,104):rect((18,y,134,y+29),6);d.ellipse(xy((30,y+10,38,y+18)),fill=c);line([(57,y+14),(117,y+14)])
  if name=='server-check':d.ellipse(xy((92,88,153,149)),fill='#071B33',outline=c,width=lw);line([(105,116),(119,130),(141,105)])
 elif name in ('guide','mapping'):
  rect((12,28,148,126));line([(80,126),(80,146)]);line([(56,146),(104,146)])
  if name=='guide':
   for y in (53,77,101):line([(35,y),(47,y)]);line([(65,y),(125,y)])
   line([(62,9),(80,27),(99,9)])
  else:d.arc(xy((50,57,96,96)),120,360,fill=c,width=lw);d.arc(xy((70,42,116,81)),300,180,fill=c,width=lw);line([(70,80),(97,58)])
 elif name=='clock':d.arc(xy((16,16,144,144)),35,345,fill=c,width=lw);line([(80,39),(80,80),(111,100)]);line([(119,107),(144,109),(147,84)])
 else:rect((39,67,121,140),5);d.arc(xy((54,22,106,88)),180,360,fill=c,width=lw)
 im.resize((160,160),Image.Resampling.LANCZOS).save(A/(name+'.png'),optimize=True)

for name,w,h in (('media-focus',432,734),('favorite-focus',414,302)):
 im=Image.new('RGBA',(w*3,h*3));d=ImageDraw.Draw(im);d.rounded_rectangle((4,4,w*3-5,h*3-5),radius=27,outline='#20D5ED',width=10);im.resize((w,h),Image.Resampling.LANCZOS).save(A/(name+'.png'),optimize=True)
im=Image.new('RGBA',(480,480));d=ImageDraw.Draw(im)
for r in range(210,0,-1):
 t=r/210.;color=(0,round(115+90*t),round(65+50*t),255);d.ellipse((240-r,240-r,240+r,240+r),fill=color)
d.ellipse((30,30,450,450),outline='#25EFA0',width=14);d.line(((139,245),(214,316),(351,167)),fill='#F6FFFA',width=24,joint='curve')
im.resize((160,160),Image.Resampling.LANCZOS).save(A/'ok.png',optimize=True)

im=Image.new('RGBA',(930,216));d=ImageDraw.Draw(im);d.rounded_rectangle((3,3,926,212),radius=27,fill='#0088BD',outline='#20D5ED',width=8);im.resize((310,72),Image.Resampling.LANCZOS).save(A/'tab-focus.png',optimize=True)
