from pathlib import Path
from PIL import Image,ImageDraw
ROOT=Path(__file__).resolve().parents[2]
A=ROOT/'GTIPTVPlayerPro/skin/images/accounts-r99'
A.mkdir(exist_ok=True)
def panel(name,w,h,top,bottom,edge=None,radius=14,width=2):
 im=Image.new('RGBA',(w*2,h*2));d=ImageDraw.Draw(im)
 a=tuple(bytes.fromhex(top.lstrip('#')));b=tuple(bytes.fromhex(bottom.lstrip('#')))
 for y in range(h*2):
  t=y/max(1,h*2-1);d.line((0,y,w*2,y),fill=tuple(round(v+(b[i]-v)*t) for i,v in enumerate(a))+(255,))
 mask=Image.new('L',im.size);ImageDraw.Draw(mask).rounded_rectangle((0,0,w*2-1,h*2-1),radius=radius*2,fill=255);im.putalpha(mask)
 if edge:d.rounded_rectangle((width,width,w*2-width-1,h*2-width-1),radius=radius*2,outline=edge,width=width*2)
 im.resize((w,h),Image.Resampling.LANCZOS).save(A/(name+'.png'),optimize=True)
panel('list-panel',1276,802,'#081D35','#04152C','#20496F')
panel('detail-panel',548,802,'#081E38','#05162D','#20496F')
panel('row',1256,114,'#071B32','#06192E',radius=0)
panel('focus',1256,110,'#103455','#092645','#20D5ED',width=3)
panel('badge',282,52,'#071A32','#041329','#275174')
for name,a,b,edge in (('neutral','#0A1B30','#041329','#739ABF'),('green','#03261D','#031C16','#42EC79'),('yellow','#292511','#171C14','#F6D15B'),('red','#301725','#201120','#FF647B')):
 panel('status-'+name,260,60,a,b,edge,width=2)
im=Image.new('RGBA',(640,640));d=ImageDraw.Draw(im);c='#20D5ED'
for y in (72,248,424):
 d.rounded_rectangle((76,y,564,y+132),radius=28,outline=c,width=20)
 d.ellipse((112,y+42,160,y+90),fill=c);d.line((228,y+66,512,y+66),fill=c,width=20)
im.resize((160,160),Image.Resampling.LANCZOS).save(A/'server.png',optimize=True)
