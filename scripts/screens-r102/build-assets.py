"""Original native UI primitives; no text, programme art or provider data."""
from pathlib import Path
from PIL import Image,ImageDraw

ROOT=Path(__file__).resolve().parents[2]
A=ROOT/'GTIPTVPlayerPro/skin/images/screens-r102'
A.mkdir(parents=True,exist_ok=True)

def panel(name,w,h,focus=False):
 im=Image.new('RGBA',(w*2,h*2));d=ImageDraw.Draw(im)
 for y in range(h*2):
  t=y/max(1,h*2-1);top=(5,42,65) if focus else (3,28,47);bottom=(3,27,45) if focus else (2,18,32)
  d.line((0,y,w*2,y),fill=tuple(round(v+(bottom[i]-v)*t) for i,v in enumerate(top))+(255,))
 mask=Image.new('L',im.size);ImageDraw.Draw(mask).rounded_rectangle((1,1,w*2-2,h*2-2),radius=18,fill=255);im.putalpha(mask)
 d.rounded_rectangle((2,2,w*2-3,h*2-3),radius=18,outline='#20D5ED' if focus else '#227091',width=5 if focus else 2)
 im.resize((w,h),Image.Resampling.LANCZOS).save(A/(name+'.png'),optimize=True)

for name,w,h in (('search-list',1116,780),('search-detail',710,780),('search-bar',1836,72),('files-list',1248,762),('files-detail',576,762),('path-bar',1216,72),('footer',1872,88)):
 panel(name,w,h)
panel('search-focus',1072,64,True)
panel('file-focus',1208,56,True)
for name in ('search','folder','folder-outline','storage','document','no-poster'):
 im=Image.new('RGBA',(128*4,128*4));d=ImageDraw.Draw(im);cyan='#20D5ED';lw=5*4
 xy=lambda b:tuple(round(v*4) for v in b)
 if name=='search':
  d.ellipse(xy((19,17,86,84)),outline='#EDF2F8',width=lw)
  d.line([xy((80,78)),xy((113,111))],fill='#EDF2F8',width=lw)
 elif name=='folder-outline':
  d.line([xy(v) for v in ((11,47),(11,31),(49,31),(61,43),(115,43))],fill=cyan,width=lw,joint='curve')
  d.rounded_rectangle(xy((11,46,117,109)),radius=5*4,outline=cyan,width=lw)
 elif name=='folder':
  d.polygon([xy(v) for v in ((11,32),(48,32),(60,44),(117,44),(117,108),(11,108))],fill='#F0C51A')
  d.rounded_rectangle(xy((11,46,117,109)),radius=5*4,fill='#FFD52F',outline='#E4AF0A',width=2*4)
 elif name=='storage':
  d.line([xy(v) for v in ((18,75),(35,26),(93,26),(111,75))],fill=cyan,width=lw,joint='curve')
  d.rounded_rectangle(xy((16,73,113,109)),radius=7*4,outline=cyan,width=lw)
  for x in (81,98):d.ellipse(xy((x-4,87,x+4,95)),fill=cyan)
 elif name=='document':
  d.line([xy(v) for v in ((27,12),(79,12),(105,39),(105,116),(27,116),(27,12))],fill=cyan,width=lw,joint='curve')
  d.line([xy(v) for v in ((78,14),(78,42),(103,42))],fill=cyan,width=lw)
  for y in (63,80,97):d.line([xy((43,y)),xy((88,y))],fill=cyan,width=lw)
 else:
  d.rounded_rectangle(xy((18,16,110,113)),radius=7*4,outline=cyan,width=lw)
  for y in (28,48,68,88):
   for x in (23,98):d.rectangle(xy((x,y,x+7,y+10)),fill=cyan)
  d.line([xy((47,43)),xy((47,85)),xy((83,64)),xy((47,43))],fill=cyan,width=lw,joint='curve')
 im.resize((128,128),Image.Resampling.LANCZOS).save(A/(name+'.png'),optimize=True)
print('Built 15 original R102 PNG UI primitives.')
