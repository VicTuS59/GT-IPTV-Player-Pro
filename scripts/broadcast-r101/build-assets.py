from pathlib import Path
from PIL import Image,ImageDraw
ROOT=Path(__file__).resolve().parents[2]
A=ROOT/'GTIPTVPlayerPro/skin/images/broadcast-r101'
A.mkdir(exist_ok=True)
def panel(name,w,h,focus=False):
 im=Image.new('RGBA',(w*2,h*2));d=ImageDraw.Draw(im)
 for y in range(h*2):
  t=y/max(1,h*2-1);top=(8,48,83) if focus else (3,29,53);bottom=(3,32,61) if focus else (2,18,35)
  d.line((0,y,w*2,y),fill=tuple(round(v+(bottom[i]-v)*t) for i,v in enumerate(top))+(255,))
 mask=Image.new('L',im.size);ImageDraw.Draw(mask).rounded_rectangle((1,1,w*2-2,h*2-2),radius=18,fill=255);im.putalpha(mask)
 d.rounded_rectangle((2,2,w*2-3,h*2-3),radius=18,outline='#20D5ED' if focus else '#22597B',width=6 if focus else 3)
 im.resize((w,h),Image.Resampling.LANCZOS).save(A/(name+'.png'),optimize=True)
for n,w,h in (('wide-panel',1260,830),('narrow-panel',520,830),('file-row',650,116),('category-row',915,82),('stream-row',880,74),('archive-row',820,82),('tab-idle',310,62),('footer',1850,90)):
 panel(n,w,h)
for n,w,h in (('file-focus',650,116),('category-focus',915,82),('stream-focus',880,74),('archive-focus',820,82),('tab-active',310,62)):
 panel(n,w,h,True)
for n in ('file','plus','tv','news','kids','sport','tiles','archive'):
 S=4;im=Image.new('RGBA',(160*S,160*S));d=ImageDraw.Draw(im);c='#20D5ED';white='#DDEAF9';lw=5*S
 xy=lambda p:tuple(round(v*S) for v in p)
 def line(points,color=c):d.line([xy(p) for p in points],fill=color,width=lw,joint='curve')
 def rect(box,r=6,color=c):d.rounded_rectangle(xy(box),radius=r*S,outline=color,width=lw)
 def ellipse(box,color=c):d.ellipse(xy(box),outline=color,width=lw)
 if n in ('file','news'):
  line([(40,15),(96,15),(125,44),(125,145),(35,145),(35,15),(40,15)]);line([(94,17),(94,47),(124,47)])
  for y in (75,94,114):line([(54,y),(105,y)])
 elif n=='plus':ellipse((13,13,147,147));line([(80,45),(80,115)]);line([(45,80),(115,80)])
 elif n=='tv':rect((15,35,145,125),color=white);line([(55,145),(105,145)],white);line([(80,125),(80,145)],white)
 elif n=='tiles':
  for i,color in enumerate(('#4F7CFF','#C24EFF','#FFD239','#20D5ED')):
   x=23+i%2*66;y=23+i//2*66;d.rounded_rectangle(xy((x,y,x+49,y+49)),radius=7*S,fill=color)
 elif n=='kids':
  ellipse((23,29,137,143),white);d.arc(xy((50,71,110,119)),0,180,fill=white,width=lw)
  for x in (58,101):d.ellipse(xy((x-4,66,x+4,74)),fill=white)
  d.arc(xy((55,12,106,61)),180,360,fill=white,width=lw)
 elif n=='sport':
  ellipse((15,15,145,145),white);d.regular_polygon((80*S,80*S,31*S),5,rotation=0,fill=white)
  for a,b in (((80,15),(80,48)),((145,80),(111,80)),((80,145),(80,111)),((15,80),(48,80))):line([a,b],white)
 else:
  rect((15,26,130,133));line([(15,58),(130,58)]);line([(40,10),(40,41)]);line([(108,10),(108,41)])
  for x in (38,64,90):
   for y in (79,102):d.rectangle(xy((x,y,x+8,y+8)),fill=c)
  d.ellipse(xy((88,82,156,150)),fill='#031C34',outline=c,width=lw);line([(122,95),(122,119),(138,127)])
 im.resize((160,160),Image.Resampling.LANCZOS).save(A/(n+'.png'),optimize=True)

im=Image.new('RGBA',(1640,164));d=ImageDraw.Draw(im);d.rounded_rectangle((1,1,1638,162),radius=16,fill='#0D365D',outline='#22597B',width=3);im.resize((820,82),Image.Resampling.LANCZOS).save(A/'selection.png',optimize=True)
