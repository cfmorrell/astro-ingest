# PROJECT_INFO.txt for Canon 450D (CR2) sessions: frame inventory from EXIF (PIL). Location comes from LOCATIONS below / .project_notes.txt
import os,re,sys,collections,datetime
from PIL import Image,ExifTags
HERE=os.path.dirname(os.path.abspath(__file__)); ROOT=os.environ.get('ASTRO_ROOT') or os.path.dirname(os.path.dirname(HERE)); os.chdir(ROOT)
def kind(f):
    u=f.upper()
    if u.startswith(('B_',)) or 'BIAS' in u or 'OFFSET' in u and u.startswith('B_'): return 'Bias/Offset'
    if u.startswith('D_') or u.startswith('DARK'): return 'Dark'
    if u.startswith('F_') or u.startswith('FLAT'): return 'Flat'
    if u.startswith(('SINGLE','INCOMPLETE')): return 'Test/incomplete'
    return 'Light'
def exif(p):
    try:
        e=Image.open(p).getexif(); x=e.get_ifd(0x8769); T=lambda k:x.get(k) or e.get(k)
        return dict(dt=T(36867) or T(306),exp=float(T(33434) or 0),iso=T(34855),model=e.get(272))
    except Exception as ex: return dict(err=str(ex))
def main(S,location):
    fs=sorted(f for f in os.listdir(S) if f.lower().endswith('.cr2'))
    g=collections.defaultdict(list)
    for f in fs: g[kind(f)].append((f,exif(os.path.join(S,f))))
    out=[];w=out.append
    t,s=S.split('/')[:2]
    w('PROJECT / SESSION INFORMATION  (Canon DSLR / CR2 session)'); w('='*60)
    w(f"Target folder : {t}"); w(f"Session folder: {s}")
    w(f"Generated     : {datetime.date.today().isoformat()} by Claude from CR2 EXIF + filenames"); w('')
    model=next((e.get('model') for v in g.values() for _,e in v if e.get('model')),'?')
    w('EQUIPMENT'); w('-'*60)
    w(f"Camera          : {model} (= Canon EOS 450D)")
    w("Lens/telescope  : not recorded - EXIF focal length 0 means no electronic lens (telescope or manual lens via T-ring)")
    w(f"Capture site    : {location}"); w('')
    L=g.get('Light',[])
    ds=sorted(e['dt'] for _,e in L if e.get('dt'))
    if ds:
        w('CAPTURE NIGHTS'); w('-'*60)
        nights=collections.Counter((datetime.datetime.strptime(d,'%Y:%m:%d %H:%M:%S')-datetime.timedelta(hours=12)).date().isoformat() for d in ds)
        for n,c in sorted(nights.items()): w(f"  {n}: {c} lights")
        w(f"  first {ds[0]}  last {ds[-1]} (camera clock)"); w('')
    w('FRAMES (all calibration stays in this session - CR2 rule)'); w('-'*60)
    tot=0
    for k in ('Light','Dark','Flat','Bias/Offset','Test/incomplete'):
        if k not in g: continue
        c=collections.Counter((e.get('iso'),e.get('exp')) for _,e in g[k])
        for (iso,exp),n in sorted(c.items(),key=lambda x:str(x)):
            w(f"  {k:16} {n:4d} x {exp:g}s  ISO {iso}" if exp is not None else f"  {k:16} {n:4d}")
            if k=='Light': tot+=n*(exp or 0)
    w(f"  Total light integration: {tot/3600:.2f} h"); w('')
    for extra in ('stacked','_to_delete'):
        if os.path.isdir(os.path.join(S,extra)): w(f"OTHER FOLDERS: {extra}/")
    notes=os.path.join(S,'.project_notes.txt')
    if os.path.exists(notes): w(''); w('NOTES'); w('-'*60); out.extend(open(notes).read().rstrip().split('\n'))
    open(os.path.join(S,'PROJECT_INFO.txt'),'w').write('\n'.join(out)+'\n'); print('WROTE',S)
if __name__=='__main__': main(sys.argv[1],sys.argv[2])
