import os, sys, re, json, datetime, collections
HERE=os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0,HERE)
from fitshdr import header
ROOT=os.environ.get('ASTRO_ROOT') or os.path.dirname(os.path.dirname(HERE))
SKIP=re.compile(r'^(_to_delete|process|masters|siril.*|wbpp.*|pixinsight.*|autointegrate|session \d+|dss.*|logs?|cache|drizztmp|calibrated|debayered|registered)$',re.I)
BAD=re.compile(r'^(trash|badframes)$',re.I)
FITS=re.compile(r'\.(fits?|xisf)$',re.I)
CAMS=[('2600MC','ASI2600MC Pro'),('2600MM','ASI2600MM Pro'),('294MC','ASI294MC Pro'),('183MM','ASI183MM Pro (PANE)')]
def camfolder(inst):
    for k,v in CAMS:
        if k in (inst or ''): return v
def f(h,*ks):
    for k in ks:
        if k in h and h[k] not in ('',None): return h[k]
def num(x):
    try: return float(x)
    except: return None
def kind(h,name,parent):
    t=(h.get('IMAGETYP') or h.get('FRAME') or '').lower()
    p=parent.lower(); n=name.lower()
    if 'darkflat' in p.replace(' ','') or 'flatdark' in t.replace(' ','') or 'dark flat' in t: return 'DarkFlat'
    if 'flat' in t: return 'Flat'
    if 'dark' in t:
        e=num(f(h,'EXPTIME','EXPOSURE'))
        return 'Dark'
    if 'bias' in t or 'offset' in t: return 'Bias'
    if 'light' in t: return 'Light'
    for k in ('darkflat','flat','dark','bias','light'):
        if k in n or k in p: return {'darkflat':'DarkFlat'}.get(k,k.capitalize())
    return 'Unknown'
def hms(deg,ra=True):
    d=num(deg)
    if d is None: return str(deg)
    if ra:
        h=d/15; H=int(h); m=(h-H)*60; M=int(m); S=(m-M)*60; return f"{H:02d}h{M:02d}m{S:04.1f}s"
    s='-' if d<0 else '+'; d=abs(d); D=int(d); m=(d-D)*60; M=int(m); S=(m-M)*60; return f"{s}{D:02d}°{M:02d}'{S:04.1f}\""
def dateobs(h):
    v=f(h,'DATE-OBS','DATE-LOC')
    if not v: return None
    try: return datetime.datetime.fromisoformat(v[:19])
    except: return None
SITES=[(41.43,-74.036,'Cornwall, NY (home)'),(41.5,-74.0167,'Cornwall, NY area (coarse NINA coords - likely home)'),
 (41.3903,-73.9539,'West Point, NY'),(41.3819,-73.9752,'West Point, NY area'),(41.75,-73.9167,'GPS error - Chris confirms this was home (Cornwall, NY)'),
 (42.0896,-73.72,'MHAA Star Party (Lake Taghkanic State Park parking lot, Ancram NY)'),(44.3875,-68.0155,'Gouldsboro/Schoodic Peninsula area, Maine')]
def sexa(v):
    x=num(v)
    if x is not None: return x
    m=re.match(r'\s*([+-]?)(\d+)[ :d°]+(\d+)[ :\']+([\d.]+)',str(v or ''))
    if not m: return None
    d=int(m.group(2))+int(m.group(3))/60+float(m.group(4))/3600
    return -d if m.group(1)=='-' else d
def place(lat,lon):
    la,lo=sexa(lat),sexa(lon)
    if la is None or lo is None: return None
    import math
    best=min(SITES,key=lambda s:(s[0]-la)**2+((s[1]-lo)*math.cos(math.radians(la)))**2)
    dkm=111*math.hypot(best[0]-la,(best[1]-lo)*math.cos(math.radians(la)))
    return f"{best[2]}  (~{best[0]:.3f}, {best[1]:.3f})" if dkm<4 else f"unrecognized site ({la:.4f}, {lo:.4f})"
