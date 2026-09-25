# Keep the first 10 frames (by filename order = capture order) of every calibration set; move the rest to
# <calibration folder>/_to_delete/. A "set" = one exposure (+ filter) within a folder.
# Covers COOLED cameras only: 001-MasterBias/**, 002-MasterDarks/**, session flats*/darkflats* folders. DSLR/CR2 sessions are exempt.
# Usage: trim_calibration.py [--apply]   (dry run by default). Resumable; 140 s budget.
import os,re,sys,time,collections
HERE=os.path.dirname(os.path.abspath(__file__)); ROOT=os.path.dirname(os.path.dirname(HERE)); os.chdir(ROOT)
APPLY='--apply' in sys.argv; KEEP=10; t0=time.time()
IMG=re.compile(r'\.(fits?|xisf|cr2)$',re.I)
def key(f):
    u=f
    m=re.search(r'FlatWizard_([A-Za-z]+)_',u) or re.search(r'_(?:183MM|294MC|2600MM|2600MC)_([A-Za-z]+)_gain',u)
    flt=m.group(1) if m else '-'
    e=re.search(r'(?<![\d.])(\d+(?:\.\d+)?)(ms|s)(?=[_\-.])',u); exp=e.group(0) if e else '-'
    kind=re.match(r'(Bias|Dark|Flat|B_|D_|F_|L_)',u,re.I); kind=(kind.group(1) if kind else re.sub(r'[-_].*','',u))[:4].upper()
    return (kind,flt,exp)
def sets(folder,files):
    g=collections.defaultdict(list)
    for f in files: g[key(f)].append(f)
    return g
folders=[]
for base in ('001-MasterBias','002-MasterDarks'):
    for dp,dn,fn in os.walk(base):
        dn[:]=[d for d in dn if d!='_to_delete']
        fs=sorted(f for f in fn if IMG.search(f))
        if fs: folders.append((dp,fs))
for t in sorted(os.listdir('.')):
    if (not t[0].isalpha() or t[0]=='Z') or not os.path.isdir(t): continue
    for s in sorted(os.listdir(t)):
        S=os.path.join(t,s)
        if not os.path.isdir(S): continue
        for d in sorted(os.listdir(S)):
            if re.match(r'^(flats|darkflats)(-.+)?$',d) and os.path.isdir(os.path.join(S,d)):
                fs=sorted(f for f in os.listdir(os.path.join(S,d)) if IMG.search(f))
                if fs: folders.append((os.path.join(S,d),fs))
        # DSLR (CR2, uncooled) calibration is exempt from the cap (Chris, 2026-09-24)
tot_keep=tot_move=0; changed=0
for folder,fs in folders:
    for k,group in sorted(sets(folder,fs).items()):
        group=sorted(group); extra=group[KEEP:]
        tot_keep+=min(len(group),KEEP); tot_move+=len(extra)
        if not extra: continue
        changed+=1
        if not APPLY: print(f"{len(group):4d} -> keep {KEEP}, move {len(extra):3d}  {folder}  {k}")
        else:
            td=os.path.join(folder,'_to_delete'); os.makedirs(td,exist_ok=True)
            for f in extra:
                if time.time()-t0>140: print('PAUSE'); sys.exit()
                if not os.path.exists(os.path.join(td,f)): os.rename(os.path.join(folder,f),os.path.join(td,f))
print(f"{'APPLIED' if APPLY else 'DRY RUN'}: sets changed={changed}, frames kept={tot_keep}, frames to move={tot_move}")
