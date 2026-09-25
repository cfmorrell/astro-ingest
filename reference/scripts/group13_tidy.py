# Group 13 tidy: loose lights -> lights/, exposure folders -> lights-<N>s, NAN per-filter flatten,
# outputs -> stacked/, DSS sidecars/apt_thumbs/LOGS/empty dirs -> _to_delete, finals renamed. Resumable, 140 s budget.
import os,re,sys,time
HERE=os.path.dirname(os.path.abspath(__file__)); ROOT=os.path.dirname(os.path.dirname(HERE)); os.chdir(ROOT)
LOG=open(os.path.join(os.path.dirname(HERE),'plans','group13_tidy.log'),'a'); t0=time.time()
def tick():
    if time.time()-t0>140: print('PAUSE'); sys.exit()
def mv(a,b):
    tick()
    if not os.path.exists(a): return
    if os.path.exists(b): LOG.write(f"SKIP\t{a}\t{b}\n"); return
    os.makedirs(os.path.dirname(b),exist_ok=True); os.rename(a,b); LOG.write(f"OK\t{a}\t{b}\n")
KEEP={'PROJECT_INFO.txt','.project_notes.txt','.flats_are_copies','.DS_Store','desktop.ini','Notes.txt'}
OUT=re.compile(r'(result|stacked_|processed|starless|starmask|autosave|masterlight|^(m|ngc)_\d+_\d+x\d+sec|integration|_abe\.|seestar)',re.I)
SIDE=re.compile(r'\.(info|stackinfo|description)\.txt$',re.I)
for t in sorted(os.listdir('.')):
    if (not t[0].isalpha() or t[0]=='Z') or not os.path.isdir(t): continue
    for s in sorted(os.listdir(t)):
        S=os.path.join(t,s)
        if not os.path.isdir(S): continue
        cr2=any(x.lower().endswith('.cr2') for x in os.listdir(S))
        # exposure-split light folders
        for d in sorted(os.listdir(S)):
            m=re.match(r'^(\d+)\s*[Ss]econds(-(\w+))?$',d)
            if m and os.path.isdir(os.path.join(S,d)):
                P=os.path.join(S,d)
                for x in os.listdir(P):
                    if SIDE.search(x): mv(os.path.join(P,x),os.path.join(S,'_to_delete',d.replace(' ','')+'__'+x))
                mv(P,os.path.join(S,f"lights-{m.group(1)}s"+(f"-{m.group(3)}" if m.group(3) else '')))
        # NAN per-filter folders
        if s.startswith('2024-09-02-NorthAmericaNebula'):
            for f in ('H','O','S','L'):
                F=os.path.join(S,f)
                if not os.path.isdir(F): continue
                for k in ('lights','flats'):
                    mv(os.path.join(F,k),os.path.join(S,f"{k}-{f}"))
                if not [x for x in os.listdir(F) if x not in('.DS_Store',)]: mv(F,os.path.join(S,'_to_delete','emptied_'+f))
        # retire apt_thumbs / LOGS
        for d in ('apt_thumbs','LOGS'):
            if os.path.isdir(os.path.join(S,d)): mv(os.path.join(S,d),os.path.join(S,'_to_delete',d))
        # empty subfolders inside lights
        L=os.path.join(S,'lights')
        if os.path.isdir(L):
            for d in os.listdir(L):
                p=os.path.join(L,d)
                if os.path.isdir(p) and not [x for x in os.listdir(p) if x!='.DS_Store']: mv(p,os.path.join(S,'_to_delete','empty_lights_'+d.replace(' ','')))
        # loose files
        for x in sorted(os.listdir(S)):
            p=os.path.join(S,x)
            if not os.path.isfile(p) or x in KEEP or x.lower().endswith(('.cr2','.xmp')) or re.search(r'Capture Notes\.txt$',x): continue
            if re.match(r'M8-LagoonNebula-082826\.(fits|jpeg)$',x): continue   # finals handled below
            e=x.rsplit('.',1)[-1].lower()
            if SIDE.search(x) or x.startswith('MasterDark'): mv(p,os.path.join(S,'_to_delete',x))
            elif e in('fit','fits','xisf') and not OUT.search(x) and not cr2: mv(p,os.path.join(S,'lights',x))
            elif e in('fit','fits','xisf','tif','tiff','png','jpg','jpeg','psd'): mv(p,os.path.join(S,'stacked',x))
# finals
mv('OmegaNebula-M17/M17-OmegaNebula-082826.fits','OmegaNebula-M17/2026-08-OmegaNebula-2600MC.fits')
mv('OmegaNebula-M17/M17-OmegaNebula-082826.jpeg','000-FinalizedImages/2026-08-OmegaNebula-2600MC.jpeg')
mv('LagoonNebula-M8/2026-08-28-LagoonNebula-2600MC-Z61/M8-LagoonNebula-082826.fits','LagoonNebula-M8/2026-08-LagoonNebula-2600MC.fits')
mv('LagoonNebula-M8/2026-08-28-LagoonNebula-2600MC-Z61/M8-LagoonNebula-082826.jpeg','000-FinalizedImages/2026-08-LagoonNebula-2600MC.jpeg')
print('ALLDONE')
