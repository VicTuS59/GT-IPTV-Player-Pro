"""One fixed FHD palette theme, fitted to the selected OSD, with old RAM caps."""
import os
import re
from .wallpaper_assets import MASTERS,SOURCE_FAMILIES,WINDOWS

MIB=1024*1024
MAX_CACHE_BYTES=96*MIB
MEMORY_RESERVE_BYTES=64*MIB

def wallpaper_size(width,height,total=None,available=None,cached=0):
    width,height=max(1,int(width)),max(1,int(height))
    # File quality never switches with RAM. SD/HD decode at the OSD size;
    # UHD displays the same FHD bitmap through the native scaling widget.
    scale=min(1.,1920./width,1080./height)
    return max(1,int(round(width*scale))),max(1,int(round(height*scale)))

def cache_budget(pixels,total=None,available=None,cached=0):
    if pixels<=720*576:ceiling=8*MIB
    elif pixels<=1280*720:ceiling=16*MIB
    elif pixels<=1920*1080:ceiling=32*MIB
    elif total is None or total<=1024*MIB:ceiling=32*MIB
    elif total<=2048*MIB:ceiling=64*MIB
    else:ceiling=MAX_CACHE_BYTES
    if total is not None:ceiling=min(ceiling,max(0,int(total)//16))
    if available is None:return min(ceiling,32*MIB)
    pool=max(0,int(cached))+max(0,int(available))
    return max(0,min(ceiling,pool//4,pool-MEMORY_RESERVE_BYTES))

def asset_variant(width,height):
    return '1080'

def wallpaper_path(path,width,height):
    name=os.path.basename(path or '')
    family=SOURCE_FAMILIES.get(name)
    master=MASTERS.get(family)
    if master:
        candidate=os.path.join(os.path.dirname(path),master)
        if os.path.isfile(candidate):return candidate
    return path

def window_path(path,desktop,viewport,target):
    if SOURCE_FAMILIES.get(os.path.basename(path or ''))!='screen-global-r89':return None
    dw,dh=desktop;vw,vh=viewport
    value=WINDOWS.get((dw,dh,vw,vh))
    if value is None:return None
    name,width,height=value;candidate=os.path.join(os.path.dirname(path),name)
    return (candidate,width,height) if os.path.isfile(candidate) else None


def rewrite_wallpaper_skin(skin):
    """Route direct skin backgrounds to the same fixed source, without I/O."""
    def widget(match):
        text=match.group(0)
        image=re.search(r'\bpixmap="([^"]+)"',text)
        if image is None:return text
        path=image[1]
        master=MASTERS.get(SOURCE_FAMILIES.get(os.path.basename(path)))
        if master is None:return text
        text=text[:image.start(1)]+os.path.join(os.path.dirname(path),master)+text[image.end(1):]
        if re.search(r'\bscale="[^"]*"',text):
            return re.sub(r'\bscale="[^"]*"','scale="1"',text)
        return text[:-2]+' scale="1"/>'
    return re.sub(r'<(?:widget|ePixmap)\b[^>]*?/>',widget,skin)