def scope(fl,session):
    SC={'Z61':'William Optics ZenithStar 61 (Z61)','WO61':'William Optics ZenithStar 61 (Z61)','RC6':'6in Ritchey-Chretien (RC6)','SV503':'SVBony SV503 80mm','FMA135':'Askar FMA135'}
    toks=[t for t in re.split(r'[- ]',session.split('/')[1]) if t.upper() in SC]
    if toks: return SC[toks[0].upper()]+' (from folder name)'
    fl=num(fl)
    if fl is None: return 'unknown - confirm'
    for lo,hi,n in ((130,145,'Askar FMA135'),(245,256,'Seestar S50 (integrated)'),(280,292,'Z61 with 0.8x reducer'),(355,372,'Z61 with flattener'),(555,575,'SVBony SV503 80mm'),(1360,1395,'RC6')):
        if lo<=fl<=hi: return n+' (inferred from focal length - confirm)'
    return 'unknown - confirm'
def build_index():
    idx=[]
    for base,kindname in (('001-MasterBias','Bias'),('002-MasterDarks','Dark')):
        for dp,dn,fn in os.walk(os.path.join(ROOT,base)):
            fits=sorted(x for x in fn if FITS.search(x))
            if not fits or '/Archive' in dp and False: pass
            if not fits: continue
            h=header(os.path.join(dp,fits[0]))
            dates=[dateobs(header(os.path.join(dp,x))) for x in (fits[0],fits[-1])]
            idx.append(dict(kind=kindname,path=os.path.relpath(dp,ROOT),n=len(fits),inst=f(h,'INSTRUME'),cam=camfolder(f(h,'INSTRUME') or dp),
              exp=num(f(h,'EXPTIME','EXPOSURE')),gain=f(h,'GAIN'),offset=f(h,'OFFSET'),temp=num(f(h,'CCD-TEMP')),settemp=f(h,'SET-TEMP'),
              date=(min(d for d in dates if d).date().isoformat() if any(dates) else None),bin=f(h,'XBINNING')))
    json.dump(idx,open(os.path.join(HERE,'calindex.json'),'w'),indent=1)
    return idx
def scan(session):
    frames=[]
    for dp,dn,fn in os.walk(session):
        dn[:]=[d for d in dn if not SKIP.match(d)]
        rel=os.path.relpath(dp,session)
        bad=any(BAD.match(p) for p in rel.split(os.sep))
        for x in fn:
            if not FITS.search(x): continue
            p=os.path.join(dp,x)
            if os.path.islink(p): continue
            h=header(p)
            if f(h,'NAXIS')=='3' or 'Stacked' in x or x.lower().startswith(('master','result','starless','starmask','autosave')) or re.search(r'_\d+x\d+sec_',x): continue
            frames.append(dict(rel=os.path.relpath(p,session),dir=rel,h=h,k=kind(h,x,os.path.basename(dp)),bad=bad))
    return frames
def gb(frames,keyf):
    d=collections.OrderedDict()
    for fr in frames: d.setdefault(keyf(fr),[]).append(fr)
    return d
