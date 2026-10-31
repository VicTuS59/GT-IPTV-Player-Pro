"""Two original native PNG primitives matching the approved category preview."""
from pathlib import Path
from PIL import Image,ImageDraw
A=Path(__file__).resolve().parents[2]/'GTIPTVPlayerPro/skin/images/drawer-r104'
A.mkdir(parents=True,exist_ok=True)
for name,size,fill,outline in (('panel',(620,824),'#071725','#20D5ED'),('focus',(570,72),'#10375B','#20D5ED')):
 im=Image.new('RGBA',size);d=ImageDraw.Draw(im)
 d.rounded_rectangle((1,1,size[0]-2,size[1]-2),radius=14 if name=='panel' else 9,fill=fill,outline=outline,width=2)
 im.save(A/(name+'.png'),optimize=True)
print('Built two native drawer PNG primitives.')
