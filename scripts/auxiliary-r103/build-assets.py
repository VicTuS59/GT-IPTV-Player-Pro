"""Original native UI primitives for the nine approved auxiliary pages."""
from pathlib import Path
from PIL import Image,ImageDraw

ROOT=Path(__file__).resolve().parents[2]
A=ROOT/'GTIPTVPlayerPro/skin/images/auxiliary-r103';A.mkdir(parents=True,exist_ok=True)
for name in ('link','pin','code','search'):
 S=4;im=Image.new('RGBA',(160*S,160*S));d=ImageDraw.Draw(im);cyan='#20D5ED';pale='#9FC5E9';lw=5*S
 xy=lambda b:tuple(round(v*S) for v in b)
 def line(points,color=cyan):d.line([xy(p) for p in points],fill=color,width=lw,joint='curve')
 if name=='link':
  d.rounded_rectangle(xy((18,58,93,106)),radius=23*S,outline=cyan,width=lw)
  d.rounded_rectangle(xy((68,25,143,73)),radius=23*S,outline=cyan,width=lw)
  line(((59,91),(102,49)))
 elif name=='pin':
  d.ellipse(xy((35,14,125,104)),outline=cyan,width=lw)
  line(((40,80),(80,148),(120,80)))
  d.ellipse(xy((63,42,97,76)),outline=cyan,width=lw)
 elif name=='code':
  d.rounded_rectangle(xy((28,12,135,146)),radius=7*S,outline=pale,width=lw)
  line(((45,55),(26,76),(45,95)),pale);line(((110,55),(129,76),(110,95)),pale)
  line(((92,46),(66,105)),pale)
 else:
  d.ellipse(xy((22,15,114,107)),outline=pale,width=lw)
  line(((104,99),(145,144)),pale)
 im.resize((160,160),Image.Resampling.LANCZOS).save(A/(name+'.png'),optimize=True)
for status,color in (('high','#22C55E'),('possible','#EAB308'),('fps_convert','#3B82F6'),('unknown','#94A3B8'),('different','#EF4444')):
 im=Image.new('RGBA',(460,106));d=ImageDraw.Draw(im)
 d.rounded_rectangle((2,2,457,103),radius=18,fill='#091725',outline=color,width=4)
 im.resize((230,53),Image.Resampling.LANCZOS).save(A/('status-'+status+'.png'),optimize=True)
# The single shared raster fallback is intentionally only a flat palette.
im=Image.new('RGBA',(550,220));d=ImageDraw.Draw(im)
d.rounded_rectangle((1,1,548,218),radius=8,outline='#20D5ED',width=2)
im.save(A/'source-focus.png',optimize=True)
im=Image.new('P',(1920,1080),0);im.putpalette([4,16,31]*256)
im.save(A.parent/'canvas-navy-r103.png',optimize=True,bits=1)
print('Built ten auxiliary PNG primitives and the 350-byte shared canvas.')