def fmt(e): return '?' if e is None else f'{e:g}'
def nightof(dt): return (dt-datetime.timedelta(hours=16)).date() if dt else None
def main(session,idx):
    S=os.path.join(ROOT,session); fr=scan(S)
    L=[x for x in fr if x['k']=='Light' and not x['bad']]
    if not L: print('SKIP no lights:',session); return
    h0=L[0]['h']; inst=f(h0,'INSTRUME'); cam=camfolder(inst or session)
    out=[]; w=out.append
    w('PROJECT / SESSION INFORMATION'); w('='*60)
    w(f"Target folder : {session.split('/')[0]}"); w(f"Session folder: {session.split('/')[1]}")
    w(f"Generated     : {datetime.date.today().isoformat()} by Claude from FITS headers + folder names")
    w('')
    w('TARGET'); w('-'*60)
    objs=collections.Counter(f(x['h'],'OBJECT') for x in L); w(f"Object (header) : {', '.join(f'{k} ({v})' for k,v in objs.items() if k) or 'n/a'}")
    ra=f(h0,'RA','OBJCTRA'); de=f(h0,'DEC','OBJCTDEC')
    if f(h0,'OBJCTRA') and num(ra) is None: ra,de=h0['OBJCTRA'],h0.get('OBJCTDEC')
    if ra: w(f"Pointing RA/Dec : {hms(ra) if num(ra) is not None else ra}  {hms(de,False) if num(de) is not None else de}  (J2000, from mount)")
    if f(h0,'CRVAL1'): w(f"Plate-solved ctr: {hms(h0['CRVAL1'])}  {hms(h0['CRVAL2'],False)}  (first light frame)")
    if f(h0,'ROTATOR','OBJCTROT','POSANGLE'): w(f"Rotation/angle  : {f(h0,'ROTATOR','OBJCTROT','POSANGLE')}")
    w('')
    w('EQUIPMENT'); w('-'*60)
    w(f"Camera          : {inst or 'n/a'}   (master-folder name: {cam})")
    if f(h0,'BAYERPAT'): w(f"Bayer pattern   : {h0['BAYERPAT']}")
    px=num(f(h0,'XPIXSZ')); fl=num(f(h0,'FOCALLEN'))
    w(f"Sensor          : " + (f"{f(h0,'NAXIS1')} x {f(h0,'NAXIS2')} px, " if f(h0,'NAXIS1') else '') + (f"pixel {px:.2f} µm, " if px else '') + f"bin {f(h0,'XBINNING')}")
    tel=f(h0,'TELESCOP'); w(f"TELESCOP header : {tel or 'n/a'}" + ("  (mount name on ASIAIR)" if tel and ('AM5' in tel or 'iOptron' in tel) else ''))
    w(f"Telescope       : {scope(f(h0,'FOCALLEN'),session)}")
    if fl: w(f"Focal length    : {fl:g} mm" + (f"   Image scale: {206.265*px/fl:.2f} \"/px" if px else ''))
    if f(h0,'APTDIA'): w(f"Aperture        : {h0['APTDIA']} mm")
    if f(h0,'GUIDECAM'): w(f"Guide camera    : {h0['GUIDECAM']}")
    if f(h0,'FOCUSPOS','FOCPOS'): w(f"Focuser pos     : {f(h0,'FOCUSPOS','FOCPOS')}")
    w(f"Capture tool    : {f(h0,'CREATOR','SWCREATE','PROGRAM') or 'n/a'}")
    sites=collections.Counter(place(f(x['h'],'SITELAT'),f(x['h'],'SITELONG')) for x in L)
    for sname,cnt in sites.items(): w(f"Capture site    : {sname or 'not recorded in headers'}" + (f"  ({cnt} frames)" if len(sites)>1 else ''))
    if len(sites)>1: w("  ** headers disagree on site - check **")
    w('')
    w('CAPTURE NIGHTS (night = local evening date)'); w('-'*60)
    nights=gb(L,lambda x:nightof(dateobs(x['h'])))
    for n,v in nights.items():
        ds=[dateobs(x['h']) for x in v if dateobs(x['h'])]
        w(f"  {n}: {len(v)} lights, {min(ds).isoformat()}Z -> {max(ds).isoformat()}Z" if ds else f"  {n}: {len(v)} lights")
    if len(nights)>1: w('  ** MULTI-NIGHT SESSION **')
    w('')
    w('LIGHTS'); w('-'*60)
    tot=0
    for (flt,e,g,o,d),v in gb(L,lambda x:(f(x['h'],'FILTER') or '-',num(f(x['h'],'EXPTIME','EXPOSURE')),f(x['h'],'GAIN'),f(x['h'],'OFFSET'),x['dir'])).items():
        temps=[num(f(x['h'],'CCD-TEMP')) for x in v if num(f(x['h'],'CCD-TEMP')) is not None]
        t=f"{min(temps):.1f}..{max(temps):.1f}C" if temps else '?'
        tot+=(e or 0)*len(v)
        w(f"  filter {flt:5} {len(v):4d} x {fmt(e)}s  gain {g} offset {o} temp {t}  -> {d}/")
    w(f"  Total integration: {tot/3600:.2f} h")
    bad=[x for x in fr if x['bad']]
    if bad: w(f"  (+ {len(bad)} rejected frames in TRASH/BadFrames, not counted)")
    w('')
    w('FLATS / DARK FLATS (stay with this session)'); w('-'*60)
    for k in ('Flat','DarkFlat'):
        for (flt,e,d),v in gb([x for x in fr if x['k']==k],lambda x:(f(x['h'],'FILTER') or '-',num(f(x['h'],'EXPTIME','EXPOSURE')),x['dir'])).items():
            w(f"  {k:8} filter {flt:5} {len(v):3d} x {fmt(e)}s -> {d}/")
    w('')
    w('CALIBRATION - DARKS (paths relative to the Astronomy volume root)'); w('-'*60)
    need=gb(L,lambda x:(num(f(x['h'],'EXPTIME','EXPOSURE')),f(x['h'],'GAIN'),f(x['h'],'OFFSET')))
    sd=min(d for d in (dateobs(x['h']) for x in L) if d).date()
    def pick(kind,e,g,o):
        c=[m for m in idx if m['kind']==kind and m['cam']==cam and str(m['gain'])==str(g) and (e is None or m['exp']==e)]
        lt=[num(f(x['h'],'CCD-TEMP')) for x in L if num(f(x['h'],'CCD-TEMP')) is not None]; lt=sum(lt)/len(lt) if lt else None
        tbad=lambda m: (lt is not None and m.get('temp') is not None and abs(m['temp']-lt)>5)
        c.sort(key=lambda m:(str(m['offset'])!=str(o) if o else False, tbad(m), abs((datetime.date.fromisoformat(m['date'])-sd).days) if m['date'] else 9999))
        for m in c: m['_tbad']=tbad(m)
        return c
    for (e,g,o) in sorted(need,key=lambda k:(k[0] or 0)):
        c=pick('Dark',e,g,o)
        w(f"  For {fmt(e)}s lights (gain {g}, offset {o}):")
        if not c: w("    !! NO MATCHING MASTER DARKS FOUND in 002-MasterDarks" + ("" if cam else " (camera has no master-dark library)"))
        for i,m in enumerate(c[:3]):
            w(f"    {'USE ->' if i==0 else '  alt '} {m['path']}/  ({m['n']} frames, {m['date']}, gain {m['gain']} offset {m['offset']} temp {m['temp']:.1f}C)" + ("  !! TEMPERATURE MISMATCH vs lights" if m.get('_tbad') else ''))
    w('')
    w('CALIBRATION - BIAS'); w('-'*60)
    for (g,o) in {(k[1],k[2]) for k in need}:
        c=pick('Bias',None,g,o)
        if not c: w(f"  gain {g} offset {o}: !! NO MATCHING MASTER BIAS in 001-MasterBias (use dark flats for flats)")
        for i,m in enumerate(c[:3]):
            w(f"  {'USE ->' if i==0 else '  alt '} {m['path']}/  ({m['n']} frames, {m['date']}, gain {m['gain']} offset {m['offset']})")
    w('')
    other=[d for d in sorted(os.listdir(S)) if os.path.isdir(os.path.join(S,d)) and SKIP.match(d)]
    if other: w('OTHER FOLDERS: '+', '.join(other)); w('')
    extra=os.path.join(S,'.project_notes.txt')
    if os.path.exists(extra): w('NOTES'); w('-'*60); out.extend(open(extra).read().rstrip().split('\n')); w('')
    txt='\n'.join(out)+'\n'
    open(os.path.join(S,'PROJECT_INFO.txt'),'w').write(txt)
    print('WROTE',session) if QUIET else print(txt)
QUIET='-q' in sys.argv
if __name__=='__main__':
    idx=build_index() if (len(sys.argv)>1 and sys.argv[1]=='--reindex') else json.load(open(os.path.join(HERE,'calindex.json')))
    for s in sys.argv[1:]:
        if s not in ('--reindex','-q'): main(s.rstrip('/'),idx)
